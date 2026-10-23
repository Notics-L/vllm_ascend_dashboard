"""
发布件管理 - 核心业务服务
"""
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import GitHubClient
from infrastructure.persistence.models import (
    ReleaseWorkflow,
)
from release_management.analyzer import ReleaseAnalyzer
from release_management.collector import ReleaseWorkflowCollector

logger = logging.getLogger(__name__)


class ReleaseManagementService:
    """发布件管理核心服务"""

    def __init__(self, db: AsyncSession, github_client: GitHubClient):
        self.db = db
        self.github = github_client
        self.collector = ReleaseWorkflowCollector(db, github_client)
        self.analyzer = ReleaseAnalyzer(db)

    # --- Workflow management ---

    async def list_workflows(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        stmt = select(ReleaseWorkflow)
        if enabled_only:
            stmt = stmt.where(ReleaseWorkflow.enabled)
        stmt = stmt.order_by(ReleaseWorkflow.created_at.desc())
        result = await self.db.execute(stmt)
        workflows = result.scalars().all()
        return [self._workflow_to_dict(w) for w in workflows]

    async def discover_and_register(self, owner: str, repo: str) -> dict[str, Any]:
        raw_workflows = await self.collector.discover_workflows(owner, repo)
        registered = await self.collector.register_workflows(owner, repo, raw_workflows)
        # 返回注册后从数据库读取的完整 workflow 列表（含 enabled, last_sync_at 等字段）
        refreshed = await self.list_workflows(enabled_only=False)
        return {
            "total_found": len(raw_workflows),
            "newly_registered": registered,
            "total_registered": len(refreshed),
            "workflows": refreshed,
        }

    async def toggle_workflow(self, workflow_id: int, enabled: bool) -> dict[str, Any]:
        wf = await self.db.get(ReleaseWorkflow, workflow_id)
        if not wf:
            return {"error": "工作流不存在"}
        wf.enabled = enabled
        await self.db.commit()
        return self._workflow_to_dict(wf)

    # --- Sync ---

    async def trigger_sync(
        self, workflow_id: int, days_back: int = 30, max_runs: int = 100
    ) -> dict[str, Any]:
        wf = await self.db.get(ReleaseWorkflow, workflow_id)
        if not wf:
            return {"error": "工作流不存在"}
        collected, task = await self.collector.sync_workflow_runs(
            wf, days_back=days_back, max_runs=max_runs
        )
        return {
            "collected": collected,
            "task_id": task.id,
            "status": task.status,
        }

    # --- Overview & data ---

    async def get_overview(
        self, workflow_ids: list[int] | None = None, days_back: int = 30
    ) -> dict[str, Any]:
        if not workflow_ids:
            wf_result = await self.db.execute(
                select(ReleaseWorkflow.id).where(ReleaseWorkflow.enabled)
            )
            workflow_ids = [r[0] for r in wf_result.all()]

        if not workflow_ids:
            return self.analyzer._empty_overview()

        return await self.analyzer.get_overview(workflow_ids, days_back=days_back)

    async def get_runs(self, **kwargs) -> dict[str, Any]:
        workflow_ids = kwargs.pop("workflow_ids", None)
        if not workflow_ids:
            wf_result = await self.db.execute(
                select(ReleaseWorkflow.id).where(ReleaseWorkflow.enabled)
            )
            workflow_ids = [r[0] for r in wf_result.all()]
        return await self.analyzer.get_runs(workflow_ids, **kwargs)

    async def get_run_detail(self, run_id: int) -> dict[str, Any] | None:
        return await self.analyzer.get_run_detail(run_id)

    @staticmethod
    def _workflow_to_dict(wf: ReleaseWorkflow) -> dict[str, Any]:
        return {
            "id": wf.id,
            "repo_owner": wf.repo_owner,
            "repo_name": wf.repo_name,
            "workflow_id": wf.workflow_id,
            "workflow_name": wf.workflow_name,
            "workflow_path": wf.workflow_path,
            "workflow_type": wf.workflow_type,
            "enabled": wf.enabled,
            "last_sync_at": wf.last_sync_at.isoformat() if wf.last_sync_at else None,
            "created_at": wf.created_at.isoformat() if wf.created_at else None,
        }
