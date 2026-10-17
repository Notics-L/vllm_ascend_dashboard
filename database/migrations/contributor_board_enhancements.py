"""Contributor board enhancements: employee_id, repo_name, pending_pr_count columns."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_board_enhancements_migration")
MIGRATION_VERSION = "20261012_01_contributor_board_enhancements"

# MySQL INFORMATION_SCHEMA query to check if a column exists in a table.
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
        # contributors.employee_id
        if not await _column_exists(db, "contributors", "employee_id"):
            try:
                await db.execute(text("""
                    ALTER TABLE contributors
                    ADD COLUMN employee_id VARCHAR(100) NULL AFTER company
                """))
                logger.info("Added contributors.employee_id")
                altered["contributors.employee_id"] = 1
            except Exception as exc:
                logger.error("Failed to add contributors.employee_id: %s", exc)
        else:
            logger.info("contributors.employee_id already exists, skipping")

        # contributors employee_id index
        try:
            await db.execute(text("""
                CREATE INDEX idx_contributors_employee_id
                ON contributors(employee_id)
            """))
            logger.info("Created idx_contributors_employee_id")
        except Exception as exc:
            if "Duplicate" not in str(exc):
                logger.warning("Could not create idx_contributors_employee_id: %s", exc)

        # contributor_metrics.repo_name
        if not await _column_exists(db, "contributor_metrics", "repo_name"):
            try:
                await db.execute(text("""
                    ALTER TABLE contributor_metrics
                    ADD COLUMN repo_name VARCHAR(200) DEFAULT 'vllm-ascend'
                        AFTER contributor_login
                """))
                logger.info("Added contributor_metrics.repo_name")
                altered["contributor_metrics.repo_name"] = 1
            except Exception as exc:
                logger.error("Failed to add contributor_metrics.repo_name: %s", exc)
        else:
            logger.info("contributor_metrics.repo_name already exists, skipping")

        # contributor_metrics repo_name index
        try:
            await db.execute(text("""
                CREATE INDEX idx_contributor_metrics_repo
                ON contributor_metrics(repo_name)
            """))
            logger.info("Created idx_contributor_metrics_repo")
        except Exception as exc:
            if "Duplicate" not in str(exc):
                logger.warning("Could not create idx_contributor_metrics_repo: %s", exc)

        # contributor_metrics.pending_pr_count
        if not await _column_exists(db, "contributor_metrics", "pending_pr_count"):
            try:
                await db.execute(text("""
                    ALTER TABLE contributor_metrics
                    ADD COLUMN pending_pr_count INT DEFAULT 0 AFTER merged_pr_count
                """))
                logger.info("Added contributor_metrics.pending_pr_count")
                altered["contributor_metrics.pending_pr_count"] = 1
            except Exception as exc:
                logger.error("Failed to add contributor_metrics.pending_pr_count: %s", exc)
        else:
            logger.info("contributor_metrics.pending_pr_count already exists, skipping")

        # Record migration version
        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied contributor board enhancements %s: %s", MIGRATION_VERSION, altered)
    return altered
