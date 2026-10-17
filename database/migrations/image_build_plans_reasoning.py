"""Add reasoning column to image_build_plans."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("image_build_plans_reasoning_migration")
MIGRATION_VERSION = "20261015_01_add_build_plan_reasoning"

_CHECK_COLUMN_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
      AND COLUMN_NAME = :column
"""


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(text(_CHECK_COLUMN_SQL), {"table": table, "column": column})
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    altered: dict[str, int] = {}
    async with SessionLocal() as db:
        if not await _column_exists(db, "image_build_plans", "reasoning"):
            try:
                await db.execute(text("""
                    ALTER TABLE image_build_plans
                    ADD COLUMN reasoning VARCHAR(500)
                """))
                altered["image_build_plans"] = 1
                logger.info("Added reasoning to image_build_plans")
            except Exception as exc:
                logger.error("Failed to add reasoning to image_build_plans: %s", exc)

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied reasoning migration %s: %s", MIGRATION_VERSION, altered)
    return altered
