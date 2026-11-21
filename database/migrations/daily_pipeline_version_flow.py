"""Add version-compatibility flows to an existing Daily tracking database."""

from sqlalchemy import inspect, text

from infrastructure.db.base import engine
from infrastructure.persistence.models import DailyPipelineFailure, DailyPipelineVersionFlow


async def run():
    async with engine.begin() as conn:
        await conn.run_sync(DailyPipelineVersionFlow.__table__.create, checkfirst=True)
        columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns(DailyPipelineFailure.__tablename__)
        )
        if any(column["name"] == "version_flow_id" for column in columns):
            return
        await conn.execute(text("""
            ALTER TABLE daily_pipeline_failures
            ADD COLUMN version_flow_id BIGINT NULL,
            ADD INDEX ix_daily_pipeline_failures_version_flow_id (version_flow_id),
            ADD CONSTRAINT fk_daily_failure_version_flow
                FOREIGN KEY (version_flow_id) REFERENCES daily_pipeline_version_flows(id)
        """))
