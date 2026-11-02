"""Migration: 贡献者看板重构 — DROP 旧聚合表 + 新建 contributor_profiles 宽表。

将旧的按月粒度 contributor_metrics / commit_classifications / error_records /
quadrant_snapshots 清空并重建为「每贡献者一行」的 contributor_profiles 宽表，
承载 7 维原始指标 + 综合评分 + 四层分区。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_board_refactor_migration")
MIGRATION_VERSION = "20261102_01_contributor_board_refactor"

_CHECK_TABLE_SQL = """
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table
"""

# 旧表，DROP 后不再使用（脏数据彻底清除）
_OLD_TABLES = [
    "quadrant_snapshots",
    "contributor_metrics",
    "commit_classifications",
    "error_records",
]


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(_CHECK_TABLE_SQL), {"table": table})
    return int(result.scalar_one()) > 0


async def _drop_table(db, table: str) -> None:
    if await _table_exists(db, table):
        await db.execute(text(f"DROP TABLE IF EXISTS {table}"))
        logger.info("Dropped table %s", table)
    else:
        logger.info("Table %s not present, skipping drop", table)


async def _create_profiles(db) -> bool:
    if await _table_exists(db, "contributor_profiles"):
        logger.info("contributor_profiles already exists, skipping")
        return False
    await db.execute(text("""
        CREATE TABLE contributor_profiles (
            id                 INT AUTO_INCREMENT PRIMARY KEY,
            repo_name          VARCHAR(200) DEFAULT NULL,
            login              VARCHAR(100) NOT NULL,
            contributor_id     INT DEFAULT NULL,

            -- 7 维原始指标
            commit_count       INT NOT NULL DEFAULT 0,
            code_files         INT NOT NULL DEFAULT 0,
            doc_files          INT NOT NULL DEFAULT 0,
            pr_merged          INT NOT NULL DEFAULT 0,
            pr_open            INT NOT NULL DEFAULT 0,
            pr_closed          INT NOT NULL DEFAULT 0,
            active_days_90     INT NOT NULL DEFAULT 0,
            span_days          INT NOT NULL DEFAULT 0,

            -- Reviewer 角色指标
            review_count       INT NOT NULL DEFAULT 0,
            approval_count     INT NOT NULL DEFAULT 0,
            review_ratio       FLOAT NOT NULL DEFAULT 0,

            -- 评分与分区
            total_score        FLOAT NOT NULL DEFAULT 0,
            tier               VARCHAR(20) DEFAULT NULL,
            role               VARCHAR(20) DEFAULT NULL,
            first_seen         DATE DEFAULT NULL,
            last_seen          DATE DEFAULT NULL,

            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

            UNIQUE KEY uq_contributor_profile_login_repo (login, repo_name),
            INDEX idx_profile_tier (tier),
            INDEX idx_profile_score (total_score),
            INDEX idx_profile_last_seen (last_seen),
            CONSTRAINT fk_profile_contributor FOREIGN KEY (contributor_id)
                REFERENCES contributors(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='贡献者画像宽表(7维指标+评分+四层分区)'
    """))
    logger.info("Created contributor_profiles")
    return True


async def run() -> dict[str, int]:
    created: dict[str, int] = {"dropped": 0, "created": 0}
    async with SessionLocal() as db:
        for table in _OLD_TABLES:
            await _drop_table(db, table)
            created["dropped"] += 1
        if await _create_profiles(db):
            created["created"] = 1

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied contributor_board_refactor migration %s: dropped=%d created=%d",
                MIGRATION_VERSION, created["dropped"], created["created"])
    return created
