"""Indexes for the persisted resource-history query paths."""
from __future__ import annotations

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

MIGRATION_VERSION = "20261015_02_resource_metrics_query_indexes"


async def _ensure_index(db, table_name: str, index_name: str, columns: str) -> None:
    exists = await db.execute(
        text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :table_name "
            "AND index_name = :index_name LIMIT 1"
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    if exists.scalar_one_or_none() is None:
        await db.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({columns})"))


async def run() -> str:
    async with SessionLocal() as db:
        await _ensure_index(
            db,
            "resource_npu_metrics",
            "ix_resource_npu_metrics_cluster_collected",
            "cluster_id, collected_at",
        )
        await _ensure_index(
            db,
            "resource_node_metrics",
            "ix_resource_node_metrics_cluster_collected",
            "cluster_id, collected_at",
        )
        await db.execute(
            text(
                "INSERT INTO migration_history (version) VALUES (:version) "
                "ON DUPLICATE KEY UPDATE version = VALUES(version)"
            ),
            {"version": MIGRATION_VERSION},
        )
        await db.commit()
    return MIGRATION_VERSION
