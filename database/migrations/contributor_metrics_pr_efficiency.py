"""Add PR-efficiency columns to contributor_metrics table."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_metrics_pr_efficiency_migration")
MIGRATION_VERSION = "20261031_01_add_pr_efficiency_columns"

_CHECK_COLUMN_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
      AND COLUMN_NAME = :column
"""

_NEW_COLUMNS = [
    ("ci_failure_count", "INT NOT NULL DEFAULT 0 COMMENT 'CI 失败次数'"),
    ("first_review_hours_avg", "FLOAT NULL COMMENT '首次 Review 响应时长（小时均值）'"),
    ("merge_hours_avg", "FLOAT NULL COMMENT '从创建到合并时长（小时均值）'"),
    ("ci_duration_hours_avg", "FLOAT NULL COMMENT 'CI 执行时长（小时均值）'"),
]


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(
        text(_CHECK_COLUMN_SQL), {"table": table, "column": column}
    )
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    altered: dict[str, int] = {}
    async with SessionLocal() as db:
        for col_name, col_def in _NEW_COLUMNS:
            if not await _column_exists(db, "contributor_metrics", col_name):
                try:
                    await db.execute(text(
                        f"ALTER TABLE contributor_metrics ADD COLUMN {col_name} {col_def}"
                    ))
                    logger.info("Added %s column to contributor_metrics", col_name)
                    altered[f"contributor_metrics.{col_name}"] = 1
                except Exception as exc:
                    await db.rollback()
                    logger.error("Failed to add %s: %s", col_name, exc)
            else:
                logger.info("%s already exists in contributor_metrics", col_name)

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
