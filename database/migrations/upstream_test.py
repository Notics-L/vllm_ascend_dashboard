"""Migration: upstream test tables.

上游测试模块每次同步会写入一份不可变快照，快照本身以 JSON 保存原始行，
同时展开到两张明细表用于聚合查询。
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger("upstream_test_migration")
MIGRATION_VERSION = "20261123_01_upstream_test"

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
        if not await _table_exists(db, "upstream_test_snapshots"):
            await db.execute(text("""
                CREATE TABLE upstream_test_snapshots (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source_repo VARCHAR(200) NOT NULL DEFAULT 'vllm-ascend/upstream-test',
                    source_commit VARCHAR(64) DEFAULT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'success',
                    error_message TEXT DEFAULT NULL,
                    warnings JSON DEFAULT NULL,
                    weekly_file VARCHAR(255) DEFAULT NULL,
                    weekly_summary JSON DEFAULT NULL,
                    version_name VARCHAR(64) DEFAULT NULL,
                    version_file VARCHAR(255) DEFAULT NULL,
                    version_summary JSON DEFAULT NULL,
                    issue_summary JSON DEFAULT NULL,
                    ci_summary JSON DEFAULT NULL,
                    weekly_cases JSON DEFAULT NULL,
                    version_cases JSON DEFAULT NULL,
                    issues JSON DEFAULT NULL,
                    KEY ix_upstream_snapshot_fetched (fetched_at),
                    KEY ix_upstream_snapshot_status (status),
                    KEY ix_upstream_snapshot_version (version_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COMMENT='上游测试结果快照（每日同步）'
            """))
            created["upstream_test_snapshots"] = 1
            logger.info("Created upstream_test_snapshots")

        if not await _table_exists(db, "upstream_test_case_results"):
            await db.execute(text("""
                CREATE TABLE upstream_test_case_results (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    snapshot_id BIGINT NOT NULL,
                    scope VARCHAR(20) NOT NULL,
                    version_name VARCHAR(64) DEFAULT NULL,
                    test_path VARCHAR(500) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    snippet TEXT DEFAULT NULL,
                    analysis TEXT DEFAULT NULL,
                    category VARCHAR(100) DEFAULT NULL,
                    case_version VARCHAR(64) DEFAULT NULL,
                    removed VARCHAR(20) DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY ix_upstream_case_snapshot_scope (snapshot_id, scope),
                    KEY ix_upstream_case_status (status),
                    KEY ix_upstream_case_category (category),
                    KEY ix_upstream_case_path (test_path(191)),
                    KEY ix_upstream_case_version (version_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COMMENT='上游测试用例明细'
            """))
            created["upstream_test_case_results"] = 1
            logger.info("Created upstream_test_case_results")

        if not await _table_exists(db, "upstream_test_issues"):
            await db.execute(text("""
                CREATE TABLE upstream_test_issues (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    snapshot_id BIGINT NOT NULL,
                    state VARCHAR(20) NOT NULL,
                    issue_number INT DEFAULT NULL,
                    issue_url VARCHAR(500) DEFAULT NULL,
                    issue_title VARCHAR(1000) DEFAULT NULL,
                    test_path VARCHAR(500) DEFAULT NULL,
                    impact_level VARCHAR(100) DEFAULT NULL,
                    impact TEXT DEFAULT NULL,
                    notes TEXT DEFAULT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY ix_upstream_issue_snapshot_state (snapshot_id, state),
                    KEY ix_upstream_issue_number (issue_number),
                    KEY ix_upstream_issue_path (test_path(191))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COMMENT='上游测试关联 issue'
            """))
            created["upstream_test_issues"] = 1
            logger.info("Created upstream_test_issues")

        if await _table_exists(db, "upstream_test_snapshots"):
            for column, ddl in (
                ("warnings", "JSON DEFAULT NULL"),
                ("weekly_failure_reasons", "JSON DEFAULT NULL"),
                ("previous_version_name", "VARCHAR(64) DEFAULT NULL"),
                ("version_failure_reasons", "JSON DEFAULT NULL"),
                ("issue_added_count", "INT DEFAULT NULL"),
                ("upstream_tests", "JSON DEFAULT NULL"),
            ):
                if not await _column_exists(db, "upstream_test_snapshots", column):
                    await db.execute(
                        text(
                            f"ALTER TABLE upstream_test_snapshots ADD COLUMN {column} {ddl}"
                        )
                    )
                    created[f"upstream_test_snapshots.{column}"] = 1
                    logger.info("Added upstream_test_snapshots.%s", column)

        if created:
            await db.commit()
    return created
