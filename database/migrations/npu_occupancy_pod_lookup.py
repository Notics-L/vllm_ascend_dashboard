"""Index pod-history names for exact resource-dashboard metadata joins."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("npu_occupancy_pod_lookup_migration")
MIGRATION_VERSION = "20261026_01_npu_occupancy_pod_lookup"


async def _column_exists(db, name: str) -> bool:
    return bool((await db.scalar(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'npu_occupancy_raw_envs'
          AND column_name = :name
        LIMIT 1
    """), {"name": name})))


async def _index_exists(db, name: str) -> bool:
    return bool((await db.scalar(text("""
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'npu_occupancy_raw_envs'
          AND index_name = :name
        LIMIT 1
    """), {"name": name})))


async def run() -> str:
    async with SessionLocal() as db:
        applied = await db.scalar(text("""
            SELECT 1 FROM migration_history WHERE version = :version LIMIT 1
        """), {"version": MIGRATION_VERSION})
        if applied:
            return MIGRATION_VERSION
        if not await _column_exists(db, "pod_name"):
            await db.execute(text(
                "ALTER TABLE npu_occupancy_raw_envs ADD COLUMN pod_name VARCHAR(255) NULL"
            ))
        await db.execute(text("""
            UPDATE npu_occupancy_raw_envs
            SET pod_name = NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.name')), '')
            WHERE pod_name IS NULL
        """))
        if not await _index_exists(db, "ix_npu_occupancy_pod_name"):
            await db.execute(text(
                "CREATE INDEX ix_npu_occupancy_pod_name ON npu_occupancy_raw_envs (pod_name)"
            ))
        await db.execute(text("""
            INSERT INTO migration_history (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version = VALUES(version)
        """), {"version": MIGRATION_VERSION})
        await db.commit()
    logger.info("Applied NPU occupancy pod lookup migration %s", MIGRATION_VERSION)
    return MIGRATION_VERSION
