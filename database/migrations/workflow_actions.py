"""Workflow Actions 用户授权与操作日志迁移。"""

from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import engine

logger = logging.getLogger(__name__)
MIGRATION_VERSION = "20261117_01_workflow_actions"

_PERMISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_action_permissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    workflow_path VARCHAR(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    granted_by VARCHAR(50) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_workflow_action_user_path (user_id, workflow_path),
    KEY ix_workflow_action_permissions_user_id (user_id),
    CONSTRAINT fk_workflow_action_permission_user FOREIGN KEY (user_id)
        REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_LOGS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_action_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    action VARCHAR(30) NOT NULL,
    workflow_path VARCHAR(500) NOT NULL,
    command_text TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_workflow_action_logs_username (username),
    KEY ix_workflow_action_logs_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


async def _ensure_case_sensitive_path(conn) -> None:
    result = await conn.execute(
        text(
            "SELECT COLLATION_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'workflow_action_permissions' "
            "AND COLUMN_NAME = 'workflow_path'"
        )
    )
    collation = result.scalar_one_or_none()
    if collation and collation != "utf8mb4_bin":
        await conn.execute(
            text(
                "ALTER TABLE workflow_action_permissions "
                "MODIFY workflow_path VARCHAR(500) "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL"
            )
        )


async def run() -> None:
    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT 1 FROM migration_history WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )
        applied = existing.fetchone() is None
        if applied:
            await conn.execute(text(_PERMISSIONS_DDL))
            await conn.execute(text(_LOGS_DDL))
            await conn.execute(
                text("INSERT INTO migration_history (version) VALUES (:version)"),
                {"version": MIGRATION_VERSION},
            )
        await _ensure_case_sensitive_path(conn)
    if applied:
        logger.info("Applied workflow actions migration %s", MIGRATION_VERSION)
