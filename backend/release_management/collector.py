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
from release_management.third_party_service import ThirdPartyService

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
        days_back: int = 30, max_runs: int = 100,
        sync_mode: str = "incremental", since_date: str | None = None,
        until_date: str | None = None
    ) -> int:
        """同步指定 workflow 的构建记录到数据库
        
        sync_mode: 'full' | 'incremental' | 'range'
        since_date: YYYY-MM-DD (for incremental: start from this date; for range: start)
        until_date: YYYY-MM-DD (for range only: end date)
        
        Args:
            api_task_id: API 层创建的 ReleaseAnalysisTask 的 ID，用于更新进度
        Returns:
            新采集的记录数量
        """
        from sqlalchemy import update as sa_update, text
        
        collected = 0
        new_runs_for_tp: list[tuple[int, list[dict]]] = []
        
        # Determine sync window based on mode
        since_dt: datetime | None = None
        if sync_mode == 'full':
            since_str: str | None = None  # no date filter — full sync
        elif sync_mode == 'range' and since_date:
            since_str = since_date
        elif sync_mode == 'incremental' and since_date:
            since_str = since_date
        else:
            # Fallback: incremental from last_sync_at
            since_dt = workflow_config.last_sync_at
            if since_dt:
                since_str = since_dt.strftime('%Y-%m-%d')
            else:
                since_str = (datetime.now(UTC) - timedelta(days=days_back)).strftime('%Y-%m-%d')

        try:
            # Build GitHub API time filter: >=start,<=end
            if sync_mode == 'range' and until_date:
                created_filter = f">={since_str},<={until_date}" if since_str else f"<={until_date}"
            elif since_str:
                created_filter = f">={since_str}"
            else:
                created_filter = None  # full sync, no time filter
            runs = await self.github.get_workflow_runs(
                workflow_id_or_name=str(workflow_config.workflow_id),
                per_page=max_runs,
                created=created_filter,
            )

            # Filter out runs already synced (incremental)
            if since_dt:
                # Ensure since_dt is timezone-aware for comparison
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=UTC)
                filtered_runs = []
                for run in runs:
                    run_created = _to_dt(run.get("created_at", ""))
                    if run_created and run_created > since_dt:
                        filtered_runs.append(run)
                    elif not filtered_runs:
                        # Always keep the newest run if nothing is truly new
                        filtered_runs.append(run)
                runs = filtered_runs

            total_runs = max(len(runs), 1)
            for idx, run in enumerate(runs):
                # Check cancelled
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

                    run_record_id = None
                    if existing:
                        from sqlalchemy import delete as sa_delete
                        await self.db.execute(
                            sa_delete(ReleaseBuildGroup).where(ReleaseBuildGroup.run_id == existing.id)
                        )
                        await self._update_run_record(existing, workflow_config, run, jobs)
                        run_record_id = existing.id
                        collected += 1
                        self._update_api_task(api_task_id, idx + 1, total_runs, f"已更新 {collected} 条记录...")
                    else:
                        run_record_id = await self._persist_run_data(workflow_config, run, jobs)
                        new_runs_for_tp.append((run_record_id, jobs))
                        collected += 1
                        self._update_api_task(api_task_id, idx + 1, total_runs, f"已采集 {collected} 条新记录...")

                    # Flush progress immediately so poll sees it
                    await self.db.flush()
                    await self.db.commit()
                except Exception as e:
                    logger.error(f"Error processing run {run.get('id')}: {e}")
                    try:
                        await self.db.rollback()
                    except Exception:
                        pass
                    continue

            # Update last_sync_at before returning (so next sync is incremental)
            if not self._is_task_cancelled(api_task_id):
                workflow_config.last_sync_at = datetime.now(UTC)
                try:
                    await self.db.commit()
                except Exception:
                    pass

            # Fire-and-forget third-party detection (don't block sync response)
            if new_runs_for_tp:
                latest_run_id, latest_jobs = new_runs_for_tp[0]
                asyncio.create_task(self._detect_third_party_bg(latest_run_id, latest_jobs))

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
    ) -> int:
        """持久化单次运行的数据，返回 run_record.id"""
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

        return run_record.id

    async def _detect_third_party(self, run_record_id: int, jobs: list[dict]):
        """从单个 build job 的日志中检测三方件版本（只拉 1 次 API）"""
        if not jobs:
            return

        keywords = ["build", "install", "compile", "dependency"]
        scored_jobs: list[tuple[int, dict]] = []
        for job in jobs:
            name_lower = (job.get("name") or "").lower()
            score = sum(1 for kw in keywords if kw in name_lower)
            if score > 0:
                scored_jobs.append((score, job))
        scored_jobs.sort(key=lambda x: -x[0])

        target = scored_jobs[0][1] if scored_jobs else jobs[0]
        job_id = target.get("id")
        job_name = target.get("name") or ""

        if not job_id:
            return

        tp_service = ThirdPartyService(self.db)
        try:
            log_content = await self.github.get_job_logs(job_id)
            if log_content:
                detected = await tp_service.detect_from_logs(run_record_id, log_content)
                if detected:
                    logger.info(
                        f"Detected {len(detected)} third-party components "
                        f"from run {run_record_id} job [{job_name}]"
                    )
        except Exception as e:
            logger.warning(f"Failed to detect third-party from job {job_name}: {e}")

    async def _detect_third_party_bg(self, run_record_id: int, jobs: list[dict]):
        """后台执行三方件检测，不阻塞主流程"""
        try:
            await self._detect_third_party(run_record_id, jobs)
        except Exception as e:
            logger.warning(f"Background third-party detection failed: {e}")

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
