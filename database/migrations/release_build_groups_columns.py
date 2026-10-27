"""Expand narrow columns in release_build_groups."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("release_build_groups_columns_migration")
MIGRATION_VERSION = "20261026_release_build_groups_columns"

ALTERS = [
    ("target", "VARCHAR(500)"),
    ("os_name", "VARCHAR(500)"),
]


async def run() -> dict[str, int]:
    done: dict[str, int] = {}
    async with SessionLocal() as db:
        for col_name, col_type in ALTERS:
            await db.execute(text(f"ALTER TABLE release_build_groups MODIFY COLUMN {col_name} {col_type}"))
            done[col_name] = 1
            logger.info("Expanded column %s to %s", col_name, col_type)

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied release_build_groups columns migration %s: %d columns expanded", MIGRATION_VERSION, len(done))
    return done
