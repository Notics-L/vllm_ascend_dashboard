"""Contributor board settings table for quadrant/scoring configuration."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_board_settings_migration")
MIGRATION_VERSION = "20261013_01_contributor_board_settings"

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
        if not await _table_exists(db, "contributor_board_settings"):
            try:
                await db.execute(text("""
                    CREATE TABLE contributor_board_settings (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        setting_key VARCHAR(100) NOT NULL UNIQUE COMMENT '配置键',
                        setting_value JSON NOT NULL COMMENT '配置值(JSON)',
                        description VARCHAR(500) DEFAULT NULL COMMENT '配置说明',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """))
                logger.info("Created contributor_board_settings table")
                created["contributor_board_settings"] = 1

                # 插入默认配置
                await db.execute(text("""
                    INSERT INTO contributor_board_settings (setting_key, setting_value, description) VALUES
                    ('quadrant_y_axis', '{"y_axis_type":"review_count"}', '四象限Y轴指标类型'),
                    ('scoring_weights', '{"merged_pr_weight":10.0,"code_line_weight":0.5,"review_weight":3.0,"approval_weight":5.0,"core_module_weight":8.0}', '贡献分数计算权重'),
                    ('role_thresholds', '{"star_threshold":30,"active_threshold":15,"max_error_score":2}', '静态角色标签阈值')
                """))
                logger.info("Inserted default settings")
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to create settings table: %s", exc)
        else:
            logger.info("contributor_board_settings already exists")
            # 确保默认配置存在
            for key, val, desc in [
                ('quadrant_y_axis', '{"y_axis_type":"review_count"}', '四象限Y轴指标类型'),
                ('scoring_weights', '{"merged_pr_weight":10.0,"code_line_weight":0.5,"review_weight":3.0,"approval_weight":5.0,"core_module_weight":8.0}', '贡献分数计算权重'),
                ('role_thresholds', '{"star_threshold":30,"active_threshold":15,"max_error_score":2}', '静态角色标签阈值'),
            ]:
                check = await db.execute(text("""
                    SELECT COUNT(*) FROM contributor_board_settings WHERE setting_key = :key
                """), {"key": key})
                if int(check.scalar_one()) == 0:
                    await db.execute(text("""
                        INSERT INTO contributor_board_settings (setting_key, setting_value, description)
                        VALUES (:key, :val, :desc)
                    """), {"key": key, "val": val, "desc": desc})

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration: %s", exc)

        await db.commit()

    logger.info("Applied %s: %s", MIGRATION_VERSION, created)
    return created
