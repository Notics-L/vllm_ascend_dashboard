"""Add new columns to release_analysis_tasks for sync task tracking."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("release_analysis_task_columns_migration")
MIGRATION_VERSION = "20261018_release_analysis_task_columns"

NEW_COLUMNS = [
    ("trigger_source", "VARCHAR(50) NOT NULL DEFAULT 'manual' AFTER task_type"),
    ("progress_message", "VARCHAR(500) NULL AFTER progress"),
    ("collected_items", "INT NOT NULL DEFAULT 0 AFTER processed_items"),
    ("days_back", "INT NOT NULL DEFAULT 30 AFTER collected_items"),
    ("max_runs", "INT NOT NULL DEFAULT 100 AFTER days_back"),
    ("duration_seconds", "FLOAT NOT NULL DEFAULT 0 AFTER max_runs"),
    ("errors_json", "TEXT NULL AFTER error_message"),
]


async def run() -> dict[str, int]:
    added: dict[str, int] = {}
    async with SessionLocal() as db:
        for col_name, col_def in NEW_COLUMNS:
            result = await db.execute(text(f"""
                SELECT COUNT(*) as cnt FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'release_analysis_tasks' AND COLUMN_NAME = '{col_name}'
            """))
            if int(result.scalar_one()) == 0:
                try:
                    await db.execute(text(f"""
                        ALTER TABLE release_analysis_tasks ADD COLUMN {col_name} {col_def}
                    """))
                    added[col_name] = 1
                    logger.info("Added column %s to release_analysis_tasks", col_name)
                except Exception as exc:
                    logger.error("Failed to add column %s: %s", col_name, exc)

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied release_analysis_task columns migration %s: %d columns added", MIGRATION_VERSION, len(added))
    return added
