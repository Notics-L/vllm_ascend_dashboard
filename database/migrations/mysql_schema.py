"""Apply idempotent production MySQL schema migrations.

This command never creates, resets, or deletes users. Run it only after a
verified database backup has been created.
"""
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, text

repository_root = Path(__file__).resolve().parents[2]
application_root = repository_root / "backend"
if not application_root.is_dir():
    application_root = repository_root
sys.path.insert(0, str(application_root))

from infrastructure.db.base import SessionLocal, engine  # noqa: E402

logger = logging.getLogger("mysql_schema_migration")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

MIGRATION_VERSION = "20260929_02_workflow_auto_failure_analysis"
TABLE_COLUMN_MIGRATIONS = {
    "user_login_logs": {
        "ip_address_hashed": "VARCHAR(64) NULL",
        "login_method": "VARCHAR(20) NULL",
        "user_agent": "VARCHAR(500) NULL",
        "created_at": "TIMESTAMP NULL",
    },
    "job_failure_analysis": {
        "analysis_phase": "VARCHAR(30) NULL",
        "evidence_ledger": "JSON NULL",
        "validation_result": "JSON NULL",
        "agent_trace": "JSON NULL",
        "agent_steps": "INT NOT NULL DEFAULT 0",
    },
    "ci_jobs": {
        "processing_status": "VARCHAR(20) NOT NULL DEFAULT '未处理'",
        "notes": "TEXT NULL",
        "updated_by": "VARCHAR(50) NULL",
        "status_updated_at": "TIMESTAMP NULL",
    },
    "workflow_configs": {
        "materialize_name_regex": "VARCHAR(500) NULL",
        "auto_failure_analysis_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    },
    "pull_requests": {
        "author_email": "VARCHAR(200) NULL",
        "author_avatar_base64": "LONGTEXT NULL",
    },
    "test_cases": {
        "lifetime_runs": "INT NOT NULL DEFAULT 0",
        "lifetime_failures": "INT NOT NULL DEFAULT 0",
        "issues_found": "INT NOT NULL DEFAULT 0",
        "suspected_test_issue_count": "INT NOT NULL DEFAULT 0",
        "is_flaky_manual": "BOOLEAN NOT NULL DEFAULT FALSE",
        "auto_issues_found": "INT NOT NULL DEFAULT 0",
        "auto_suspected_test_issue_count": "INT NOT NULL DEFAULT 0",
        "issues_found_override": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    # 每日失败用例跟踪：物化记录需带上来源分支与人工处理字段，
    # 否则 _populate_daily_failure_records 写入会因 Unknown column 报 1054 被静默吞掉。
    "daily_failure_records": {
        "source_branch": "VARCHAR(100) NOT NULL DEFAULT 'main'",
        "problem_category": "VARCHAR(50) NULL",
        "related_pr": "VARCHAR(20) NULL",
        "processing_time": "TIMESTAMP NULL",
        "closure_time": "TIMESTAMP NULL",
    },
}
INDEX_MIGRATIONS = {
    "ci_jobs": {"ix_ci_jobs_processing_status": "processing_status"},
    "test_cases": {"ix_test_cases_is_flaky_manual": "is_flaky_manual"},
}

# 唯一索引替换：drop 旧索引后 add 带新列的唯一索引（幂等——缺列先由
# TABLE_COLUMN_MIGRATIONS 补上，drop 缺失索引时忽略，add 已存在索引时忽略）。
INDEX_REPLACEMENTS = [
    {
        "table": "daily_failure_records",
        "drop": "uq_daily_failure_date_wf_job",
        "add": "uq_daily_failure_date_branch_wf_job",
        "columns": "(report_date, source_branch, workflow_name, job_name)",
    },
]

# 整表新建（CREATE TABLE IF NOT EXISTS）：仅在建表迁移缺失时补齐。
CREATE_TABLE_MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS `job_log_summaries` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `job_id` BIGINT NOT NULL,
      `run_id` BIGINT NOT NULL,
      `summary` TEXT NULL,
      `log_excerpt` LONGTEXT NULL,
      `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
      `llm_provider` VARCHAR(50) NULL,
      `llm_model` VARCHAR(100) NULL,
      `prompt_tokens` INT NULL,
      `completion_tokens` INT NULL,
      `generation_time_seconds` FLOAT NULL,
      `error_message` VARCHAR(500) NULL,
      `created_at` TIMESTAMP NULL DEFAULT NULL,
      `updated_at` TIMESTAMP NULL DEFAULT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uq_job_log_summaries_job_id` (`job_id`),
      KEY `ix_job_log_summaries_run_id` (`run_id`),
      KEY `ix_job_log_summaries_status` (`status`)
    ) ENGINE=InnoDB
    """,
    # 当前模型 FO 映射。仅用于生成未来 Nightly 快照，不回写历史物化记录。
    """
    CREATE TABLE IF NOT EXISTS `model_fo_mappings` (
      `id` INT NOT NULL AUTO_INCREMENT,
      `model_key` VARCHAR(255) NOT NULL,
      `model_fo` VARCHAR(100) NOT NULL,
      `created_at` TIMESTAMP NULL DEFAULT NULL,
      `updated_at` TIMESTAMP NULL DEFAULT NULL,
      PRIMARY KEY (`id`),
      UNIQUE KEY `uq_model_fo_mapping_key` (`model_key`),
      KEY `ix_model_fo_mappings_model_key` (`model_key`)
    ) ENGINE=InnoDB
    """,
    # 调度器心跳表 — 独立 scheduler 进程每 20s 写入，API 读取以判断调度器存活。
    """
    CREATE TABLE IF NOT EXISTS `scheduler_heartbeat` (
      `id` INT NOT NULL,
      `running` BOOLEAN DEFAULT FALSE,
      `jobs` JSON,
      `pid` INT,
      `updated_at` TIMESTAMP NULL DEFAULT NULL,
      PRIMARY KEY (`id`)
    ) ENGINE=InnoDB
    """,
]


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_FORWARDING_VIEW_SOURCE = re.compile(
    r"\bFROM\s+`(?P<schema>[A-Za-z0-9_]+)`\.`(?P<table>[A-Za-z0-9_]+)`",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _TableTarget:
    logical_table: str
    physical_schema: str | None
    physical_table: str
    is_view: bool


def _quoted_identifier(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise RuntimeError(f"Unsafe MySQL identifier: {identifier!r}")
    return f"`{identifier}`"


async def _current_schema(db) -> str:
    schema = (await db.execute(text("SELECT DATABASE()"))).scalar_one()
    if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
        raise RuntimeError(f"Unsafe current MySQL schema: {schema!r}")
    return schema


async def _object_type(db, schema: str, table: str) -> str | None:
    return (await db.execute(text("""
        SELECT TABLE_TYPE
        FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :table
    """), {"schema": schema, "table": table})).scalar_one_or_none()


async def _resolve_table_target(db, table: str) -> _TableTarget:
    """Resolve a logical table or a simple cross-schema forwarding view.

    Production keeps a compatibility schema where selected control-plane
    tables are exposed as views.  Schema migrations must change the owning
    base table rather than issuing ALTER TABLE against the compatibility view.
    """
    logical_schema = await _current_schema(db)
    object_type = await _object_type(db, logical_schema, table)
    if object_type == "BASE TABLE":
        return _TableTarget(table, None, table, False)
    if object_type != "VIEW":
        raise RuntimeError(
            f"Schema migration requires `{table}` to exist; found {object_type or 'missing'}."
        )

    definition = (await db.execute(text("""
        SELECT VIEW_DEFINITION
        FROM information_schema.views
        WHERE table_schema = :schema AND table_name = :table
    """), {"schema": logical_schema, "table": table})).scalar_one_or_none()
    source = _FORWARDING_VIEW_SOURCE.search(definition or "")
    if source is None:
        raise RuntimeError(
            f"Schema migration cannot resolve base table for view `{table}`. "
            "Only simple cross-schema forwarding views are supported."
        )

    physical_schema = source.group("schema")
    physical_table = source.group("table")
    physical_type = await _object_type(db, physical_schema, physical_table)
    if physical_type != "BASE TABLE":
        raise RuntimeError(
            f"Schema migration resolved `{table}` to `{physical_schema}`.`{physical_table}`, "
            f"but found {physical_type or 'missing'} instead of BASE TABLE."
        )
    return _TableTarget(table, physical_schema, physical_table, True)


async def _refresh_forwarding_view(db, target: _TableTarget) -> None:
    """Expose newly added physical columns through a simple compatibility view."""
    if not target.is_view or target.physical_schema is None:
        return
    logical_schema = await _current_schema(db)
    await db.execute(text(
        "CREATE OR REPLACE VIEW "
        f"{_quoted_identifier(logical_schema)}.{_quoted_identifier(target.logical_table)} AS "
        "SELECT * FROM "
        f"{_quoted_identifier(target.physical_schema)}.{_quoted_identifier(target.physical_table)}"
    ))


async def _inspection(
    db, table: str, schema: str | None = None
) -> tuple[set[str], set[str]]:
    def inspect_schema(sync_session):
        inspector = inspect(sync_session.connection())
        columns = {item["name"] for item in inspector.get_columns(table, schema=schema)}
        indexes = {item["name"] for item in inspector.get_indexes(table, schema=schema)}
        return columns, indexes

    return await db.run_sync(inspect_schema)


async def migrate() -> None:
    if engine.dialect.name != "mysql":
        raise RuntimeError(f"MySQL migration refused for dialect: {engine.dialect.name}")

    async with SessionLocal() as db:
        acquired = (await db.execute(
            text("SELECT GET_LOCK('vllm_dashboard_schema_migration', 30)")
        )).scalar_one()
        if acquired != 1:
            raise RuntimeError("Could not acquire MySQL schema migration lock")

        try:
            user_count_before = int((await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one())
            added: list[str] = []
            for table, definitions in TABLE_COLUMN_MIGRATIONS.items():
                target = await _resolve_table_target(db, table)
                columns, indexes = await _inspection(
                    db, target.physical_table, target.physical_schema
                )
                added_to_target = False
                for name, definition in definitions.items():
                    if name not in columns:
                        logger.info("Adding %s.%s", table, name)
                        physical_name = ".".join(
                            part for part in (
                                _quoted_identifier(target.physical_schema)
                                if target.physical_schema else None,
                                _quoted_identifier(target.physical_table),
                            ) if part is not None
                        )
                        await db.execute(text(
                            f"ALTER TABLE {physical_name} ADD COLUMN {_quoted_identifier(name)} {definition}"
                        ))
                        added.append(f"{table}.{name}")
                        added_to_target = True
                for index_name, column_name in INDEX_MIGRATIONS.get(table, {}).items():
                    if index_name not in indexes:
                        logger.info("Creating index %s", index_name)
                        physical_name = ".".join(
                            part for part in (
                                _quoted_identifier(target.physical_schema)
                                if target.physical_schema else None,
                                _quoted_identifier(target.physical_table),
                            ) if part is not None
                        )
                        await db.execute(text(
                            f"CREATE INDEX {_quoted_identifier(index_name)} ON {physical_name} "
                            f"({_quoted_identifier(column_name)})"
                        ))
                if added_to_target:
                    await _refresh_forwarding_view(db, target)

            if any(item in added for item in (
                "test_cases.lifetime_runs", "test_cases.lifetime_failures"
            )):
                logger.info("Backfilling lifetime counters from retained test_runs")
                await db.execute(text("""
                    UPDATE test_cases tc
                    LEFT JOIN (
                        SELECT test_case_id,
                               COUNT(*) AS run_count,
                               SUM(CASE WHEN result = 'failed' THEN 1 ELSE 0 END) AS failure_count
                        FROM test_runs
                        GROUP BY test_case_id
                    ) totals ON totals.test_case_id = tc.id
                    SET tc.lifetime_runs = COALESCE(totals.run_count, 0),
                        tc.lifetime_failures = COALESCE(totals.failure_count, 0)
                """))

            model_fo_target = await _resolve_table_target(db, "model_fo_master")
            model_fo_schema = model_fo_target.physical_schema or await _current_schema(db)
            model_fo_nullable = (await db.execute(text("""
                SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table AND COLUMN_NAME = 'model_fo'
            """), {"schema": model_fo_schema, "table": model_fo_target.physical_table})).scalar_one()
            if model_fo_nullable == "NO":
                table = f"{_quoted_identifier(model_fo_schema)}.{_quoted_identifier(model_fo_target.physical_table)}"
                await db.execute(text(f"ALTER TABLE {table} MODIFY COLUMN model_fo VARCHAR(100) NULL"))
                await _refresh_forwarding_view(db, model_fo_target)

            # 唯一索引替换（drop 旧 + add 新），幂等：drop 缺失索引忽略，add 已存在索引忽略
            for repl in INDEX_REPLACEMENTS:
                _, indexes = await _inspection(db, repl["table"])
                if repl["drop"] in indexes:
                    logger.info("Dropping index %s on %s", repl["drop"], repl["table"])
                    await db.execute(text(
                        f"ALTER TABLE `{repl['table']}` DROP INDEX `{repl['drop']}`"
                    ))
                if repl["add"] not in indexes:
                    logger.info("Adding unique index %s on %s", repl["add"], repl["table"])
                    await db.execute(text(
                        f"ALTER TABLE `{repl['table']}` ADD UNIQUE INDEX `{repl['add']}` {repl['columns']}"
                    ))
                    added.append(f"{repl['table']}.{repl['add']}")

            # 整表新建
            for ddl in CREATE_TABLE_MIGRATIONS:
                await db.execute(text(ddl))

            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(100) PRIMARY KEY,
                    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    description VARCHAR(500) NOT NULL
                ) ENGINE=InnoDB
            """))
            await db.execute(text("""
                INSERT INTO schema_migrations (version, description)
                VALUES (:version, :description)
                ON DUPLICATE KEY UPDATE description = VALUES(description)
            """), {
                "version": MIGRATION_VERSION,
                "description": "Add workflow name matching and automatic failure analysis control",
            })
            await db.commit()

            user_count_after = int((await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one())
            if user_count_after != user_count_before:
                raise RuntimeError(
                    f"User count changed during migration: {user_count_before} -> {user_count_after}"
                )

            missing: list[str] = []
            for table, definitions in TABLE_COLUMN_MIGRATIONS.items():
                final_columns, final_indexes = await _inspection(db, table)
                missing.extend(
                    f"{table}.{name}" for name in set(definitions) - final_columns
                )
                missing.extend(
                    f"{table}.{name}" for name in set(INDEX_MIGRATIONS.get(table, {})) - final_indexes
                )
            # 校验替换后的新唯一索引确实存在
            for repl in INDEX_REPLACEMENTS:
                _, final_indexes = await _inspection(db, repl["table"])
                if repl["add"] not in final_indexes:
                    missing.append(f"{repl['table']}.{repl['add']}")
            # 校验新建表确实存在
            for ddl in CREATE_TABLE_MIGRATIONS:
                m = re.search(r"CREATE TABLE IF NOT EXISTS `(\w+)`", ddl)
                if m:
                    name = m.group(1)
                    exists = (await db.execute(text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() AND table_name = :t"
                    ), {"t": name})).scalar_one()
                    if not exists:
                        missing.append(name)
            if missing:
                raise RuntimeError(f"Migration verification failed; missing: {sorted(missing)}")
            logger.info(
                "Migration %s complete; users=%s; added=%s",
                MIGRATION_VERSION,
                user_count_after,
                ",".join(added) or "none",
            )
        finally:
            await db.execute(text("SELECT RELEASE_LOCK('vllm_dashboard_schema_migration')"))


async def main() -> None:
    try:
        await migrate()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
