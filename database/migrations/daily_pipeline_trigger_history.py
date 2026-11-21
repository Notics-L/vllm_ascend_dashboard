"""Create the Daily workflow dispatch history table without seeding records."""
from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineTriggerHistory
from sqlalchemy import inspect, text


async def run() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(DailyPipelineTriggerHistory.__table__.create, checkfirst=True)
        columns = await conn.run_sync(lambda c: inspect(c).get_columns(DailyPipelineTriggerHistory.__tablename__))
        if not any(column["name"] == "is_deleted" for column in columns):
            await conn.execute(text(
                "ALTER TABLE daily_pipeline_trigger_history "
                "ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0"
            ))
