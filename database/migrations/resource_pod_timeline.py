"""Store complete Kubernetes Pod lifecycle snapshots for the NPU timeline."""
from __future__ import annotations

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

MIGRATION_VERSION = "20261023_01_resource_pod_timeline"


async def run() -> str:
    async with SessionLocal() as db:
        exists = await db.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'resource_npu_metrics' "
                "AND column_name = 'pod_timeline_json' LIMIT 1"
            )
        )
        if exists.scalar_one_or_none() is None:
            await db.execute(
                text("ALTER TABLE resource_npu_metrics ADD COLUMN pod_timeline_json JSON NULL")
            )
        await db.execute(
            text(
                "INSERT INTO migration_history (version) VALUES (:version) "
                "ON DUPLICATE KEY UPDATE version = VALUES(version)"
            ),
            {"version": MIGRATION_VERSION},
        )
        await db.commit()
    return MIGRATION_VERSION
