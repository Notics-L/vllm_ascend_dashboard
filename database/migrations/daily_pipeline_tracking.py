"""Create tracking tables without changing existing users or collection data."""
from infrastructure.db.base import engine
from infrastructure.persistence.models import (
    Base,
    DailyPipelineFailure,
    DailyPipelineIssue,
    DailyPipelineProcessNode,
    DailyPipelineProcessNodeExecution,
    DailyPipelineVersionFlow,
)
from sqlalchemy import inspect, text

from database.migrations.daily_pipeline_version_flow import run as migrate_version_flow


async def run():
    from database.migrations.daily_pipeline_process_nodes import run as rename_process_nodes
    await rename_process_nodes()
    from database.migrations.daily_pipeline_failure_source import run as normalize_sources
    await normalize_sources()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[model.__table__ for model in (DailyPipelineIssue, DailyPipelineVersionFlow, DailyPipelineFailure, DailyPipelineProcessNode, DailyPipelineProcessNodeExecution)], checkfirst=True)
        case_columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("daily_pipeline_test_cases"))
        if not any(column["name"] == "auto_disabled" for column in case_columns):
            await conn.execute(text("ALTER TABLE daily_pipeline_test_cases ADD COLUMN auto_disabled BOOLEAN NOT NULL DEFAULT 0"))
        if not any(column["name"] == "manual_enabled_at" for column in case_columns):
            await conn.execute(text("ALTER TABLE daily_pipeline_test_cases ADD COLUMN manual_enabled_at DATETIME(6) NULL"))
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("daily_pipeline_failures"))
        if any(column["name"] == "deadline_at" for column in columns):
            await conn.execute(text("ALTER TABLE daily_pipeline_failures DROP COLUMN deadline_at"))
    await migrate_version_flow()
