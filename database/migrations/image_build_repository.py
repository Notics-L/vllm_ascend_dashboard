"""Scope workflow definitions and submission history to their repositories."""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from infrastructure.db.base import engine

MIGRATION_VERSION = "20261126_05_image_build_repository"
LEGACY_REPOSITORY = "vllm-project/vllm-ascend"
WORKFLOWS = "image_build_workflow_definitions"
RECORDS = "image_build_trigger_records"


async def _backfill_workflows(conn: AsyncConnection) -> None:
    columns = await conn.run_sync(lambda sync: inspect(sync).get_columns(WORKFLOWS))
    if not any(column["name"] == "repository" for column in columns):
        await conn.execute(text(f"ALTER TABLE {WORKFLOWS} ADD COLUMN repository VARCHAR(140) NULL"))

    primary_key = await conn.run_sync(lambda sync: inspect(sync).get_pk_constraint(WORKFLOWS))
    if primary_key["constrained_columns"] == ["branch_name"]:
        await conn.execute(
            text(f"UPDATE {WORKFLOWS} SET repository = :repository WHERE repository IS NULL"),
            {"repository": LEGACY_REPOSITORY},
        )
        await conn.execute(text(f"""
            ALTER TABLE {WORKFLOWS} MODIFY COLUMN repository VARCHAR(140) NOT NULL,
            DROP PRIMARY KEY, ADD PRIMARY KEY (repository, branch_name)
        """))


async def _backfill_records(conn: AsyncConnection) -> None:
    columns = await conn.run_sync(lambda sync: inspect(sync).get_columns(RECORDS))
    repository = next((column for column in columns if column["name"] == "repository"), None)
    if repository is None:
        await conn.execute(text(f"ALTER TABLE {RECORDS} ADD COLUMN repository VARCHAR(140) NULL"))
    if repository is None or repository["nullable"]:
        await conn.execute(
            text(f"""
                UPDATE {RECORDS}
                SET repository = CASE
                    WHEN JSON_TYPE(JSON_EXTRACT(dispatch_snapshot, '$.repository')) = 'STRING'
                    THEN JSON_UNQUOTE(JSON_EXTRACT(dispatch_snapshot, '$.repository'))
                    ELSE :repository
                END
                WHERE repository IS NULL
            """),
            {"repository": LEGACY_REPOSITORY},
        )
        await conn.execute(text(f"ALTER TABLE {RECORDS} MODIFY COLUMN repository VARCHAR(140) NOT NULL"))


async def run() -> None:
    async with engine.begin() as conn:
        await _backfill_workflows(conn)
        await _backfill_records(conn)
        await conn.execute(
            text("INSERT IGNORE INTO migration_history (version) VALUES (:version)"),
            {"version": MIGRATION_VERSION},
        )
