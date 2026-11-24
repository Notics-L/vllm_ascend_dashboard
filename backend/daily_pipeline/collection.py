"""Collect Daily executions and expose their persisted results to the dashboard."""
import logging
import time
from datetime import UTC, datetime, timedelta

from daily_pipeline.collection_source import CollectionSourceError, DailyCollectionSource
from daily_pipeline.github_links import github_run_url
from daily_pipeline.merged_pr import DailyMergedPRService
from infrastructure.clients.daily_pipeline_collection import DailyCollectionClient
from infrastructure.clients.daily_pipeline_scheduler import start_collection
from infrastructure.core.security import decrypt_api_key
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository
from infrastructure.persistence.daily_pipeline_settings import read_settings

logger = logging.getLogger(__name__)
ISSUE_REPORT_GRACE = timedelta(minutes=5)
RESULT_LABELS = {
    "queued": "排队中", "running": "执行中", "success": "成功",
    "failure": "失败", "cancelled": "已取消", "skipped": "已跳过",
}


def timestamp(value):
    return value.replace(tzinfo=UTC).isoformat() if value is not None else None


def node_github_url(row, repository, nodes_by_id):
    if row.node_type == "workflow_run":
        return github_run_url(repository, row.source_id)

    source_run_id = row.source_run_id
    # Older rows can lack the stored source Run ID. Use the nearest workflow ancestor.
    parent = nodes_by_id.get(row.parent_id) if source_run_id is None else None
    while parent is not None:
        if parent.node_type == "workflow_run" and parent.source_id:
            source_run_id = parent.source_id
            break
        parent = nodes_by_id.get(parent.parent_id)

    job_id = row.source_id if row.node_type in ("job", "test_job") else None
    return github_run_url(repository, source_run_id or row.run_id, job_id)


def run_view(row, history_id, history_repository):
    issue = None
    if row.issue_number is not None:
        issue = {"number": row.issue_number, "state": row.issue_state}
    return {
        "id": str(row.run_id), "runId": row.run_id,
        "githubUrl": github_run_url(row.repository or history_repository, row.run_id),
        "startedAt": timestamp(row.started_at), "endedAt": timestamp(row.finished_at),
        "lastCollectedAt": timestamp(row.last_collected_at),
        "triggerHistoryId": str(history_id) if history_id is not None else None,
        "result": RESULT_LABELS.get(row.result, "未获取"), "issue": issue,
    }


def node_view(row, github_url):
    return {
        "id": str(row.id), "parentId": str(row.parent_id) if row.parent_id is not None else None,
        "nodeType": row.node_type, "sourceId": str(row.source_id) if row.source_id else None,
        "githubUrl": github_url,
        "name": row.name, "result": RESULT_LABELS.get(row.result, "未获取"),
        "group": row.case_type, "hardware": row.hardware, "vllmVersion": row.vllm_version,
    }


