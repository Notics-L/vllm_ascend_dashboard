"""Migration: contributor_scoring_version, contributor_score_snapshot, contributor_score_update_audit."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_scoring_version_migration")
MIGRATION_VERSION = "20261016_01_contributor_scoring_version"

_CHECK_TABLE_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
"""


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(_CHECK_TABLE_SQL), {"table": table})
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        # 1. contributor_scoring_version
        if not await _table_exists(db, "contributor_scoring_version"):
            try:
                await db.execute(text("""
                    CREATE TABLE contributor_scoring_version (
                        id              INT AUTO_INCREMENT PRIMARY KEY,
                        version         VARCHAR(30) NOT NULL COMMENT '版本号',
                        repo_name       VARCHAR(200) DEFAULT 'vllm-ascend',
                        weights         JSON NOT NULL COMMENT '权重配置快照',
                        method          ENUM('manual','auto','ai') NOT NULL COMMENT '权重确定方式',
                        status          ENUM('draft','active','archived') NOT NULL DEFAULT 'draft' COMMENT '版本状态',
                        data_summary    JSON DEFAULT NULL COMMENT '数据分布摘要',
                        ai_prompt       TEXT DEFAULT NULL,
                        ai_response     TEXT DEFAULT NULL,
                        ai_model        VARCHAR(100) DEFAULT NULL,
                        confidence_score FLOAT DEFAULT NULL,
                        description     TEXT DEFAULT NULL,
                        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_by      VARCHAR(100) DEFAULT NULL,
                        activated_at    TIMESTAMP NULL,
                        activated_by    VARCHAR(100) DEFAULT NULL,
                        INDEX idx_repo_name (repo_name),
                        INDEX idx_scoring_version_repo_status (repo_name, status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评分权重版本记录'
                """))
                logger.info("Created contributor_scoring_version")
                created["contributor_scoring_version"] = 1
            except Exception as exc:
                logger.error("Failed to create contributor_scoring_version: %s", exc)
        else:
            logger.info("contributor_scoring_version already exists, skipping")

        # 2. contributor_score_snapshot
        if not await _table_exists(db, "contributor_score_snapshot"):
            try:
                await db.execute(text("""
                    CREATE TABLE contributor_score_snapshot (
                        id               INT AUTO_INCREMENT PRIMARY KEY,
                        version_id       INT NOT NULL,
                        repo_name        VARCHAR(200) DEFAULT 'vllm-ascend',
                        snapshot_date    DATE NOT NULL,
                        period_start     DATE DEFAULT NULL,
                        period_end       DATE DEFAULT NULL,
                        contributor_count INT NOT NULL DEFAULT 0,
                        statistics       JSON DEFAULT NULL,
                        scores           JSON NOT NULL,
                        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_by       VARCHAR(100) DEFAULT NULL,
                        description      TEXT DEFAULT NULL,
                        INDEX idx_version_id (version_id),
                        INDEX idx_repo_name (repo_name),
                        INDEX idx_snapshot_date (snapshot_date),
                        CONSTRAINT fk_snapshot_version FOREIGN KEY (version_id)
                            REFERENCES contributor_scoring_version(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分数快照'
                """))
                logger.info("Created contributor_score_snapshot")
                created["contributor_score_snapshot"] = 1
            except Exception as exc:
                logger.error("Failed to create contributor_score_snapshot: %s", exc)
        else:
            logger.info("contributor_score_snapshot already exists, skipping")

        # 3. contributor_score_update_audit
        if not await _table_exists(db, "contributor_score_update_audit"):
            try:
                await db.execute(text("""
                    CREATE TABLE contributor_score_update_audit (
                        id               INT AUTO_INCREMENT PRIMARY KEY,
                        action           VARCHAR(50) NOT NULL COMMENT '操作类型',
                        version_id       INT DEFAULT NULL,
                        snapshot_id      INT DEFAULT NULL,
                        repo_name        VARCHAR(200) DEFAULT 'vllm-ascend',
                        details          JSON DEFAULT NULL,
                        summary          TEXT DEFAULT NULL,
                        weight_diff      JSON DEFAULT NULL,
                        max_weight_shift FLOAT DEFAULT NULL COMMENT '最大单项权重变化幅度(%)',
                        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_by       VARCHAR(100) DEFAULT NULL,
                        INDEX idx_repo_name (repo_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评分更新审计日志'
                """))
                logger.info("Created contributor_score_update_audit")
                created["contributor_score_update_audit"] = 1
            except Exception as exc:
                logger.error("Failed to create contributor_score_update_audit: %s", exc)
        else:
            logger.info("contributor_score_update_audit already exists, skipping")

        # Create seed: seed version v1.0.0 if no active version exists — seed from
        # existing scoring_weights setting so that the board is not left without
        # an active version after the refactor.
        try:
            import json
            needs_seed = await db.execute(text("""
                SELECT COUNT(*) FROM contributor_scoring_version WHERE status = 'active'
            """))
            if int(needs_seed.scalar_one()) == 0:
                seed_weights = await db.execute(text("""
                    SELECT setting_value FROM contributor_board_settings
                    WHERE setting_key = 'scoring_weights'
                """))
                row = seed_weights.fetchone()
                if row is None:
                    w = {}
                else:
                    raw = row[0]
                    if isinstance(raw, str):
                        w = json.loads(raw)
                    elif isinstance(raw, dict):
                        w = raw
                    else:
                        w = {}
                await db.execute(text("""
                    INSERT INTO contributor_scoring_version
                        (version, weights, method, status, description, created_by)
                    VALUES (:ver, :w, 'manual', 'active', 'v1.0.0 initial version (migrated from current settings)', 'system')
                """), {"ver": "v1.0.0", "w": json.dumps(w)})
                logger.info("Seeded contributor_scoring_version v1.0.0 from existing settings")
            else:
                logger.info("contributor_scoring_version already has active version, skipping seed")
        except Exception as exc:
            logger.error("Failed to seed scoring version: %s", exc)

        # Record migration version
        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied scoring version migration %s: %s", MIGRATION_VERSION, created)
    return created
