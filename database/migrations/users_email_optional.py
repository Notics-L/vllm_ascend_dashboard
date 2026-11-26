"""Migration: make users.email optional (allow NULL for imported users without email)."""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("users_email_optional_migration")
MIGRATION_VERSION = "20261126_01_users_email_optional"

_COLUMN_SQL = """
    SELECT IS_NULLABLE, TABLE_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS c
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
    WHERE c.TABLE_SCHEMA = DATABASE()
      AND c.TABLE_NAME = 'users'
      AND c.COLUMN_NAME = 'email'
"""


async def run() -> dict[str, int]:
    changed: dict[str, int] = {}
    async with SessionLocal() as db:
        result = await db.execute(text(_COLUMN_SQL))
        row = result.first()
        if row is None:
            logger.info("users.email not found; skip")
            return changed
        is_nullable, table_type = row
        if table_type != "BASE TABLE":
            logger.info("users is not a base table; skip email nullable migration")
            return changed
        if str(is_nullable).upper() == "YES":
            logger.info("users.email is already nullable")
            return changed
        await db.execute(text("ALTER TABLE `users` MODIFY COLUMN `email` VARCHAR(100) NULL"))
        await db.commit()
        changed["users.email.nullable"] = 1
        logger.info("users.email changed to nullable")
    return changed
