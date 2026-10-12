"""Allow Queue / Running snapshots to retain fractional physical NPU usage."""
from __future__ import annotations

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

MIGRATION_VERSION = "20261010_02_ci_npu_queue_snapshot_physical_running"


async def run() -> str:
    async with SessionLocal() as db:
        applied = await db.scalar(
            text("SELECT 1 FROM migration_history WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )
        if not applied:
            await db.execute(
                text(
                    "ALTER TABLE ci_npu_queue_snapshots "
                    "MODIFY COLUMN running_cards DOUBLE NOT NULL DEFAULT 0"
                )
            )
            await db.execute(
                text("INSERT INTO migration_history (version) VALUES (:version)"),
                {"version": MIGRATION_VERSION},
            )
            await db.commit()
    return MIGRATION_VERSION
