"""Create durable samples for the NPU Queue / Running 24h chart."""
from __future__ import annotations

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

MIGRATION_VERSION = "20261010_01_ci_npu_queue_snapshots"


async def run() -> str:
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS ci_npu_queue_snapshots (
              id BIGINT NOT NULL AUTO_INCREMENT,
              captured_at TIMESTAMP NOT NULL,
              waiting_jobs INT NOT NULL DEFAULT 0,
              waiting_cards INT NOT NULL DEFAULT 0,
              running_jobs INT NOT NULL DEFAULT 0,
              running_cards INT NOT NULL DEFAULT 0,
              PRIMARY KEY (id),
              KEY ix_ci_npu_queue_snapshots_captured (captured_at)
            ) ENGINE=InnoDB
        """))
        await db.execute(text("""
            INSERT INTO migration_history (version) VALUES (:version)
            ON DUPLICATE KEY UPDATE version = VALUES(version)
        """), {"version": MIGRATION_VERSION})
        await db.commit()
    return MIGRATION_VERSION
