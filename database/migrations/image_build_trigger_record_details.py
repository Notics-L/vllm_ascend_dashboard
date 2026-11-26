"""Add the submitted parameters and GitHub status to image build history."""

from sqlalchemy import inspect, text

from infrastructure.db.base import engine
from infrastructure.persistence.models import ImageBuildTriggerRecord

MIGRATION_VERSION = "20261126_02_image_build_trigger_record_details"


async def run() -> None:
    table = ImageBuildTriggerRecord.__table__
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns(table.name))
        existing = {column["name"] for column in columns}
        additions = {
            "dispatch_snapshot": "JSON NULL",
            "github_status_code": "INT NULL",
        }
        for name, definition in additions.items():
            if name not in existing:
                await conn.execute(
                    text(f"ALTER TABLE `{table.name}` ADD COLUMN `{name}` {definition}")
                )
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
