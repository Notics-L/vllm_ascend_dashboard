from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import DailyPipelineTriggerHistory as History


class DailyTriggerHistoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_history(self, page: int, page_size: int):
        visible = History.is_deleted.is_(False)
        total = await self.db.scalar(select(func.count()).select_from(History).where(visible))
        query = (
            select(History.id, History.triggered_at, History.event, History.result)
            .where(visible)
            .order_by(History.triggered_at.desc(), History.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.db.execute(query)).all()
        return rows, total

    async def get_history(self, history_id: int):
        return await self.db.get(History, history_id)

    async def delete_history(self, ids: set[int]) -> int:
        result = await self.db.execute(
            update(History).where(History.id.in_(ids), History.is_deleted.is_(False))
            .values(is_deleted=True),
        )
        await self.db.commit()
        return result.rowcount

    async def add_history(self, *, commit: bool = True, **values):
        row = History(**values)
        self.db.add(row)
        if commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return row
