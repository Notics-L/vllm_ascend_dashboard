"""Add checkpoint_kind / last_updated columns for daily-backfill checkpoints.

Adds:
- ``contributor_sync_checkpoints.checkpoint_kind`` (VARCHAR 20, default 'full_sync')
  used to distinguish full-sync (contributor_profiles) from daily-backfill
  (daily_prs / daily_commits) checkpoints so they don't collide.
- ``contributor_sync_checkpoints.last_updated`` (TIMESTAMP NULL)
  last explicit save/update time (in addition to the automatic updated_at).
- Loosen ``profiles_snapshot`` and ``pr_numbers_done`` to NULL (daily_backfill
  checkpoints don't store profiles).
- Widen ``contributor_sync_records.trigger_source`` to VARCHAR 32 (supports
  'scheduled_full' / 'scheduled_incremental').
- Add ``contributor_sync_records.background_task_id`` (INT NULL) used to link
  a sync record to its ``background_task_records`` row (cancel lookup).

Idempotent: safe to repeat.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_sync_checkpoint_kind_migration")
MIGRATION_VERSION = "20261109_01_contributor_sync_checkpoint_kind"


async def _ensure_column(
    db, table: str, column: str, definition: str, after: str | None = None
) -> bool:
    """Add a column if missing. Returns True if added."""
    result = await db.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :table AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    )
    if int(result.scalar_one()) > 0:
        return False
    try:
        after_clause = f" AFTER {after}" if after else ""
        await db.execute(
            text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}{after_clause}")
        )
        return True
    except Exception as e:
        logger.warning("Failed to add column %s.%s: %s", table, column, e)
        return False


async def run() -> dict[str, int]:
    applied: dict[str, int] = {}
    async with SessionLocal() as db:
        # 1. checkpoint_kind column on contributor_sync_checkpoints
        applied["checkpoint_kind"] = int(
            await _ensure_column(
                db,
                "contributor_sync_checkpoints",
                "checkpoint_kind",
                "VARCHAR(20) NOT NULL DEFAULT 'full_sync'",
                after="repo_name",
            )
        )
        # 2. last_updated column on contributor_sync_checkpoints
        applied["last_updated"] = int(
            await _ensure_column(
                db,
                "contributor_sync_checkpoints",
                "last_updated",
                "TIMESTAMP NULL",
                after="abort_reason",
            )
        )
        # 3. Widen trigger_source on contributor_sync_records (VARCHAR 20 -> 32)
        #    MySQL MODIFY COLUMN; ignore if column doesn't exist or already wide.
        try:
            await db.execute(
                text(
                    "ALTER TABLE contributor_sync_records "
                    "MODIFY COLUMN trigger_source VARCHAR(32) NOT NULL DEFAULT 'manual'"
                )
            )
            applied["trigger_source_widened"] = 1
        except Exception as e:
            logger.warning("Could not widen trigger_source: %s", e)

        # 4. background_task_id column on contributor_sync_records
        applied["background_task_id"] = int(
            await _ensure_column(
                db,
                "contributor_sync_records",
                "background_task_id",
                "INT NULL",
                after="trigger_source",
            )
        )
        # 5. Index on (repo_name, checkpoint_kind, status) for fast dedup lookups
        try:
            await db.execute(
                text(
                    "ALTER TABLE contributor_sync_checkpoints "
                    "ADD INDEX idx_repo_kind_status "
                    "(repo_name, checkpoint_kind, status)"
                )
            )
        except Exception:
            pass  # duplicate index — ignore

        # 6. Mark migration applied
        try:
            await db.execute(
                text(
                    "INSERT INTO migration_history (version) VALUES (:version) "
                    "ON DUPLICATE KEY UPDATE version = VALUES(version)"
                ),
                {"version": MIGRATION_VERSION},
            )
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied checkpoint_kind migration %s: %s", MIGRATION_VERSION, applied)
    return applied
