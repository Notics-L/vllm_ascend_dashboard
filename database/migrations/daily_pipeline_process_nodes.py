"""Rename process tables preserving their IDs, rows and foreign keys."""
from infrastructure.db.base import engine
from sqlalchemy import inspect, text

RENAMES = (
    ("daily_pipeline_processing_nodes", "daily_pipeline_process_nodes"),
    ("daily_pipeline_node_executions", "daily_pipeline_process_node_executions"),
)

async def run():
    async with engine.begin() as conn:
        tables = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
        for old, new in RENAMES:
            if old not in tables:
                continue
            if new in tables:
                raise RuntimeError(f"Both {old} and {new} exist; refusing to overwrite either table")
            await conn.execute(text(f"RENAME TABLE `{old}` TO `{new}`"))
            tables.remove(old)
            tables.add(new)
