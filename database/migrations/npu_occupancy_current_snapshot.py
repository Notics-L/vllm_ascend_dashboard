"""Keep only the newest occupancy payload per environment and index time-window reads."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal
from resource_dashboard.npu_occupancy_config import POOLS

logger = logging.getLogger("npu_occupancy_current_snapshot_migration")
MIGRATION_VERSION = "20261016_01_npu_occupancy_current_snapshot"


async def _migration_applied(db) -> bool:
    return bool((await db.scalar(text("""
        SELECT 1 FROM migration_history
        WHERE version = :version
        LIMIT 1
    """), {"version": MIGRATION_VERSION})))


async def _index_exists(db, name: str) -> bool:
    return bool((await db.scalar(text("""
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'npu_occupancy_raw_envs'
          AND index_name = :name
        LIMIT 1
    """), {"name": name})))


async def _column_exists(db, name: str) -> bool:
    return bool((await db.scalar(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'npu_occupancy_raw_envs'
          AND column_name = :name
        LIMIT 1
    """), {"name": name})))


async def run() -> str:
    async with SessionLocal() as db:
        if await _migration_applied(db):
            logger.info("NPU occupancy current-snapshot migration already applied, skipping.")
            return MIGRATION_VERSION

        for name, definition in (
            ("pool", "VARCHAR(255) NULL"),
            ("effective_start_at", "TIMESTAMP NULL"),
            ("effective_end_at", "TIMESTAMP NULL"),
        ):
            if not await _column_exists(db, name):
                await db.execute(text(
                    f"ALTER TABLE npu_occupancy_raw_envs ADD COLUMN {name} {definition}"
                ))

        # The old schema preserved every upstream revision. Retain exactly the
        # newest payload before making env_id the durable current-snapshot key.
        await db.execute(text("""
            DELETE older
            FROM npu_occupancy_raw_envs AS older
            INNER JOIN npu_occupancy_raw_envs AS newer
              ON newer.env_id = older.env_id
             AND newer.source_updated_at > older.source_updated_at
        """))

        # Seed indexed fields from retained payloads so the first page request
        # is useful before the next scheduler run. Pool labels come from the
        # same configuration the repository uses, not hard-coded SQL strings.
        pool_cases = "\n".join(
            "WHEN JSON_UNQUOTE(JSON_EXTRACT(raw.payload, '$.cluster')) "
            f"LIKE :cluster_prefix_{index} THEN :pool_name_{index}"
            for index, pool in enumerate(POOLS)
        )
        pool_params = {
            **{
                f"cluster_prefix_{index}": f"{pool.code}%"
                for index, pool in enumerate(POOLS)
            },
            **{
                f"pool_name_{index}": pool.name
                for index, pool in enumerate(POOLS)
            },
        }
        await db.execute(text(f"""
            UPDATE npu_occupancy_raw_envs AS raw
            LEFT JOIN npu_occupancy_sync_state AS state ON state.id = 1
            SET raw.pool = CASE
                    {pool_cases}
                    ELSE CONCAT('unconfigured:', COALESCE(
                        JSON_UNQUOTE(JSON_EXTRACT(raw.payload, '$.cluster')), 'unknown'
                    ))
                END,
                raw.effective_start_at = STR_TO_DATE(
                    REPLACE(SUBSTRING(
                        JSON_UNQUOTE(JSON_EXTRACT(raw.payload, '$.created_at')), 1, 19
                    ), 'T', ' '),
                    '%Y-%m-%d %H:%i:%s'
                ),
                raw.effective_end_at = CASE
                    WHEN JSON_UNQUOTE(JSON_EXTRACT(raw.payload, '$.status'))
                        IN ('active', 'provisioning') THEN state.last_snapshot_at
                    ELSE STR_TO_DATE(
                        REPLACE(SUBSTRING(
                            JSON_UNQUOTE(JSON_EXTRACT(raw.payload, '$.expires_at')), 1, 19
                        ), 'T', ' '),
                        '%Y-%m-%d %H:%i:%s'
                    )
                END
        """), pool_params)

        if await _index_exists(db, "uq_npu_occupancy_env_revision"):
            await db.execute(text(
                "ALTER TABLE npu_occupancy_raw_envs "
                "DROP INDEX uq_npu_occupancy_env_revision"
            ))
        if not await _index_exists(db, "uq_npu_occupancy_env_id"):
            await db.execute(text(
                "ALTER TABLE npu_occupancy_raw_envs "
                "ADD UNIQUE KEY uq_npu_occupancy_env_id (env_id)"
            ))
        if not await _index_exists(db, "ix_npu_occupancy_pool_end_start"):
            await db.execute(text("""
                CREATE INDEX ix_npu_occupancy_pool_end_start
                ON npu_occupancy_raw_envs (pool, effective_end_at, effective_start_at)
            """))
        if not await _index_exists(db, "ix_npu_occupancy_effective_end"):
            await db.execute(text("""
                CREATE INDEX ix_npu_occupancy_effective_end
                ON npu_occupancy_raw_envs (effective_end_at)
            """))
        await db.execute(text("""
            INSERT INTO migration_history (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version = VALUES(version)
        """), {"version": MIGRATION_VERSION})
        await db.commit()

    logger.info("Applied NPU occupancy current-snapshot migration %s", MIGRATION_VERSION)
    return MIGRATION_VERSION
