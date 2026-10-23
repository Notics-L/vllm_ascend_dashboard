"""
数据采集器 - 从 GitHub Actions API 拉取工作流运行数据
"""
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
        self, workflow_config: ReleaseWorkflow, days_back: int = 30, max_runs: int = 100
    ) -> tuple[int, ReleaseAnalysisTask]:
        """同步指定 workflow 的构建记录到数据库"""
        task = ReleaseAnalysisTask(
            workflow_id=workflow_config.id,
            task_type="sync_runs",
            status="running",
            progress=0,
            total_items=max_runs,
            started_at=datetime.now(UTC),
        )
        self.db.add(task)
        await self.db.flush()

        collected = 0
        try:
            owner = workflow_config.repo_owner
            repo = workflow_config.repo_name

            runs = await self.github.get_workflow_runs(
                workflow_id_or_name=workflow_config.workflow_path,
                days_back=days_back,
                per_page=max_runs,
            )

            for idx, run in enumerate(runs):
                existing = (
                    await self.db.execute(
                        select(ReleaseBuildRun).where(
                            ReleaseBuildRun.workflow_id == workflow_config.id,
                            ReleaseBuildRun.github_run_id == run["id"],
                        )
                    )
                ).scalar_one_or_none()

                if existing:
                    task.processed_items = idx + 1
                    task.progress = int((idx + 1) / max(len(runs), 1) * 100)
                    await self.db.flush()
                    continue

                jobs = await self.github.get_job_list(run["id"])
                await self._persist_run_data(workflow_config, run, jobs)
                collected += 1

                task.processed_items = idx + 1
                task.progress = int((idx + 1) / max(len(runs), 1) * 100)
                await self.db.flush()

            task.status = "completed"
            task.completed_at = datetime.now(UTC)
            workflow_config.last_sync_at = datetime.now(UTC)

        except GitHubAuthenticationError:
            task.status = "failed"
            task.error_message = "GitHub authentication failed"
            task.completed_at = datetime.now(UTC)
            raise
        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.now(UTC)
            logger.error(f"Sync failed for workflow {workflow_config.id}: {e}")
        finally:
            await self.db.commit()

        return collected, task

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
            status=run.get("conclusion", run.get("status", "")),
            event=run.get("event", ""),
            branch=run.get("head_branch", "") or run.get("head_tag", ""),
            commit_sha=run.get("head_sha", ""),
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
                job.get("name", ""), workflow_config.workflow_type
            )
            job_duration = calc_duration(
                job.get("started_at"), job.get("completed_at")
            )

            steps_data = []
            for step in job.get("steps", []):
                step_dur = calc_duration(step.get("started_at"), step.get("completed_at"))
                steps_data.append({
                    "name": step.get("name", ""),
                    "number": step.get("number", 0),
                    "status": step.get("conclusion", step.get("status", "")),
                    "duration": step_dur,
                })

            group = ReleaseBuildGroup(
                run_id=run_record.id,
                group_name=parsed["group_key"],
                target=parsed.get("target", ""),
                os_name=parsed.get("os_name", ""),
                phase=parsed.get("phase", ""),
                status=job.get("conclusion", job.get("status", "")),
                total_duration=job_duration,
                runner_name=job.get("runner_name", ""),
                architecture=parsed.get("architecture", ""),
                sub_job_name=parsed.get("sub_name", ""),
                steps_json=json.dumps(steps_data),
            )
            self.db.add(group)

    @staticmethod
    def _build_conclusion_summary(jobs: list[dict]) -> dict:
        summary = {"total": len(jobs), "success": 0, "failure": 0, "skipped": 0, "other": 0}
        for job in jobs:
            c = job.get("conclusion", job.get("status", "other")).lower()
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
