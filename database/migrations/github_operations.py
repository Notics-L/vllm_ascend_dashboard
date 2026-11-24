"""Create the GitHub operation history table."""

import os

from sqlalchemy import inspect, text

from infrastructure.db.base import engine
from infrastructure.persistence.models import GitHubOperation

_LEGACY_TABLES = ("github_operations", "github_operatoxx_log")


async def run() -> None:
    table = GitHubOperation.__table__
    async with engine.begin() as conn:
        table_names = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
        audit_tables = [name for name in (*_LEGACY_TABLES, table.name) if name in table_names]
        if len(audit_tables) > 1:
            raise RuntimeError(f"Multiple GitHub audit tables exist: {', '.join(audit_tables)}. Reconcile their records before migrating.")
        if audit_tables and audit_tables[0] != table.name:
            await conn.execute(text(f"RENAME TABLE `{audit_tables[0]}` TO `{table.name}`"))

        await conn.run_sync(table.create, checkfirst=True)
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns(table.name))
        if not any(column["name"] == "credential_source" for column in columns):
            await conn.execute(text(f"ALTER TABLE `{table.name}` ADD COLUMN credential_source VARCHAR(16) NULL"))
        credential_index = next(index for index in table.indexes if index.name == "ix_github_operations_credential_time")
        await conn.run_sync(credential_index.create, checkfirst=True)
        await conn.execute(text(f"""
            UPDATE `{table.name}`
            SET credential_source = CASE
                WHEN source IN ('daily_config', 'daily_collection', 'daily_revert') THEN 'daily'
                WHEN source IN ('github_client', 'github_api', 'failure_analysis', 'agent') THEN 'global'
                WHEN source = 'workflow_actions' THEN 'actions'
            END
            WHERE credential_source IS NULL
              AND source IN ('daily_config', 'daily_collection', 'daily_revert',
                             'github_client', 'github_api', 'failure_analysis', 'agent', 'workflow_actions')
        """))
        if os.getenv("ENVIRONMENT", "").lower() == "production":
            await conn.execute(
                text(f"GRANT SELECT, INSERT ON `{table.name}` TO :account@'%'"),
                {"account": os.getenv("CONTROL_DB_USER", "control_svc")},
            )
            for setting, default in (
                ("SCHEDULER_DB_USER", "scheduler_svc"),
                ("COLLECTOR_DB_USER", "collector_svc"),
            ):
                await conn.execute(
                    text(f"GRANT INSERT ON `{table.name}` TO :account@'%'"),
                    {"account": os.getenv(setting, default)},
                )
