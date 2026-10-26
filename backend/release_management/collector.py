"""
数据采集器 - 从 GitHub Actions API 拉取工作流运行数据
"""
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import (
    GitHubAuthenticationError,
    GitHubClient,
)
from infrastructure.persistence.models import (
    ReleaseAnalysisTask,
    ReleaseBuildGroup,
    ReleaseBuildRun,
    ReleaseWorkflow,
)
from release_management.parser import JobNameParser

logger = logging.getLogger(__name__)


def calc_duration(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0, (e - s).total_seconds())
    except Exception:
        return 0.0


class ReleaseWorkflowCollector:
    """发布件工作流采集器"""

    def __init__(self, db: AsyncSession, github_client: GitHubClient):
        self.db = db
        self.github = github_client

    async def discover_workflows(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """列出仓库所有 workflow"""
        try:
            workflows = await self.github.list_workflows(owner, repo, per_page=100)
        except Exception as e:
            logger.error(f"Failed to discover workflows for {owner}/{repo}: {e}")
            workflows = {"workflows": []}
        return workflows.get("workflows", [])

    async def register_workflows(
        self, owner: str, repo: str, workflows: list[dict[str, Any]]
    ) -> int:
        """将 workflow 注册到数据库"""
        registered = 0
        for wf in workflows:
            existing = (
                await self.db.execute(
                    select(ReleaseWorkflow).where(
                        ReleaseWorkflow.repo_owner == owner,
                        ReleaseWorkflow.repo_name == repo,
                        ReleaseWorkflow.workflow_path == wf.get("path", ""),
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.workflow_id = wf.get("id", 0)
                existing.workflow_name = wf.get("name", "")
                existing.updated_at = datetime.now(UTC)
            else:
                wf_type = self._detect_workflow_type(wf.get("name", ""))
                new_wf = ReleaseWorkflow(
                    repo_owner=owner,
                    repo_name=repo,
                    workflow_id=wf.get("id", 0),
                    workflow_name=wf.get("name", ""),
                    workflow_path=wf.get("path", ""),
                    workflow_type=wf_type,
                )
                self.db.add(new_wf)
                registered += 1

        await self.db.commit()
        return registered

    async def sync_workflow_runs(
        self, workflow_config: ReleaseWorkflow, api_task_id: int,
        days_back: int = 30, max_runs: int = 100
    ) -> int:
        """同步指定 workflow 的构建记录到数据库
        
        Args:
            api_task_id: API 层创建的 ReleaseAnalysisTask 的 ID，用于更新进度
        Returns:
            新采集的记录数量
        """
        from sqlalchemy import update as sa_update, text
        
        collected = 0
        try:
            runs = await self.github.get_workflow_runs(
                workflow_id_or_name=str(workflow_config.workflow_id),
                days_back=days_back,
                per_page=max_runs,
            )

            total_runs = max(len(runs), 1)
            for idx, run in enumerate(runs):
                # 检查是否被取消
                if self._is_task_cancelled(api_task_id):
                    logger.info(f"Sync task {api_task_id} cancelled during run loop")
                    break

                try:
                    existing = (
                        await self.db.execute(
                            select(ReleaseBuildRun).where(
                                ReleaseBuildRun.workflow_id == workflow_config.id,
                                ReleaseBuildRun.github_run_id == run["id"],
                            )
                        )
                    ).scalar_one_or_none()

                    jobs = await self.github.get_job_list(run["id"])
                    # Rate limit: avoid hammering GitHub API (1 req/s)
                    await asyncio.sleep(0.5)

                    if existing:
                        from sqlalchemy import delete as sa_delete
                        await self.db.execute(
                            sa_delete(ReleaseBuildGroup).where(ReleaseBuildGroup.run_id == existing.id)
                        )
                        await self._update_run_record(existing, workflow_config, run, jobs)
                        collected += 1
                        self._update_api_task(api_task_id, idx + 1, total_runs, f"已更新 {collected} 条记录...")
                    else:
                        await self._persist_run_data(workflow_config, run, jobs)
                        collected += 1
                        self._update_api_task(api_task_id, idx + 1, total_runs, f"已采集 {collected} 条新记录...")

                    # Each run commits its own changes to avoid lock wait timeouts
                    await self.db.commit()
                except Exception as e:
                    logger.error(f"Error processing run {run.get('id')}: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    continue

            # Only update last_sync_at if not cancelled; no need to commit since each run committed above
            if not self._is_task_cancelled(api_task_id):
                workflow_config.last_sync_at = datetime.now(UTC)
                try:
                    await self.db.commit()
                except Exception:
                    pass
            return collected

        except GitHubAuthenticationError:
            await self._mark_api_task_failed(api_task_id, "GitHub authentication failed")
            raise
        except Exception as e:
            logger.error(f"Sync failed for workflow {workflow_config.id}: {e}")
            await self._mark_api_task_failed(api_task_id, str(e))
            raise

    def _is_task_cancelled(self, task_id: int) -> bool:
        """检查任务是否已被取消"""
        try:
            result = self.db.execute(
                select(ReleaseAnalysisTask.status).where(ReleaseAnalysisTask.id == task_id)
            )
            status = result.scalar_one_or_none()
            return status == "cancelled"
        except Exception:
            return False

    def _update_api_task(self, task_id: int, processed: int, total: int, message: str):
        """使用 flush 更新 API 层任务进度（不 commit，由调用方统一 commit）"""
        from sqlalchemy import update as sa_update
        progress = int(processed / max(total, 1) * 100)
        try:
            self.db.execute(
                sa_update(ReleaseAnalysisTask)
                .where(ReleaseAnalysisTask.id == task_id)
                .values(
                    processed_items=processed,
                    progress=progress,
                    progress_message=message,
                )
            )
            self.db.flush()
        except Exception as e:
            logger.warning(f"Failed to update api task {task_id}: {e}")

    async def _mark_api_task_failed(self, task_id: int, error: str):
        """标记 API 层任务为失败"""
        from sqlalchemy import update as sa_update
        try:
            self.db.execute(
                sa_update(ReleaseAnalysisTask)
                .where(ReleaseAnalysisTask.id == task_id)
                .values(
                    status="failed",
                    error_message=error,
                    progress_message=f"同步失败: {error[:200]}",
                    completed_at=datetime.now(UTC),
                )
            )
            await self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to mark api task {task_id} as failed: {e}")

    async def _update_run_record(
        self, run_record: ReleaseBuildRun, workflow_config: ReleaseWorkflow, run: dict[str, Any], jobs: list[dict]
    ):
        """更新已有的 run 记录"""
        created = run.get("created_at", "")
        started = run.get("run_started_at", created) or created
        updated = run.get("updated_at", "")

        run_record.status = run.get("conclusion") or run.get("status") or ""
        run_record.event = run.get("event") or ""
        run_record.branch = run.get("head_branch") or run.get("head_tag") or ""
        run_record.commit_sha = run.get("head_sha") or ""
        run_record.created_at_remote = _to_dt(created)
        run_record.started_at_remote = _to_dt(started)
        run_record.completed_at_remote = _to_dt(updated)
        run_record.wait_duration = calc_duration(created, started)
        run_record.exec_duration = calc_duration(started, updated)
        run_record.wall_duration = calc_duration(created, updated)
        run_record.num_jobs = len(jobs)
        run_record.conclusion_summary = json.dumps(self._build_conclusion_summary(jobs))

        await self.db.flush()

        for job in jobs:
            parsed = JobNameParser.parse(
                job.get("name") or "", workflow_config.workflow_type
            )
            job_duration = calc_duration(
                job.get("started_at"), job.get("completed_at")
            )

            steps_data = []
            for step in job.get("steps", []):
                step_dur = calc_duration(step.get("started_at"), step.get("completed_at"))
                steps_data.append({
                    "name": step.get("name") or "",
                    "number": step.get("number", 0),
                    "status": step.get("conclusion") or step.get("status") or "",
                    "duration": step_dur,
                })

            group = ReleaseBuildGroup(
                run_id=run_record.id,
                group_name=parsed["group_key"],
                target=parsed.get("target", ""),
                os_name=parsed.get("os_name", ""),
                phase=parsed.get("phase", ""),
                status=job.get("conclusion") or job.get("status") or "",
                total_duration=job_duration,
                runner_name=job.get("runner_name") or "",
                architecture=parsed.get("architecture", ""),
                sub_job_name=parsed.get("sub_name", ""),
                steps_json=json.dumps(steps_data),
            )
            self.db.add(group)

    async def _persist_run_data(
        self, workflow_config: ReleaseWorkflow, run: dict[str, Any], jobs: list[dict]
    ):
        """持久化单次运行的数据"""
        created = run.get("created_at", "")
        started = run.get("run_started_at", created) or created
        updated = run.get("updated_at", "")

        run_record = ReleaseBuildRun(
            workflow_id=workflow_config.id,
            github_run_id=run["id"],
            status=run.get("conclusion") or run.get("status") or "",
            event=run.get("event") or "",
            branch=run.get("head_branch") or run.get("head_tag") or "",
            commit_sha=run.get("head_sha") or "",
            created_at_remote=_to_dt(created),
            started_at_remote=_to_dt(started),
            completed_at_remote=_to_dt(updated),
            wait_duration=calc_duration(created, started),
            exec_duration=calc_duration(started, updated),
            wall_duration=calc_duration(created, updated),
            num_jobs=len(jobs),
            conclusion_summary=json.dumps(self._build_conclusion_summary(jobs)),
        )
        self.db.add(run_record)
        await self.db.flush()

        for job in jobs:
            parsed = JobNameParser.parse(
                job.get("name") or "", workflow_config.workflow_type
            )
            job_duration = calc_duration(
                job.get("started_at"), job.get("completed_at")
            )

            steps_data = []
            for step in job.get("steps", []):
                step_dur = calc_duration(step.get("started_at"), step.get("completed_at"))
                steps_data.append({
                    "name": step.get("name") or "",
                    "number": step.get("number", 0),
                    "status": step.get("conclusion") or step.get("status") or "",
                    "duration": step_dur,
                })

            group = ReleaseBuildGroup(
                run_id=run_record.id,
                group_name=parsed["group_key"],
                target=parsed.get("target", ""),
                os_name=parsed.get("os_name", ""),
                phase=parsed.get("phase", ""),
                status=job.get("conclusion") or job.get("status") or "",
                total_duration=job_duration,
                runner_name=job.get("runner_name") or "",
                architecture=parsed.get("architecture", ""),
                sub_job_name=parsed.get("sub_name", ""),
                steps_json=json.dumps(steps_data),
            )
            self.db.add(group)

    @staticmethod
    def _build_conclusion_summary(jobs: list[dict]) -> dict:
        summary = {"total": len(jobs), "success": 0, "failure": 0, "skipped": 0, "other": 0}
        for job in jobs:
            c = (job.get("conclusion") or job.get("status") or "other").lower()
            if c == "success":
                summary["success"] += 1
            elif c == "failure":
                summary["failure"] += 1
            elif c == "skipped":
                summary["skipped"] += 1
            else:
                summary["other"] += 1
        return summary

    @staticmethod
    def _detect_workflow_type(name: str) -> str:
        name_lower = name.lower()
        if "nightly" in name_lower and "base" in name_lower:
            return "nightly_with_base"
        if "image" in name_lower and ("build" in name_lower or "push" in name_lower):
            return "image_build"
        if "nightly" in name_lower and "image" in name_lower:
            return "nightly_with_base"
        return "image_build"


def _to_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
