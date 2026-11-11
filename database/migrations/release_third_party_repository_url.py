"""Add repository_url column to release_third_party_versions.

Production table was created before this column was added to the ORM model,
causing SELECT queries to fail with "Unknown column 'repository_url'".
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("release_third_party_repository_url_migration")
MIGRATION_VERSION = "20261111_release_third_party_repository_url"


async def run() -> dict[str, int]:
    done: dict[str, int] = {}
    async with SessionLocal() as db:
        def _get_columns(sync_session):
            rows = sync_session.execute(text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'release_third_party_versions'"
            )).fetchall()
            return [r[0] for r in rows]

        existing_cols = await db.run_sync(_get_columns)

        if "repository_url" not in existing_cols:
            logger.info("Adding missing column 'repository_url' to release_third_party_versions")
            await db.execute(text(
                "ALTER TABLE release_third_party_versions "
                "ADD COLUMN repository_url VARCHAR(500) DEFAULT NULL"
            ))
            done["repository_url"] = 1

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info(
        "Applied release_third_party_repository_url migration %s: %d columns added",
        MIGRATION_VERSION, len(done),
    )
    return done
