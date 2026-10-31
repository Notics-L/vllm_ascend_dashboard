"""Canonical database migration command for the dashboard.

This command is safe to repeat: it creates missing current-schema tables and
then applies the explicit MySQL compatibility and task-queue migrations. It
never creates, resets, or deletes application users.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text

repository_root = Path(__file__).resolve().parents[2]
# Source checkouts keep application code under ``backend/``; the production
# image copies it directly to ``/app``. Database code keeps the same package
# path in both layouts so the migration entrypoint is deterministic.
application_root = repository_root / "backend"
if not application_root.is_dir():
    application_root = repository_root
sys.path.insert(0, str(application_root))

from database.bootstrap import create_tables_with_latest_schema
from database.migrations.ci_npu_facts import run as migrate_ci_npu_facts
from database.migrations.ci_npu_queue_snapshot_physical_running import (
    run as migrate_ci_npu_queue_snapshot_physical_running,
)
from database.migrations.ci_npu_queue_snapshots import run as migrate_ci_npu_queue_snapshots
from database.migrations.github_workflow_run_metadata import (
    run as migrate_github_workflow_run_metadata,
)
from database.migrations.model_fo_mapping_seed import run as migrate_model_fo_mapping_seed
from database.migrations.mysql_schema import migrate as migrate_mysql_schema
from database.migrations.npu_occupancy import run as migrate_npu_occupancy
from database.migrations.npu_occupancy_current_snapshot import (
    run as migrate_npu_occupancy_current_snapshot,
)
from database.migrations.npu_occupancy_pod_lookup import run as migrate_npu_occupancy_pod_lookup
from database.migrations.process_runtime import run as migrate_process_runtime
from database.migrations.resource_metrics_query_indexes import (
    run as migrate_resource_metrics_query_indexes,
)
from database.migrations.resource_pod_timeline import run as migrate_resource_pod_timeline
from database.migrations.service_permissions import run as migrate_service_permissions
from database.migrations.task_queue import run as migrate_phase_a
from database.migrations.contributor_board import run as migrate_contributor_board
from database.migrations.contributor_board_enhancements import run as migrate_contributor_board_enhancements
from database.migrations.contributor_board_settings import run as migrate_contributor_board_settings
from database.migrations.contributor_metrics_add_commit_count import run as migrate_commit_count
from database.migrations.contributor_metrics_pr_efficiency import run as migrate_pr_efficiency
from database.migrations.contributor_board_repo_name import run as migrate_contributor_board_repo_name
from database.migrations.contributor_sync_records import run as migrate_contributor_sync_records
from database.migrations.image_build_plans_reasoning import run as migrate_image_build_plans_reasoning
from database.migrations.image_build_plans_files_changed import run as migrate_image_build_plans_files_changed
from database.migrations.test_board_data import run as migrate_test_board_data
from database.migrations.contributor_scoring_version import run as migrate_contributor_scoring_version
from database.migrations.contributor_ai_report import run as migrate_contributor_ai_report
from database.migrations.release_analysis_task_columns import run as migrate_release_analysis_task_columns
from database.migrations.release_build_groups_columns import run as migrate_release_build_groups_columns
from infrastructure.db.base import SessionLocal, engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("database_migration")


async def _user_count() -> int:
    async with SessionLocal() as db:
        return int((await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one())


async def migrate() -> None:
    if engine.dialect.name != "mysql":
        raise RuntimeError(f"Production migration requires MySQL, got {engine.dialect.name}")

    await create_tables_with_latest_schema()
    users_before = await _user_count()
    # phase_a creates migration_history first, so it must run before other migrations
    await migrate_phase_a()
    await migrate_mysql_schema()
    await migrate_process_runtime()
    model_fo_result = await migrate_model_fo_mapping_seed()
    permission_result = await migrate_service_permissions()
    await migrate_npu_occupancy()
    await migrate_npu_occupancy_current_snapshot()
    await migrate_npu_occupancy_pod_lookup()
    await migrate_github_workflow_run_metadata()
    await migrate_resource_metrics_query_indexes()
    await migrate_resource_pod_timeline()
    await migrate_ci_npu_facts()
    await migrate_ci_npu_queue_snapshots()
    await migrate_ci_npu_queue_snapshot_physical_running()
    test_board_result = await migrate_test_board_data()
    contributor_board_result = await migrate_contributor_board()
    contributor_board_enhancements_result = await migrate_contributor_board_enhancements()
    contributor_board_settings_result = await migrate_contributor_board_settings()
    commit_count_result = await migrate_commit_count()
    pr_efficiency_result = await migrate_pr_efficiency()
    contributor_board_repo_name_result = await migrate_contributor_board_repo_name()
    contributor_sync_records_result = await migrate_contributor_sync_records()
    build_plan_reasoning_result = await migrate_image_build_plans_reasoning()
    build_plan_files_changed_result = await migrate_image_build_plans_files_changed()
    scoring_version_result = await migrate_contributor_scoring_version()
    ai_report_result = await migrate_contributor_ai_report()
    release_task_result = await migrate_release_analysis_task_columns()
    release_groups_result = await migrate_release_build_groups_columns()
    users_after = await _user_count()
    if users_after != users_before:
        raise RuntimeError(
            f"User count changed during migration: {users_before} -> {users_after}"
        )
    logger.info(
        "Migration completed; users=%d permissions=%s test_board=%s contributor_board=%s enhancements=%s settings=%s commit_count=%s pr_efficiency=%s repo_name=%s sync_records=%s build_plan_reasoning=%s build_plan_files_changed=%s scoring_version=%s ai_report=%s release_task=%s release_groups=%s",
        users_after,
        permission_result,
        test_board_result,
        contributor_board_result,
        contributor_board_enhancements_result,
        contributor_board_settings_result,
        commit_count_result,
        pr_efficiency_result,
        contributor_board_repo_name_result,
        contributor_sync_records_result,
        build_plan_reasoning_result,
        build_plan_files_changed_result,
        scoring_version_result,
        ai_report_result,
        release_task_result,
        release_groups_result,
    )


async def main() -> None:
    try:
        await migrate()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
