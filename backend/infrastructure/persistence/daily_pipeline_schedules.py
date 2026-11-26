from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import DailyPipelineScheduledTask as Task


async def read_schedule(db: AsyncSession, task_key: str) -> tuple[str, str] | None:
    query = select(Task.cron_expression, Task.timezone).where(Task.task_key == task_key)
    return (await db.execute(query)).tuples().one_or_none()


async def save_schedule(db: AsyncSession, task_key: str, cron: str, timezone: str) -> None:
    stmt = insert(Task).values(task_key=task_key, cron_expression=cron, timezone=timezone)
    await db.execute(stmt.on_duplicate_key_update(cron_expression=stmt.inserted.cron_expression))


async def delete_schedule(db: AsyncSession, task_key: str) -> None:
    await db.execute(delete(Task).where(Task.task_key == task_key))


async def enabled_schedule(db: AsyncSession, task_key: str) -> tuple[str, str] | None:
    query = select(Task.cron_expression, Task.timezone).where(
        Task.task_key == task_key, Task.enabled.is_(True),
    )
    return (await db.execute(query)).tuples().one_or_none()


async def enabled_schedules(db: AsyncSession) -> dict[str, tuple[str, str]]:
    query = select(Task.task_key, Task.cron_expression, Task.timezone).where(Task.enabled.is_(True))
    rows = (await db.execute(query)).all()
    return {key: (cron, timezone) for key, cron, timezone in rows}