class DailyCollectionService:
    def __init__(self, repository: DailyCollectionRepository):
        self.repository = repository

    async def list_runs(self):
        rows = await self.repository.list_roots()
        histories = await self.repository.history_links({row.run_id for row in rows})
        return [run_view(row, *histories.get(row.run_id, (None, None))) for row in rows]

    async def detail(self, run_id: int):
        tree = await self.repository.get_tree(run_id)
        if tree is None:
            raise LookupError("采集记录不存在")
        root = tree["run"]
        history = tree["history"]
        history_repository = history.repository if history else None
        repository = root.repository or history_repository
        nodes = tree["nodes"]
        nodes_by_id = {row.id: row for row in nodes}
        github_urls = {row.id: node_github_url(row, repository, nodes_by_id) for row in nodes}
        return {
            **run_view(root, history.id if history else None, history_repository),
            "nodes": [node_view(row, github_urls[row.id]) for row in nodes],
            "jobs": [
                {"id": str(row.id), "name": row.name, "githubUrl": github_urls[row.id],
                 "result": RESULT_LABELS.get(row.result, "未获取"),
                 "group": "run-all-tests" if row.name.startswith("run-all-tests") else "other"}
                for row in nodes if row.parent_id is None and row.node_type != "test_job"
            ],
            "cases": [
                {"id": str(row.id), "jobId": str(row.source_id), "name": row.name,
                 "githubUrl": github_urls[row.id], "group": row.case_type, "hardware": row.hardware or "—",
                 **({"vllmVersion": row.vllm_version} if row.vllm_version else {}),
                 "result": RESULT_LABELS.get(row.result, "未获取")}
                for row in nodes if row.node_type == "test_job"
            ],
        }

    async def issue_detail(self, number: int):
        history = await self.repository.issue_history(number)
        if history is None:
            raise LookupError("关联 Issue 不存在")
        repository = history.repository
        config = await read_settings(self.repository.db, "github_token")
        token = decrypt_api_key(config.get("github_token") or "")
        if not token:
            raise CollectionSourceError("请先配置 GitHub Token")
        await self.repository.db.rollback()
        async with DailyCollectionClient(repository, token) as client:
            detail = await client.issue_detail(number)
        await self.repository.update_issue_state(number, detail["state"])
        return detail

    async def recollect(self, run_id: int):
        if await self.repository.get_root(run_id) is None:
            raise LookupError("采集记录不存在")
        await self.repository.db.rollback()
        await start_collection(run_id)
        return {"status": "等待采集"}

    async def delete(self, run_ids: set[int]):
        deleted = await self.repository.delete_roots(run_ids)
        logger.info("Daily collection records deleted requested_count=%d deleted_count=%d", len(run_ids), deleted)
        return {"deleted": deleted}

    async def collect(self, run_id: int) -> bool:
        started = time.perf_counter()
        async with self.repository.collection_lease(run_id) as acquired:
            if not acquired:
                logger.info("Daily collection already active run_id=%d", run_id)
                return False
            tree = await self.repository.get_tree(run_id)
            if tree is None:
                return True
            row = tree["history"]
            if row is None:
                raise CollectionSourceError("采集记录缺少关联的触发历史")
            history = {key: getattr(row, key) for key in (
                "id", "repository", "source_branch", "workflow", "run_id", "daily_cases", "triggered_at",
            )}
            root = tree["run"]
            known_runs = [{
                "run_id": root.run_id, "parent_run_id": None, "workflow": root.workflow,
                "issue_number": root.issue_number, "issue_state": root.issue_state,
            }] + [
                {"run_id": node.source_id, "parent_run_id": run_id, "workflow": node.name}
                for node in tree["nodes"] if node.node_type == "workflow_run"
            ]
            config = await read_settings(self.repository.db, "github_token")
            token = decrypt_api_key(config.get("github_token") or "")
            if not token:
                raise CollectionSourceError("请先配置 GitHub Token")
            # Network requests do not hold the session's read transaction open.
            # The independent collection lease remains held until this round ends.
            await self.repository.db.rollback()
            merged_prs = DailyMergedPRService(self.repository.db)
            merged_pr_window_ready = True
            try:
                await merged_prs.collect_window(history, token)
            except Exception:
                await self.repository.db.rollback()
                logger.exception("Daily merged PR collection failed; will retry run_id=%d", run_id)
                merged_pr_window_ready = False

            logger.info("Daily collection started run_id=%d repository=%s", run_id, history["repository"])
            try:
                source = DailyCollectionSource(history["repository"], token)
                snapshot = await source.collect_snapshot(history, known_runs)
                applied = await self.repository.apply_snapshot(run_id, snapshot)
            except Exception:
                await self.repository.db.rollback()
                logger.exception("Daily collection failed run_id=%d duration_seconds=%.3f", run_id, time.perf_counter() - started)
                raise
            logger.info(
                "Daily collection finished run_id=%d result=success complete=%s saved=%s nodes=%d duration_seconds=%.3f",
                run_id, snapshot["complete"], applied, len(snapshot["nodes"]), time.perf_counter() - started,
            )
            if not applied:
                return True
            if not merged_pr_window_ready:
                return False
            complete = snapshot["complete"]
            finished_at = snapshot["run"]["finished_at"]
            awaiting_issue = not complete or (
                not snapshot["run"].get("issue_number")
                and finished_at is not None
                and datetime.now(UTC).replace(tzinfo=None) < finished_at + ISSUE_REPORT_GRACE
                and any(node["node_type"] == "test_job" and node["result"] == "failure" for node in snapshot["nodes"])
            )
            merged_prs_done = await merged_prs.publish_linked_issue(
                history, token, awaiting_issue=awaiting_issue, verify_existing=complete,
            )
            return complete and merged_prs_done
