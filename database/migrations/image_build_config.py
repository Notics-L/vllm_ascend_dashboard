"""Create the image build settings table and seed the source repository."""

import os

from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert

from infrastructure.db.base import engine
from infrastructure.persistence.models import ImageBuildConfig

from database.migrations.task_queue import MIGRATION_HISTORY_DDL

MIGRATION_VERSION = "20261126_04_image_build_config"


async def run() -> None:
    table = ImageBuildConfig.__table__
    async with engine.begin() as conn:
        await conn.execute(text(MIGRATION_HISTORY_DDL))
        await conn.run_sync(table.create, checkfirst=True)
        seed = insert(table).values(config_key="repository", config_value="vllm-project/vllm-ascend")
        await conn.execute(seed.on_duplicate_key_update(config_key=table.c.config_key))
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            for variable, default_user in (("CONTROL_DB_USER", "control_svc"), ("SCHEDULER_DB_USER", "scheduler_svc")):
                await conn.execute(
                    text(f"GRANT SELECT ON `{table.name}` TO :account@'%'"),
                    {"account": os.getenv(variable, default_user)},
                )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
