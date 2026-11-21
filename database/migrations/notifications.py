"""Migration: notification center contacts, events and deliveries."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("notifications_migration")
MIGRATION_VERSION = "20261121_01_notifications"

_CHECK_TABLE_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
"""

_CHECK_COLUMN_SQL = """
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = :table
      AND COLUMN_NAME = :column
"""


async def _table_exists(db, table: str) -> bool:
    result = await db.execute(text(_CHECK_TABLE_SQL), {"table": table})
    return int(result.scalar_one()) > 0


async def _column_exists(db, table: str, column: str) -> bool:
    result = await db.execute(text(_CHECK_COLUMN_SQL), {"table": table, "column": column})
    return int(result.scalar_one()) > 0


async def run() -> dict[str, int]:
    created: dict[str, int] = {}
    async with SessionLocal() as db:
        if not await _table_exists(db, "notification_contacts"):
            await db.execute(text("""
                CREATE TABLE notification_contacts (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT DEFAULT NULL,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(255) DEFAULT NULL,
                    phone VARCHAR(32) DEFAULT NULL,
                    phone_verified_at DATETIME DEFAULT NULL,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    KEY ix_notification_contacts_user (user_id),
                    KEY ix_notification_contacts_email (email),
                    KEY ix_notification_contacts_phone (phone),
                    KEY ix_notification_contacts_enabled (enabled),
                    CONSTRAINT fk_notification_contacts_user FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知联系人'
            """))
            created["notification_contacts"] = 1
            logger.info("Created notification_contacts")

        if not await _table_exists(db, "notification_groups"):
            await db.execute(text("""
                CREATE TABLE notification_groups (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    description VARCHAR(500) DEFAULT NULL,
                    enabled TINYINT(1) NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_notification_groups_name (name),
                    KEY ix_notification_groups_enabled (enabled)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知联系人组'
            """))
            created["notification_groups"] = 1
            logger.info("Created notification_groups")

        if not await _table_exists(db, "notification_group_members"):
            await db.execute(text("""
                CREATE TABLE notification_group_members (
                    group_id BIGINT NOT NULL,
                    contact_id BIGINT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, contact_id),
                    CONSTRAINT fk_notification_group_members_group FOREIGN KEY (group_id)
                        REFERENCES notification_groups(id) ON DELETE CASCADE,
                    CONSTRAINT fk_notification_group_members_contact FOREIGN KEY (contact_id)
                        REFERENCES notification_contacts(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知联系人组成员'
            """))
            created["notification_group_members"] = 1
            logger.info("Created notification_group_members")

        if not await _table_exists(db, "notification_events"):
            await db.execute(text("""
                CREATE TABLE notification_events (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    source VARCHAR(50) NOT NULL,
                    event_type VARCHAR(50) NOT NULL DEFAULT 'notification',
                    severity VARCHAR(20) NOT NULL DEFAULT 'info',
                    title VARCHAR(500) NOT NULL,
                    summary TEXT DEFAULT NULL,
                    payload JSON DEFAULT NULL,
                    dedupe_key VARCHAR(255) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_notification_events_dedupe (dedupe_key),
                    KEY ix_notification_events_source (source),
                    KEY ix_notification_events_severity (severity),
                    KEY ix_notification_events_status (status),
                    KEY ix_notification_events_created (created_at),
                    KEY ix_notification_events_source_created (source, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知事件'
            """))
            created["notification_events"] = 1
            logger.info("Created notification_events")

        if not await _table_exists(db, "notification_deliveries"):
            await db.execute(text("""
                CREATE TABLE notification_deliveries (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    event_id BIGINT NOT NULL,
                    channel VARCHAR(20) NOT NULL DEFAULT 'email',
                    recipient VARCHAR(255) NOT NULL,
                    contact_id BIGINT DEFAULT NULL,
                    attempt INT NOT NULL DEFAULT 1,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    provider VARCHAR(50) DEFAULT NULL,
                    provider_message_id VARCHAR(255) DEFAULT NULL,
                    error TEXT DEFAULT NULL,
                    sent_at DATETIME DEFAULT NULL,
                    delivered_at DATETIME DEFAULT NULL,
                    answered_at DATETIME DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY ix_notification_deliveries_event (event_id),
                    KEY ix_notification_deliveries_channel (channel),
                    KEY ix_notification_deliveries_status (status),
                    KEY ix_notification_deliveries_contact (contact_id),
                    KEY ix_notification_deliveries_created (created_at),
                    KEY ix_notification_deliveries_event_channel (event_id, channel),
                    CONSTRAINT fk_notification_deliveries_event FOREIGN KEY (event_id)
                        REFERENCES notification_events(id) ON DELETE CASCADE,
                    CONSTRAINT fk_notification_deliveries_contact FOREIGN KEY (contact_id)
                        REFERENCES notification_contacts(id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知渠道投递记录'
            """))
            created["notification_deliveries"] = 1
            logger.info("Created notification_deliveries")

        if await _table_exists(db, "users") and not await _column_exists(db, "users", "phone"):
            await db.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(32) DEFAULT NULL"))
            created["users.phone"] = 1
            logger.info("Added users.phone")

        if created:
            await db.commit()
    return created
