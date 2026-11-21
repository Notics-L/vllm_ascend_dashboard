"""Replace legacy collection tables with the Daily root and execution-node tree.

The legacy rows are intentionally discarded. This reset runs only when the old
schema is detected; rerunning migrations never clears the new tree tables and
never reimports previously deleted trigger histories.
"""
from infrastructure.db.base import engine
from infrastructure.persistence.models import (
    Base,
    DailyPipelineCollectionNode,
    DailyPipelineCollectionRun,
)
from sqlalchemy import inspect, text

from database.migrations.task_queue import MIGRATION_HISTORY_DDL

TREE_SCHEMA_VERSION = "20261116_daily_collection_tree_schema"
NATURAL_IDENTITY_VERSION = "20261116_daily_collection_natural_identity"
LEGACY_TABLES = ("daily_pipeline_collection_case_results", "daily_pipeline_collection_jobs")


async def run() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        names = set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))
        columns = (
            await conn.run_sync(lambda sync: inspect(sync).get_columns("daily_pipeline_collection_runs"))
            if "daily_pipeline_collection_runs" in names else []
        )
        legacy = any(name in names for name in LEGACY_TABLES) or any(
            column["name"] == "parent_run_id" for column in columns
        )
        applied = await conn.scalar(
            text("SELECT 1 FROM migration_history WHERE version = :version"),
            {"version": TREE_SCHEMA_VERSION},
        )
        if legacy and applied:
            raise RuntimeError("Legacy collection tables reappeared after the tree migration; refusing to reset current data")
        if legacy:
            # Children first; never disable FK checks or touch unrelated tables.
            for table in (*LEGACY_TABLES, "daily_pipeline_collection_nodes", "daily_pipeline_collection_runs"):
                if table in names:
                    await conn.execute(text(f"DROP TABLE `{table}`"))
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[DailyPipelineCollectionRun.__table__, DailyPipelineCollectionNode.__table__],
            checkfirst=True,
        )
        node_columns = await conn.run_sync(
            lambda sync: inspect(sync).get_columns("daily_pipeline_collection_nodes"),
        )
        if any(column["name"] == "node_key" for column in node_columns):
            indexes = await conn.run_sync(
                lambda sync: inspect(sync).get_indexes("daily_pipeline_collection_nodes"),
            )
            for index in indexes:
                if "node_key" in index["column_names"]:
                    name = index["name"].replace("`", "``")
                    await conn.execute(text(f"ALTER TABLE daily_pipeline_collection_nodes DROP INDEX `{name}`"))
            await conn.execute(text("ALTER TABLE daily_pipeline_collection_nodes DROP COLUMN node_key"))
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": NATURAL_IDENTITY_VERSION},
        )
        # Collection is now registered per unfinished run, not by a global Cron.
        await conn.execute(text("DELETE FROM daily_pipeline_scheduled_tasks WHERE task_key = 'collect_daily_runs'"))
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": TREE_SCHEMA_VERSION},
        )
