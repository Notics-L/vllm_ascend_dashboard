"""数据库持久化的 Workflow 定时任务。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import (
    User,
    WorkflowActionSchedule,
    WorkflowSchedulePermission,
)
from infrastructure.tasks.scheduler_config import emit_scheduler_reload
from workflow_actions.schedule_schemas import (
    SCHEDULE_TIMEZONE,
    WorkflowScheduleCreate,
    WorkflowScheduleUpdate,
)
from workflow_actions.schemas import WorkflowActionRequest
from workflow_actions.service import execute_workflow_action, parse_workflow_inputs

logger = logging.getLogger(__name__)


class ScheduleRunningError(RuntimeError):
    pass


async def _acquire_schedule(db: AsyncSession, schedule_id: int) -> bool:
    result = await db.execute(
        update(WorkflowActionSchedule)
        .where(
            WorkflowActionSchedule.id == schedule_id,
            or_(
                WorkflowActionSchedule.running_at.is_(None),
                WorkflowActionSchedule.running_at
                < func.date_sub(func.utc_timestamp(), text("INTERVAL 15 MINUTE")),
            ),
        )
        .values(running_at=func.utc_timestamp())
    )
    await db.commit()
    return int(getattr(result, "rowcount", 0)) == 1


async def has_schedule_permission(db: AsyncSession, user: User) -> bool:
    result = await db.execute(
        select(WorkflowSchedulePermission.id).where(WorkflowSchedulePermission.user_id == user.id)
    )
    return result.scalar_one_or_none() is not None


async def require_schedule_permission(db: AsyncSession, user: User) -> None:
    if not await has_schedule_permission(db, user):
        raise PermissionError("无定时任务权限")


async def create_schedule(
    db: AsyncSession,
    user: User,
    request: WorkflowScheduleCreate,
) -> WorkflowActionSchedule:
    await require_schedule_permission(db, user)
    parse_workflow_inputs(request.inputs_yaml)
    schedule = WorkflowActionSchedule(
        **request.model_dump(),
        timezone=SCHEDULE_TIMEZONE,
        setter_user_id=user.id,
        setter_username=user.username,
    )
    db.add(schedule)
    try:
        await db.flush()
        await emit_scheduler_reload(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(schedule)
    return schedule


async def update_schedule(
    db: AsyncSession,
    user: User,
    schedule_id: int,
    request: WorkflowScheduleUpdate,
) -> WorkflowActionSchedule | None:
    await require_schedule_permission(db, user)
    parse_workflow_inputs(request.inputs_yaml)
    schedule = await db.get(WorkflowActionSchedule, schedule_id)
    if schedule is None:
        return None
    for field, value in request.model_dump().items():
        setattr(schedule, field, value)
    schedule.setter_user_id = user.id
    schedule.setter_username = user.username
    try:
        await emit_scheduler_reload(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return schedule


async def set_schedule_enabled(
    db: AsyncSession,
    user: User,
    schedule_id: int,
    enabled: bool,
) -> WorkflowActionSchedule | None:
    await require_schedule_permission(db, user)
    schedule = await db.get(WorkflowActionSchedule, schedule_id)
    if schedule is None:
        return None
    schedule.enabled = enabled  # type: ignore[assignment]
    schedule.setter_user_id = user.id
    schedule.setter_username = user.username
    try:
        await emit_scheduler_reload(db)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return schedule


async def _record_result(
    db: AsyncSession,
    schedule: WorkflowActionSchedule,
    result: dict[str, Any],
) -> None:
    schedule.last_run_at = datetime.now(UTC)  # type: ignore[assignment]
    schedule.running_at = None  # type: ignore[assignment]
    schedule.last_output = result["output"]
    schedule.last_submission_status = result.get("submission_status") or (
        "submitted" if result.get("success") else "failed"
    )
    await db.commit()


async def trigger_schedule(
    db: AsyncSession,
    user: User,
    schedule_id: int,
) -> dict[str, Any] | None:
    await require_schedule_permission(db, user)
    schedule = await db.get(WorkflowActionSchedule, schedule_id)
    if schedule is None:
        return None
    if not await _acquire_schedule(db, schedule_id):
        raise ScheduleRunningError("该定时任务正在执行，请勿重复触发")
    try:
        result = await execute_workflow_action(
            db,
            user,
            WorkflowActionRequest(
                action="dispatch",
                workflow_path=str(schedule.workflow_path),
                ref=str(schedule.ref),
                inputs_yaml=str(schedule.inputs_yaml),
            ),
            require_workflow_permission=False,
            log_source="（立即）",
        )
    except Exception:
        logger.exception("Immediate Workflow schedule execution failed: %s", schedule_id)
        await db.rollback()
        await _record_result(
            db,
            schedule,
            {"output": "立即触发失败，请查看服务端日志"},
        )
        raise
    await _record_result(db, schedule, result)
    return result


async def run_scheduled_task(schedule_id: int) -> None:
    """Execute an enabled task without rechecking the setter's current permission."""
    async with SessionLocal() as db:
        schedule = await db.get(WorkflowActionSchedule, schedule_id)
        if schedule is None or not schedule.enabled:
            return
        if not await _acquire_schedule(db, schedule_id):
            return
        user = User(
            id=schedule.setter_user_id,
            username=schedule.setter_username,
            role="user",
            is_active=True,
        )
        try:
            result = await execute_workflow_action(
                db,
                user,
                WorkflowActionRequest(
                    action="dispatch",
                    workflow_path=str(schedule.workflow_path),
                    ref=str(schedule.ref),
                    inputs_yaml=str(schedule.inputs_yaml),
                ),
                require_workflow_permission=False,
                log_source="（定时）",
                retry_delays=(2, 5, 10),
            )
        except Exception:
            logger.exception("Scheduled Workflow execution failed: %s", schedule_id)
            await db.rollback()
            result = {
                "success": False,
                "status_code": None,
                "output": "定时触发失败，请查看服务端日志",
                "run_url": None,
            }
        await _record_result(db, schedule, result)
