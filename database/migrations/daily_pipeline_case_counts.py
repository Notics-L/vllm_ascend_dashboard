"""Add execution counters to the Daily case catalog."""

from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineTestCase
from sqlalchemy import inspect, text


async def run() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns(DailyPipelineTestCase.__tablename__)
        )
        existing = {column["name"] for column in columns}
        additions = [
            f"ADD COLUMN {name} INT NOT NULL DEFAULT 0"
            for name in ("success_count", "failure_count")
            if name not in existing
        ]
        if additions:
            await conn.execute(text("ALTER TABLE daily_pipeline_test_cases " + ", ".join(additions)))
