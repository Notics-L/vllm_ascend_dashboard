"""Create contributor_sync_checkpoints table for resumable sync."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_sync_checkpoint_migration")
MIGRATION_VERSION = "20261105_01_contributor_sync_checkpoint"


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS contributor_sync_checkpoints (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    repo_name VARCHAR(200) NOT NULL,
                    last_processed_pr INT NOT NULL,
                    total_prs INT NOT NULL DEFAULT 0,
                    since_date DATE NOT NULL,
                    until_date DATE NULL,
                    profiles_snapshot JSON NOT NULL,
                    pr_numbers_done JSON NOT NULL,
                    contributors_count INT DEFAULT 0,
                    errors_count INT DEFAULT 0,
                    abort_reason VARCHAR(500) NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_repo_name (repo_name),
                    INDEX idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """))
            created["contributor_sync_checkpoints"] = 1
        except Exception as exc:
            logger.error("Failed to create contributor_sync_checkpoints: %s", exc)

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied sync checkpoint migration %s: %s", MIGRATION_VERSION, created)
    return created
