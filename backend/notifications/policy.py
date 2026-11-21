"""通知策略配置：静默时段、冷却、告警风暴抑制与升级链。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from infrastructure.persistence.models import ProjectDashboardConfig

POLICY_CONFIG_KEY = "notification_policy_config"

DEFAULT_POLICY: dict[str, Any] = {
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "cooldown_minutes": 0,
    "storm_window_minutes": 10,
    "storm_threshold": 20,
    "escalation_enabled": False,
    "escalation_group_ids": [],
    "oncall_group_id": None,
}

_PUBLIC_FIELDS = set(DEFAULT_POLICY)


async def get_policy_config(db) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == POLICY_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    return {**DEFAULT_POLICY, **stored}


async def save_policy_config(db, payload: dict[str, Any]) -> dict[str, Any]:
    row = (
        await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == POLICY_CONFIG_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.config_value) if (row and row.config_value) else {}
    for key in _PUBLIC_FIELDS:
        if key in payload:
            stored[key] = payload[key]
    if row is None:
        row = ProjectDashboardConfig(
            config_key=POLICY_CONFIG_KEY,
            config_value=stored,
            description="通知策略配置",
        )
        db.add(row)
    else:
        row.config_value = stored
    await db.flush()
    return await get_policy_config(db)
