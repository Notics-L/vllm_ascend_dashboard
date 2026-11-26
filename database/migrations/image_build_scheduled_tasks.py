"""Create and seed the image build schedule table."""

import os

from sqlalchemy import text

from infrastructure.db.base import engine
from infrastructure.persistence.models.image_build_schedule import (
    WORKFLOW_SYNC_TASK,
    ImageBuildScheduledTask,
)

from database.migrations.task_queue import MIGRATION_HISTORY_DDL

MIGRATION_VERSION = "20261125_02_image_build_scheduled_tasks"
DEFAULT_INTERVAL_MINUTES = 10


async def run() -> None:
    table = ImageBuildScheduledTask.__table__
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        await conn.run_sync(table.create, checkfirst=True)
        await conn.execute(
            text("""
                INSERT INTO image_build_scheduled_tasks (task_key, interval_minutes)
                VALUES (:task_key, :interval_minutes)
                ON DUPLICATE KEY UPDATE task_key = task_key
            """),
            {"task_key": WORKFLOW_SYNC_TASK, "interval_minutes": DEFAULT_INTERVAL_MINUTES},
        )
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            await conn.execute(
                text(f"GRANT SELECT ON `{table.name}` TO :account@'%'"),
                {"account": os.getenv("SCHEDULER_DB_USER", "scheduler_svc")},
            )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
