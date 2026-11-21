"""GitHub Actions 用户操作入口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, null, select, union_all
from sqlalchemy.exc import IntegrityError

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from infrastructure.persistence.models import (
    User,
    WorkflowActionLog,
    WorkflowActionPermission,
    WorkflowActionSchedule,
    WorkflowSchedulePermission,
)
from workflow_actions.credentials import (
    clear_github_token,
    get_credential_status,
    save_github_token,
)
from workflow_actions.schedule_schemas import (
    SCHEDULE_TIMEZONE,
    WorkflowScheduleCreate,
    WorkflowScheduleEnabledUpdate,
    WorkflowScheduleUpdate,
    cron_preview,
    cron_trigger,
)
from workflow_actions.schedules import (
    ScheduleRunningError,
    create_schedule,
    has_schedule_permission,
    set_schedule_enabled,
    trigger_schedule,
    update_schedule,
)
from workflow_actions.schemas import (
    WorkflowActionCredentialUpdate,
    WorkflowActionPermissionCreate,
    WorkflowActionPermissionUpdate,
    WorkflowActionRequest,
)
from workflow_actions.service import execute_workflow_action, test_github_credential

router = APIRouter()


@router.get("/permissions")
async def list_my_permissions(db: DbSession, current_user: CurrentUser):
    result = await db.execute(
        select(WorkflowActionPermission.workflow_path)
        .where(WorkflowActionPermission.user_id == current_user.id)
        .order_by(WorkflowActionPermission.workflow_path)
    )
    return {"items": list(result.scalars().all())}


@router.post("/execute")
async def execute_action(
    request: WorkflowActionRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        return await execute_workflow_action(db, current_user, request)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/logs")
async def list_action_logs(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    total = await db.scalar(select(func.count(WorkflowActionLog.id)))
    result = await db.execute(
        select(WorkflowActionLog)
        .order_by(WorkflowActionLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": item.id,
                "username": item.username,
                "command_text": item.command_text,
                "created_at": item.created_at,
            }
            for item in result.scalars().all()
        ],
    }


def _schedule_dict(schedule: WorkflowActionSchedule) -> dict:
    preview_expression = None
    next_run_at = None
    try:
        trigger = cron_trigger(schedule.cron_expression, schedule.timezone or SCHEDULE_TIMEZONE)
        preview_expression = cron_preview(schedule.cron_expression)
        if schedule.enabled:
            next_run_at = trigger.get_next_fire_time(None, datetime.now(UTC))
    except ValueError:
        # Keep legacy tasks with invalid stored expressions visible for editing.
        pass
    return {
        "id": schedule.id,
        "name": schedule.name,
        "workflow_path": schedule.workflow_path,
        "ref": schedule.ref,
        "inputs_yaml": schedule.inputs_yaml,
        "cron_expression": schedule.cron_expression,
        "cron_preview_expression": preview_expression,
        "timezone": schedule.timezone,
        "enabled": schedule.enabled,
        "setter_username": schedule.setter_username,
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
        "last_run_at": schedule.last_run_at,
        "last_output": schedule.last_output,
        "last_submission_status": schedule.last_submission_status,
        "next_run_at": next_run_at,
    }


@router.get("/schedules")
async def list_schedules(db: DbSession, current_user: CurrentUser):
    result = await db.execute(
        select(WorkflowActionSchedule).order_by(WorkflowActionSchedule.id.desc())
    )
    return {"items": [_schedule_dict(item) for item in result.scalars().all()]}


@router.get("/schedules/cron-preview")
async def preview_schedule_cron(
    current_user: CurrentUser,
    expression: str = Query(min_length=5, max_length=100),
):
    try:
        cron_trigger(expression)
        return {"expression": cron_preview(expression)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule_endpoint(
    request: WorkflowScheduleCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        schedule = await create_schedule(db, current_user, request)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _schedule_dict(schedule)


@router.put("/schedules/{schedule_id}")
async def update_schedule_endpoint(
    schedule_id: int,
    request: WorkflowScheduleUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        schedule = await update_schedule(db, current_user, schedule_id, request)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定时任务不存在")
    return _schedule_dict(schedule)


@router.patch("/schedules/{schedule_id}/enabled")
async def set_schedule_enabled_endpoint(
    schedule_id: int,
    request: WorkflowScheduleEnabledUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        schedule = await set_schedule_enabled(db, current_user, schedule_id, request.enabled)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定时任务不存在")
    return _schedule_dict(schedule)


@router.post("/schedules/{schedule_id}/trigger")
async def trigger_schedule_endpoint(
    schedule_id: int,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        result = await trigger_schedule(db, current_user, schedule_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ScheduleRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定时任务不存在")
    return result


@router.get("/schedule-permission")
async def get_my_schedule_permission(db: DbSession, current_user: CurrentUser):
    return {"allowed": await has_schedule_permission(db, current_user)}


@router.get("/admin/credential")
async def get_actions_credential(db: DbSession, current_user: CurrentAdminUser):
    return await get_credential_status(db)


@router.put("/admin/credential")
async def save_actions_credential(
    request: WorkflowActionCredentialUpdate,
    db: DbSession,
    current_user: CurrentAdminUser,
):
    try:
        await save_github_token(db, request.token, str(current_user.username))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await get_credential_status(db)


@router.delete("/admin/credential")
async def delete_actions_credential(db: DbSession, current_user: CurrentAdminUser):
    await clear_github_token(db, str(current_user.username))
    return await get_credential_status(db)


@router.post("/admin/credential/test")
async def test_actions_credential(db: DbSession, current_user: CurrentAdminUser):
    return await test_github_credential(db)


@router.get("/admin/permissions")
async def list_permissions(db: DbSession, current_user: CurrentAdminUser):
    grants = union_all(
        select(
            User.username,
            WorkflowActionPermission.workflow_path,
            WorkflowActionPermission.granted_by,
            WorkflowActionPermission.created_at,
        ).join(WorkflowActionPermission, WorkflowActionPermission.user_id == User.id),
        select(
            User.username,
            null().label("workflow_path"),
            WorkflowSchedulePermission.granted_by,
            WorkflowSchedulePermission.created_at,
        ).join(WorkflowSchedulePermission, WorkflowSchedulePermission.user_id == User.id),
    ).subquery()
    result = await db.execute(
        select(grants).order_by(grants.c.username, grants.c.created_at.desc())
    )
    users: dict[str, dict] = {}
    for username, workflow_path, granted_by, created_at in result:
        if username not in users:
            users[username] = {
                "username": username,
                "workflow_paths": [],
                "schedule_permission": False,
                "granted_by": granted_by,
                "created_at": created_at,
            }
        if workflow_path is None:
            users[username]["schedule_permission"] = True
        else:
            users[username]["workflow_paths"].append(workflow_path)
    return {"items": list(users.values())}


@router.post("/admin/permissions")
async def create_permission(
    request: WorkflowActionPermissionCreate,
    db: DbSession,
    current_user: CurrentAdminUser,
):
    result = await db.execute(
        select(User).where(User.username.in_(request.usernames)).order_by(User.id).with_for_update()
    )
    users = result.scalars().all()
    if len(users) != len(request.usernames):
        found_usernames = {user.username for user in users}
        missing = sorted(set(request.usernames) - found_usernames)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"用户不存在：{', '.join(missing)}",
        )

    created_count = 0
    schedule_created_count = 0
    for user in users:
        result = await db.execute(
            select(WorkflowActionPermission.workflow_path).where(
                WorkflowActionPermission.user_id == user.id
            )
        )
        existing_paths = {str(path) for path in result.scalars().all()}
        for path in request.workflow_paths:
            if path not in existing_paths:
                db.add(
                    WorkflowActionPermission(
                        user_id=user.id,
                        workflow_path=path,
                        granted_by=current_user.username,
                    )
                )
                existing_paths.add(path)
                created_count += 1

        if request.schedule_permission:
            schedule_permission = await db.scalar(
                select(WorkflowSchedulePermission).where(
                    WorkflowSchedulePermission.user_id == user.id
                )
            )
            if schedule_permission is None:
                db.add(
                    WorkflowSchedulePermission(
                        user_id=user.id,
                        granted_by=current_user.username,
                    )
                )
                schedule_created_count += 1

    skipped_count = len(users) * len(request.workflow_paths) - created_count
    schedule_skipped_count = (
        len(users) - schedule_created_count if request.schedule_permission else 0
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="部分授权已存在，请刷新后重试"
        ) from exc
    return {
        "user_count": len(users),
        "created_count": created_count,
        "skipped_count": skipped_count,
        "schedule_created_count": schedule_created_count,
        "schedule_skipped_count": schedule_skipped_count,
        "message": f"已为 {len(users)} 个用户添加授权",
    }


@router.put("/admin/permissions/users/{username}")
async def update_user_permissions(
    username: str,
    request: WorkflowActionPermissionUpdate,
    db: DbSession,
    current_user: CurrentAdminUser,
):
    user = await db.scalar(select(User).where(User.username == username).with_for_update())
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    result = await db.execute(
        select(WorkflowActionPermission).where(WorkflowActionPermission.user_id == user.id)
    )
    existing = {item.workflow_path: item for item in result.scalars().all()}
    requested_paths = set(request.workflow_paths)

    removed_count = 0
    for existing_path, permission in existing.items():
        if existing_path not in requested_paths:
            await db.delete(permission)
            removed_count += 1

    added_count = 0
    for requested_path in request.workflow_paths:
        if requested_path not in existing:
            db.add(
                WorkflowActionPermission(
                    user_id=user.id,
                    workflow_path=requested_path,
                    granted_by=current_user.username,
                )
            )
            added_count += 1

    if request.schedule_permission is not None:
        permission = await db.scalar(
            select(WorkflowSchedulePermission).where(WorkflowSchedulePermission.user_id == user.id)
        )
        if request.schedule_permission and permission is None:
            db.add(WorkflowSchedulePermission(user_id=user.id, granted_by=current_user.username))
        elif not request.schedule_permission and permission is not None:
            await db.delete(permission)

    await db.commit()
    return {
        "added_count": added_count,
        "removed_count": removed_count,
        "message": "用户授权已更新",
    }
