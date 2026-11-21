"""Read immutable snapshots of completed Daily dispatch attempts."""
import logging
from datetime import UTC

from infrastructure.persistence.daily_pipeline_history import DailyTriggerHistoryRepository

logger = logging.getLogger(__name__)


def history_item(row):
    return {
        "id": str(row.id),
        "triggeredAt": row.triggered_at.replace(tzinfo=UTC).isoformat(),
        "event": row.event,
        "result": row.result,
    }


def history_detail(row):
    return {
        **history_item(row),
        "repository": row.repository,
        "sourceBranch": row.source_branch,
        "workflow": row.workflow,
        "cron": row.cron_expression,
        "runId": row.run_id,
        "dailyCases": row.daily_cases,
        "logs": row.logs,
    }


class DailyTriggerHistoryService:
    def __init__(self, repository: DailyTriggerHistoryRepository):
        self.repository = repository

    async def list_history(self, page: int, page_size: int):
        rows, total = await self.repository.list_history(page, page_size)
        return {"items": [history_item(row) for row in rows], "total": total}

    async def get_history(self, history_id: int):
        row = await self.repository.get_history(history_id)
        if row is None:
            raise LookupError("触发记录不存在")
        return history_detail(row)

    async def delete_history(self, ids: set[int]):
        try:
            deleted = await self.repository.delete_history(ids)
        except Exception:
            logger.exception("Daily trigger history deletion failed: requested_count=%d", len(ids))
            raise
        logger.info("Daily trigger history deleted: requested_count=%d deleted_count=%d", len(ids), deleted)
        return {"deleted": deleted}
