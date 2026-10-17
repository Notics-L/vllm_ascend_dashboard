"""Migration: contributor_ai_reports table."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("contributor_ai_report_migration")
MIGRATION_VERSION = "20261017_01_contributor_ai_reports"

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
        if not await _table_exists(db, "contributor_ai_reports"):
            try:
                await db.execute(text("""
                    CREATE TABLE contributor_ai_reports (
                        id                  INT AUTO_INCREMENT PRIMARY KEY,
                        report_name         VARCHAR(300) NOT NULL,
                        report_type         VARCHAR(50) DEFAULT NULL COMMENT 'quarterly/monthly/custom/adhoc',
                        period_start        DATE DEFAULT NULL,
                        period_end          DATE DEFAULT NULL,
                        generated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        generated_by        VARCHAR(100) DEFAULT NULL,
                        generation_source   VARCHAR(20) DEFAULT NULL COMMENT 'manual/scheduled/ai_auto',
                        weight_version_id   INT DEFAULT NULL,
                        llm_model           VARCHAR(100) DEFAULT NULL,
                        executive_summary   TEXT DEFAULT NULL,
                        quadrant_analysis   JSON DEFAULT NULL,
                        individual_evaluations JSON DEFAULT NULL,
                        recommendations     JSON DEFAULT NULL,
                        confidence_overall  FLOAT DEFAULT NULL,
                        report_content      TEXT DEFAULT NULL,
                        report_file_url     VARCHAR(500) DEFAULT NULL,
                        schedule_cron       VARCHAR(100) DEFAULT NULL,
                        enabled             TINYINT(1) DEFAULT 1,
                        repo_name           VARCHAR(200) DEFAULT 'vllm-ascend',
                        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_repo_name (repo_name),
                        INDEX idx_ai_report_period (period_start, period_end),
                        INDEX idx_ai_report_created (generated_at),
                        CONSTRAINT fk_ai_report_version FOREIGN KEY (weight_version_id)
                            REFERENCES contributor_scoring_version(id) ON DELETE SET NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI综合分析报告'
                """))
                logger.info("Created contributor_ai_reports")
                created["contributor_ai_reports"] = 1
            except Exception as exc:
                logger.error("Failed to create contributor_ai_reports: %s", exc)
        else:
            logger.info("contributor_ai_reports already exists, skipping")

        try:
            await db.execute(text("""
                INSERT INTO migration_history (version) VALUES (:version)
                ON DUPLICATE KEY UPDATE version = VALUES(version)
            """), {"version": MIGRATION_VERSION})
        except Exception as exc:
            logger.error("Failed to record migration version: %s", exc)

        await db.commit()

    logger.info("Applied ai report migration %s: %s", MIGRATION_VERSION, created)
    return created
