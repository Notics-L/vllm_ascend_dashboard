"""Durable configuration channel from the API control plane to Scheduler."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import ProjectDashboardConfig

SCHEDULER_RUNTIME_CONFIG_KEY = "scheduler_runtime_config"

# Only scheduling inputs belong in this cross-process configuration document.
SCHEDULER_RUNTIME_FIELDS = frozenset(
    {
        "ci_sync_interval_minutes",
        "ci_sync_days_back",
        "ci_sync_max_runs_per_workflow",
        "ci_sync_force_full_refresh",
        "ci_auto_failure_analysis_enabled",
        "model_sync_interval_minutes",
        "model_sync_days_back",
        "model_sync_runs_limit",
        "pr_pipeline_sync_interval_minutes",
        "pr_pipeline_days_back",
        "pr_pipeline_max_items_per_sync",
        "pr_pipeline_incremental_lookback_minutes",
        "project_dashboard_cache_interval_minutes",
        "data_retention_days",
        "github_cache_dir",
    }
)

SCHEDULER_RUNTIME_SETTING_MAP = {
    "ci_sync_interval_minutes": "CI_SYNC_INTERVAL_MINUTES",
    "ci_sync_days_back": "CI_SYNC_DAYS_BACK",
    "ci_sync_max_runs_per_workflow": "CI_SYNC_MAX_RUNS_PER_WORKFLOW",
    "ci_sync_force_full_refresh": "CI_SYNC_FORCE_FULL_REFRESH",
    "ci_auto_failure_analysis_enabled": "CI_AUTO_FAILURE_ANALYSIS_ENABLED",
    "model_sync_interval_minutes": "MODEL_SYNC_INTERVAL_MINUTES",
    "model_sync_days_back": "MODEL_SYNC_DAYS_BACK",
    "model_sync_runs_limit": "MODEL_SYNC_RUNS_LIMIT",
    "pr_pipeline_sync_interval_minutes": "PR_PIPELINE_SYNC_INTERVAL_MINUTES",
    "pr_pipeline_days_back": "PR_PIPELINE_DAYS_BACK",
    "pr_pipeline_max_items_per_sync": "PR_PIPELINE_MAX_ITEMS_PER_SYNC",
    "pr_pipeline_incremental_lookback_minutes": "PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES",
    "project_dashboard_cache_interval_minutes": "PROJECT_DASHBOARD_CACHE_INTERVAL_MINUTES",
    "data_retention_days": "DATA_RETENTION_DAYS",
    "github_cache_dir": "GITHUB_CACHE_DIR",
}


async def load_scheduler_runtime_config(db: AsyncSession | None = None) -> dict[str, Any]:
    """Load scheduler-owned runtime settings into the current process."""
    if db is not None:
        result = await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == SCHEDULER_RUNTIME_CONFIG_KEY
            )
        )
        row = result.scalar_one_or_none()
        config = dict(row.config_value or {}) if row else {}
    else:
        async with SessionLocal() as session:
            return await load_scheduler_runtime_config(session)

    for config_key, setting_name in SCHEDULER_RUNTIME_SETTING_MAP.items():
        if config_key in config:
            setattr(settings, setting_name, config[config_key])
    return config


async def persist_scheduler_runtime_config(overrides: dict[str, Any]) -> None:
    """Persist scheduler inputs and emit a durable reload command.

    The API never mutates another process's APScheduler object.  The Scheduler
    consumes this command from MySQL and reloads its own local schedule.
    """
    values = {key: value for key, value in overrides.items() if key in SCHEDULER_RUNTIME_FIELDS}
    if not values:
        return

    async with SessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == SCHEDULER_RUNTIME_CONFIG_KEY
                )
            )
            row = result.scalar_one_or_none()
            config = dict(row.config_value or {}) if row else {}
            config.update(values)
            if row is None:
                db.add(
                    ProjectDashboardConfig(
                        config_key=SCHEDULER_RUNTIME_CONFIG_KEY,
                        config_value=config,
                        description="Scheduler runtime configuration owned by the control plane",
                    )
                )
            else:
                row.config_value = config

            version_result = await db.execute(
                text(
                    """
                    SELECT aggregate_version
                    FROM control_outbox
                    WHERE aggregate_type = 'scheduler'
                      AND aggregate_id = 'runtime-config'
                      AND event_type = 'scheduler.config.reload'
                    ORDER BY aggregate_version DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                )
            )
            current_version = version_result.scalar_one_or_none() or 0

            await db.execute(
                text(
                    """
                    INSERT INTO control_outbox
                        (event_id, aggregate_type, aggregate_id, event_type, aggregate_version, payload)
                    VALUES
                        (:event_id, 'scheduler', 'runtime-config', 'scheduler.config.reload', :version, :payload)
                    """
                ),
                {
                    "event_id": str(uuid.uuid4()),
                    "version": int(current_version) + 1,
                    "payload": json.dumps({"changed": sorted(values)}),
                },
            )


async def emit_scheduler_reload(db: AsyncSession) -> None:
    """Notify Scheduler that database-backed Workflow schedules changed."""
    await db.execute(
        text(
            """
            INSERT INTO control_outbox
                (event_id, aggregate_type, aggregate_id, event_type, aggregate_version, payload)
            VALUES
                (:event_id, 'scheduler', 'workflow-actions', 'scheduler.config.reload', :version, :payload)
            """
        ),
        {
            "event_id": str(uuid.uuid4()),
            "version": int(uuid.uuid4().int >> 65),
            "payload": json.dumps({"changed": ["workflow_action_schedules"]}),
        },
    )
