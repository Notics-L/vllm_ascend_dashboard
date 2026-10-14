"""Add repo_name column to commit_classifications and image_build_plans."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_board_repo_name_migration")
MIGRATION_VERSION = "20261014_01_add_repo_name_columns"

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
        # 1. commit_classifications.repo_name
        if not await _column_exists(db, "commit_classifications", "repo_name"):
            try:
                await db.execute(text("""
                    ALTER TABLE commit_classifications
                    ADD COLUMN repo_name VARCHAR(200) DEFAULT 'vllm-ascend'
                """))
                await db.execute(text("""
                    ALTER TABLE commit_classifications
                    ADD INDEX idx_repo_name (repo_name)
                """))
                # Backfill existing rows
                await db.execute(text("""
                    UPDATE commit_classifications SET repo_name = 'vllm-ascend' WHERE repo_name IS NULL
                """))
                altered["commit_classifications"] = 1
                logger.info("Added repo_name to commit_classifications")
            except Exception as exc:
                logger.error("Failed to add repo_name to commit_classifications: %s", exc)

        # 2. image_build_plans.repo_name
        if not await _column_exists(db, "image_build_plans", "repo_name"):
            try:
                await db.execute(text("""
                    ALTER TABLE image_build_plans
                    ADD COLUMN repo_name VARCHAR(200) DEFAULT 'vllm-ascend'
                """))
                await db.execute(text("""
                    ALTER TABLE image_build_plans
                    ADD INDEX idx_repo_name (repo_name)
                """))
                await db.execute(text("""
                    UPDATE image_build_plans SET repo_name = 'vllm-ascend' WHERE repo_name IS NULL
                """))
                altered["image_build_plans"] = 1
                logger.info("Added repo_name to image_build_plans")
            except Exception as exc:
                logger.error("Failed to add repo_name to image_build_plans: %s", exc)

        # Record migration
        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied repo_name migration %s: %s", MIGRATION_VERSION, altered)
    return altered
