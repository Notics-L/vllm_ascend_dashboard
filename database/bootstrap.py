"""Local MySQL schema bootstrap.

This command is intentionally limited to MySQL. Production deployments use
``operations/production/migrate.sh``; this module is for local development
only and never runs when ``ENVIRONMENT=production``.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

repository_root = Path(__file__).resolve().parents[1]
application_root = repository_root / "backend"
if not application_root.is_dir():
    application_root = repository_root
sys.path.insert(0, str(repository_root))
sys.path.insert(0, str(application_root))

from infrastructure.core.security import hash_password
from infrastructure.db.base import SessionLocal, engine
from infrastructure.persistence.models import Base, User

from database.migrations.daily_pipeline_case_counts import run as migrate_daily_case_counts
from database.migrations.daily_pipeline_collection import run as migrate_daily_collection
from database.migrations.daily_pipeline_config import run as seed_daily_pipeline_config
from database.migrations.daily_pipeline_issue_reopen import run as migrate_daily_issue_reopen
from database.migrations.daily_pipeline_manual_processing import (
    run as migrate_daily_manual_processing,
)
from database.migrations.daily_pipeline_merged_pr import run as migrate_daily_merged_pr
from database.migrations.daily_pipeline_notifications import run as migrate_daily_notifications
from database.migrations.daily_pipeline_retryable_nodes import run as migrate_daily_retryable_nodes
from database.migrations.daily_pipeline_scheduled_tasks import run as migrate_daily_schedules
from database.migrations.daily_pipeline_tracking import run as migrate_daily_tracking
from database.migrations.daily_pipeline_trigger_history import run as migrate_daily_trigger_history
from database.migrations.notifications import (
    ensure_notification_contacts,
)
from database.migrations.notifications import (
    run as migrate_notifications,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("database_bootstrap")

VERSION_TABLE = "database_versions"


async def _table_exists(table_name: str) -> bool:
    def inspect_tables(connection):
        from sqlalchemy import inspect
        return table_name in inspect(connection).get_table_names()

    async with engine.connect() as connection:
        return await connection.run_sync(inspect_tables)


async def _ensure_github_cache() -> None:
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS github_cache (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                owner VARCHAR(100) NOT NULL,
                repo VARCHAR(100) NOT NULL,
                data_type VARCHAR(50) NOT NULL,
                days INT NOT NULL DEFAULT 1,
                cache_data JSON NOT NULL,
                cached_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                UNIQUE KEY uq_github_cache_owner_repo_type_days (owner, repo, data_type, days),
                INDEX idx_github_cache_cached_at (cached_at),
                INDEX idx_github_cache_expires_at (expires_at)
            ) ENGINE=InnoDB
        """))
        await db.commit()


async def create_tables_with_latest_schema() -> None:
    """Create model tables and the non-ORM GitHub cache table in MySQL."""
    if engine.dialect.name != "mysql":
        raise RuntimeError(f"MySQL bootstrap requires MySQL, got {engine.dialect.name}")
    from database.migrations.daily_pipeline_process_nodes import run as rename_process_nodes
    await rename_process_nodes()
    async with engine.begin() as connection:
        from database.migrations.view_backed_user_permissions import (
            ensure_view_backed_user_permissions,
        )
        await ensure_view_backed_user_permissions(connection)
        # The ORM target is local users; pre-create the split-DB table against the base table.
        await ensure_notification_contacts(connection, view_only=True)
        await connection.run_sync(Base.metadata.create_all)
    await _ensure_github_cache()


async def ensure_version_table() -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                version VARCHAR(100) NOT NULL UNIQUE,
                applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                description VARCHAR(500) NOT NULL DEFAULT ''
            ) ENGINE=InnoDB
        """))


async def get_current_version() -> str:
    if not await _table_exists(VERSION_TABLE):
        return "0.0.0"
    async with SessionLocal() as db:
        result = await db.execute(text(f"SELECT version FROM {VERSION_TABLE} ORDER BY id DESC LIMIT 1"))
        row = result.first()
        return str(row[0]) if row else "0.0.0"


async def mark_version_applied(version: str, description: str = "") -> None:
    async with SessionLocal() as db:
        await db.execute(text(
            f"INSERT INTO {VERSION_TABLE} (version, description, applied_at) "
            "VALUES (:version, :description, :applied_at)"
        ), {"version": version, "description": description, "applied_at": datetime.now()})
        await db.commit()


async def run_upgrades() -> None:
    await migrate_daily_schedules()
    await seed_daily_pipeline_config()
    await migrate_daily_trigger_history()
    await migrate_daily_collection()
    await migrate_daily_case_counts()
    await migrate_daily_tracking()
    await migrate_daily_merged_pr()
    await migrate_daily_manual_processing()
    await migrate_daily_notifications()
    await migrate_daily_issue_reopen()
    await migrate_daily_retryable_nodes()
    await migrate_notifications()
    await ensure_version_table()
    if await get_current_version() == "0.0.0":
        await mark_version_applied("current", "Current MySQL schema bootstrap")


async def create_default_users() -> None:
    """Create development-only seed users when explicitly requested."""
    async with SessionLocal() as db:
        existing = (await db.execute(select(User.id).limit(1))).first()
        if existing:
            logger.info("Users already exist; skipping development seed users")
            return
        db.add_all([
            User(username="admin", email="admin@vllm-ascend.local", password_hash=hash_password("admin123"), role="super_admin", is_active=True),
            User(username="manager", email="manager@vllm-ascend.local", password_hash=hash_password("manager123"), role="admin", is_active=True),
            User(username="user", email="user@vllm-ascend.local", password_hash=hash_password("user123"), role="user", is_active=True),
        ])
        await db.commit()
        logger.warning("Development seed users created; change their passwords immediately")


async def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL development schema bootstrap")
    parser.add_argument("--no-upgrade", action="store_true", help="Skip the local version marker")
    parser.add_argument("--no-users", action="store_true", help="Do not create development seed users")
    args = parser.parse_args()

    if os.environ.get("ENVIRONMENT", "development").lower() == "production":
        raise RuntimeError("database/bootstrap.py is forbidden in production; run operations/production/migrate.sh")
    try:
        await create_tables_with_latest_schema()
        if not args.no_upgrade:
            await run_upgrades()
        if not args.no_users:
            await create_default_users()
    except SQLAlchemyError:
        logger.exception("MySQL bootstrap failed")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
