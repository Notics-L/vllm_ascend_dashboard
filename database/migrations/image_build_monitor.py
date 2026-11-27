"""Migration: image build monitor tables."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("image_build_monitor_migration")
MIGRATION_VERSION = "20261116_01_image_build_monitor"

_CHECK_TABLE_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
"""


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(_CHECK_TABLE_SQL), {"table": table})
    return int(result.scalar_one()) > 0


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(
        text(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table
              AND COLUMN_NAME = :column
            """
        ),
        {"table": table, "column": column},
    )
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        if not await _table_exists(db, "image_build_monitor_targets"):
            await db.execute(text("""
                CREATE TABLE image_build_monitor_targets (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    release_workflow_id BIGINT NOT NULL,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    schedule_cron VARCHAR(100) DEFAULT NULL,
                    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
                    grace_minutes INT NOT NULL DEFAULT 30,
                    lookback_days INT NOT NULL DEFAULT 2,
                    notify_emails JSON DEFAULT NULL,
                    notify_on_failure TINYINT(1) NOT NULL DEFAULT 1,
                    notify_on_missing TINYINT(1) NOT NULL DEFAULT 1,
                    notify_on_stuck TINYINT(1) NOT NULL DEFAULT 1,
                    stuck_threshold_minutes INT NOT NULL DEFAULT 180,
                    cooldown_minutes INT NOT NULL DEFAULT 60,
                    last_checked_at DATETIME DEFAULT NULL,
                    last_check_success_at DATETIME DEFAULT NULL,
                    last_check_error TEXT DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_image_build_monitor_workflow (release_workflow_id),
                    KEY ix_image_build_monitor_enabled (enabled),
                    CONSTRAINT fk_image_build_monitor_workflow FOREIGN KEY (release_workflow_id)
                        REFERENCES release_workflows(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='镜像构建监控目标'
            """))
            created["image_build_monitor_targets"] = 1
            logger.info("Created image_build_monitor_targets")

        if not await _table_exists(db, "image_build_alerts"):
            await db.execute(text("""
                CREATE TABLE image_build_alerts (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    target_id BIGINT NOT NULL,
                    release_workflow_id BIGINT NOT NULL,
                    github_run_id BIGINT DEFAULT NULL,
                    alert_type VARCHAR(20) NOT NULL,
                    severity VARCHAR(20) NOT NULL DEFAULT 'warning',
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    title VARCHAR(500) NOT NULL,
                    message TEXT DEFAULT NULL,
                    run_url VARCHAR(500) DEFAULT NULL,
                    branch VARCHAR(255) DEFAULT NULL,
                    event VARCHAR(50) DEFAULT NULL,
                    dedupe_key VARCHAR(255) NOT NULL,
                    first_detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME DEFAULT NULL,
                    resolved_by VARCHAR(100) DEFAULT NULL,
                    notified_at DATETIME DEFAULT NULL,
                    last_notified_at DATETIME DEFAULT NULL,
                    notification_success TINYINT(1) DEFAULT NULL,
                    notification_error TEXT DEFAULT NULL,
                    details JSON DEFAULT NULL,
                    UNIQUE KEY uq_image_build_alert_dedupe (dedupe_key),
                    KEY ix_image_build_alert_target (target_id),
                    KEY ix_image_build_alert_workflow (release_workflow_id),
                    KEY ix_image_build_alert_status_type (status, alert_type),
                    KEY ix_image_build_alert_first_detected (first_detected_at),
                    CONSTRAINT fk_image_build_alert_target FOREIGN KEY (target_id)
                        REFERENCES image_build_monitor_targets(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='镜像构建异常告警'
            """))
            created["image_build_alerts"] = 1
            logger.info("Created image_build_alerts")

        if await _table_exists(db, "image_build_monitor_targets"):
            for column, ddl in (
                ("last_check_success_at", "DATETIME DEFAULT NULL"),
                ("last_check_error", "TEXT DEFAULT NULL"),
                ("notify_window_enabled", "TINYINT(1) NOT NULL DEFAULT 1"),
                ("notify_window_start", "VARCHAR(5) NOT NULL DEFAULT '20:00'"),
                ("notify_window_end", "VARCHAR(5) NOT NULL DEFAULT '08:00'"),
            ):
                if not await _column_exists(db, "image_build_monitor_targets", column):
                    await db.execute(
                        text(
                            f"ALTER TABLE image_build_monitor_targets ADD COLUMN {column} {ddl}"
                        )
                    )
                    created[f"image_build_monitor_targets.{column}"] = 1
                    logger.info("Added image_build_monitor_targets.%s", column)

        if created:
            await db.commit()
    return created
