"""Add commit_count column to contributor_metrics table."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_metrics_commit_count_migration")
MIGRATION_VERSION = "20261013_02_add_commit_count"

_CHECK_COLUMN_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
      AND COLUMN_NAME = :column
"""


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(
        text(_CHECK_COLUMN_SQL), {"table": table, "column": column}
    )
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    altered: dict[str, int] = {}
    async with SessionLocal() as db:
        if not await _column_exists(db, "contributor_metrics", "commit_count"):
            try:
                await db.execute(text("""
                    ALTER TABLE contributor_metrics
                    ADD COLUMN commit_count INT NOT NULL DEFAULT 0
                    COMMENT '代码提交次数'
                    AFTER closed_pr_count
                """))
                logger.info("Added commit_count column to contributor_metrics")
                altered["contributor_metrics.commit_count"] = 1
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to add commit_count: %s", exc)
        else:
            logger.info("commit_count already exists in contributor_metrics")

        try:
            await db.execute(
                text("""
                    INSERT INTO migration_history (version) VALUES (:version)
                    ON DUPLICATE KEY UPDATE version = VALUES(version)
                """),
                {"version": MIGRATION_VERSION},
            )
        except Exception as exc:
            logger.error("Failed to record migration: %s", exc)

        await db.commit()

    logger.info("Applied %s: %s", MIGRATION_VERSION, altered)
    return altered
