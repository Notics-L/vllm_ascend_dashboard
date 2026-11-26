"""Add persistence for user-configured image build trigger tasks."""

import os

from sqlalchemy import text

from infrastructure.db.base import engine
from infrastructure.persistence.models import ImageBuildTriggerTask

from database.migrations.task_queue import MIGRATION_HISTORY_DDL

MIGRATION_VERSION = "20261125_03_image_build_trigger_tasks"


async def run() -> None:
    table = ImageBuildTriggerTask.__table__
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        await conn.run_sync(table.create, checkfirst=True)
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{table.name}` TO :account@'%'"),
                {"account": os.getenv("CONTROL_DB_USER", "control_svc")},
            )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
