"""Keep the unfinished flow checkpoint when an Issue is closed and later reopened."""
from infrastructure.db.base import engine
from sqlalchemy import inspect, text


async def run():
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("daily_pipeline_failures"))
        if not any(column["name"] == "resume_state" for column in columns):
            await conn.execute(text("ALTER TABLE daily_pipeline_failures ADD COLUMN resume_state JSON NULL"))
