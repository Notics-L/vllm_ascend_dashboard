"""Create contributor_sync_records table for tracking sync history."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_sync_records_migration")
MIGRATION_VERSION = "20261014_02_contributor_sync_records"


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS contributor_sync_records (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    repo_name VARCHAR(200),
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    since_date DATE NULL,
                    contributors_synced INT DEFAULT 0,
                    commits_classified INT DEFAULT 0,
                    build_plans_created INT DEFAULT 0,
                    errors JSON,
                    duration_seconds FLOAT DEFAULT 0,
                    synced_at TIMESTAMP NULL,
                    progress_percent FLOAT DEFAULT 0,
                    progress_message VARCHAR(500),
                    started_at TIMESTAMP NULL,
                    completed_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_status (status),
                    INDEX idx_repo_name (repo_name),
                    INDEX idx_created_at (created_at),
                    INDEX idx_status_created (status, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["contributor_sync_records"] = 1
        except Exception as exc:
            logger.error("Failed to create contributor_sync_records: %s", exc)

        # Add missing columns if table already existed (MySQL doesn't support IF NOT EXISTS for ADD COLUMN)
        result = await db.execute(text("""
            SELECT COUNT(*) as cnt FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contributor_sync_records' AND COLUMN_NAME = 'started_at'
        """))
        if int(result.scalar_one()) == 0:
            try:
                await db.execute(text("""
                    ALTER TABLE contributor_sync_records ADD COLUMN started_at TIMESTAMP NULL AFTER progress_message
                """))
            except Exception:
                pass

        result = await db.execute(text("""
            SELECT COUNT(*) as cnt FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'contributor_sync_records' AND COLUMN_NAME = 'completed_at'
        """))
        if int(result.scalar_one()) == 0:
            try:
                await db.execute(text("""
                    ALTER TABLE contributor_sync_records ADD COLUMN completed_at TIMESTAMP NULL AFTER started_at
                """))
            except Exception:
                pass

        # Add index if not exists
        try:
            await db.execute(text("""
                ALTER TABLE contributor_sync_records ADD INDEX idx_status_created (status, created_at)
            """))
        except Exception:
            pass

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied sync records migration %s: %s", MIGRATION_VERSION, created)
    return created
