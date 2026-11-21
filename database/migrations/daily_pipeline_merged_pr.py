"""Create the internal Daily merged-PR table and its collection checkpoint."""

from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineMergedPR, DailyPipelineTriggerHistory
from sqlalchemy import inspect, text


async def run() -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns(DailyPipelineTriggerHistory.__tablename__)
        )
        if not any(column["name"] == "merged_prs_synced" for column in columns):
            await conn.execute(text(
                "ALTER TABLE daily_pipeline_trigger_history ADD COLUMN merged_prs_synced BOOLEAN NULL"
            ))
        await conn.run_sync(DailyPipelineMergedPR.__table__.create, checkfirst=True)
        columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns(DailyPipelineMergedPR.__tablename__)
        )
        existing = {column["name"] for column in columns}
        additions = [
            f"ADD COLUMN {name} {column_type} NULL"
            for name, column_type in (
                ("author", "VARCHAR(255)"), ("merged_by", "VARCHAR(255)"), ("url", "VARCHAR(512)"),
            )
            if name not in existing
        ]
        if additions:
            await conn.execute(text("ALTER TABLE daily_pipeline_merged_pr " + ", ".join(additions)))
