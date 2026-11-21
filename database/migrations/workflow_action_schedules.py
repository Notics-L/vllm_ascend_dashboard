"""Workflow Actions 定时任务及独立定时权限迁移。"""

from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import engine

logger = logging.getLogger(__name__)
MIGRATION_VERSION = "20261117_02_workflow_action_schedules"

_PERMISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_schedule_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    granted_by VARCHAR(50) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_workflow_schedule_permission_user (user_id),
    KEY ix_workflow_schedule_permissions_user_id (user_id),
    CONSTRAINT fk_workflow_schedule_permission_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_SCHEDULES_DDL = """
CREATE TABLE IF NOT EXISTS workflow_action_schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    workflow_path VARCHAR(500) NOT NULL,
    ref VARCHAR(200) NOT NULL DEFAULT 'main',
    inputs_yaml TEXT NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    setter_user_id INT NOT NULL,
    setter_username VARCHAR(50) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_run_at DATETIME DEFAULT NULL,
    running_at DATETIME DEFAULT NULL,
    last_output TEXT DEFAULT NULL,
    last_submission_status VARCHAR(16) DEFAULT NULL,
    KEY ix_workflow_action_schedules_enabled (enabled),
    KEY ix_workflow_action_schedules_setter (setter_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


async def _ensure_schedule_columns(conn) -> None:
    result = await conn.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'workflow_action_schedules'"
        )
    )
    columns = set(result.scalars())
    for name, definition in (
        ("running_at", "DATETIME DEFAULT NULL"),
        ("last_submission_status", "VARCHAR(16) DEFAULT NULL"),
    ):
        if name not in columns:
            await conn.execute(text(f"ALTER TABLE workflow_action_schedules ADD {name} {definition}"))


async def run() -> None:
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT 1 FROM migration_history WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )
        applied = existing.fetchone() is None
        if applied:
            await conn.execute(text(_PERMISSIONS_DDL))
            await conn.execute(text(_SCHEDULES_DDL))
            await conn.execute(
                text("INSERT INTO migration_history (version) VALUES (:version)"),
                {"version": MIGRATION_VERSION},
            )
        await _ensure_schedule_columns(conn)
    if applied:
        logger.info("Applied workflow action schedules migration %s", MIGRATION_VERSION)
