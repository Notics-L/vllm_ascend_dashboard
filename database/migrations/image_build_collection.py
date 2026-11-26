"""Create the persistent collection table for dashboard-triggered image builds."""

import os

from sqlalchemy import text

from database.migrations.task_queue import MIGRATION_HISTORY_DDL
from infrastructure.db.base import engine
from infrastructure.persistence.models import ImageBuildCollectionRun

MIGRATION_VERSION = "20261126_01_image_build_collection_runs"


async def run() -> None:
    table = ImageBuildCollectionRun.__table__
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        await conn.run_sync(table.create, checkfirst=True)
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            scheduler = os.getenv("SCHEDULER_DB_USER", "scheduler_svc")
            control = os.getenv("CONTROL_DB_USER", "control_svc")
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE ON `{table.name}` TO :account@'%'"),
                {"account": scheduler},
            )
            await conn.execute(
                text(f"GRANT SELECT, UPDATE ON `{table.name}` TO :account@'%'"),
                {"account": control},
            )
            await conn.execute(
                text("GRANT SELECT ON `image_build_trigger_records` TO :account@'%'"),
                {"account": scheduler},
            )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
