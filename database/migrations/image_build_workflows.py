"""Create the dynamic image build workflow definition table."""

import os

from infrastructure.db.base import engine
from infrastructure.persistence.models import ImageBuildWorkflowDefinition
from sqlalchemy import text

MIGRATION_VERSION = "20261125_01_image_build_workflows"


async def run() -> None:
    table = ImageBuildWorkflowDefinition.__table__
    async with engine.begin() as conn:
        await conn.run_sync(table.create, checkfirst=True)
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            await conn.execute(
                text(f"GRANT SELECT ON `{table.name}` TO :account@'%'"),
                {"account": os.getenv("CONTROL_DB_USER", "control_svc")},
            )
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE ON `{table.name}` TO :account@'%'"),
                {"account": os.getenv("SCHEDULER_DB_USER", "scheduler_svc")},
            )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
