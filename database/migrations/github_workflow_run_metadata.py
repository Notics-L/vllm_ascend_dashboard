"""Persistent GitHub workflow-run metadata cache for NPU occupancy."""
from sqlalchemy import text

from infrastructure.db.base import SessionLocal

MIGRATION_VERSION = "20261015_01_github_workflow_run_metadata"


async def run() -> None:
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS github_workflow_run_metadata (
              run_id BIGINT PRIMARY KEY, organization VARCHAR(255) NULL,
              repository VARCHAR(255) NULL, workflow_name VARCHAR(255) NULL,
              display_title TEXT NULL, run_number INT NULL, event VARCHAR(64) NULL,
              head_branch VARCHAR(255) NULL, status VARCHAR(64) NULL,
              conclusion VARCHAR(64) NULL, html_url VARCHAR(500) NULL,
              fetch_status VARCHAR(32) NOT NULL, error_message TEXT NULL,
              fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              KEY ix_github_workflow_metadata_status (fetch_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        await db.execute(text("""
            INSERT INTO migration_history (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version=VALUES(version)
        """), {"version": MIGRATION_VERSION})
        await db.commit()
