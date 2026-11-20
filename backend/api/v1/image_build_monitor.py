"""镜像构建监控 API。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from image_build_monitor.service import ImageBuildMonitorService
from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.persistence.models import ImageBuildMonitorTarget, ReleaseWorkflow

router = APIRouter()


class TargetPayload(BaseModel):
    release_workflow_id: int
    enabled: bool = True
    schedule_cron: str | None = None
    timezone: str = "Asia/Shanghai"
    grace_minutes: int = Field(default=30, ge=0, le=1440)
    lookback_days: int = Field(default=2, ge=1, le=30)
    notify_emails: list[str] = Field(default_factory=list)
    notify_on_failure: bool = True
    notify_on_missing: bool = True
    notify_on_stuck: bool = True
    stuck_threshold_minutes: int = Field(default=180, ge=5, le=2880)
    cooldown_minutes: int = Field(default=60, ge=5, le=10080)


class TestEmailPayload(BaseModel):
    recipients: list[str] = Field(min_length=1)
    target_id: int | None = None


def _apply_payload(target: ImageBuildMonitorTarget, payload: TargetPayload) -> None:
    target.enabled = payload.enabled
    target.schedule_cron = (payload.schedule_cron or "").strip() or None
    target.timezone = payload.timezone or "Asia/Shanghai"
    target.grace_minutes = payload.grace_minutes
    target.lookback_days = payload.lookback_days
    target.notify_emails = [e.strip() for e in payload.notify_emails if e.strip()]
    target.notify_on_failure = payload.notify_on_failure
    target.notify_on_missing = payload.notify_on_missing
    target.notify_on_stuck = payload.notify_on_stuck
    target.stuck_threshold_minutes = payload.stuck_threshold_minutes
    target.cooldown_minutes = payload.cooldown_minutes


@router.get("/overview")
async def get_overview(
    db: DbSession,
    _: CurrentUser,
    days_back: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    return await ImageBuildMonitorService(db).get_overview(days_back)


@router.get("/github-status")
async def get_github_status(_: CurrentUser) -> dict[str, Any]:
    if not settings.GITHUB_TOKEN:
        return {"ok": False, "mode": "none", "message": "未配置 GITHUB_TOKEN"}
    client = GitHubClient(
        token=settings.GITHUB_TOKEN,
        owner=settings.GITHUB_OWNER,
        repo=settings.GITHUB_REPO,
    )
    try:
        data = await client._request("GET", "/rate_limit")
        core = (data.get("resources") or {}).get("core") or {}
        return {
            "ok": True,
            "mode": "anonymous" if not client.token else "authenticated",
            "remaining": core.get("remaining"),
            "message": "GitHub 可正常访问",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "mode": "anonymous" if not client.token else "token_invalid",
            "message": str(exc),
        }
    finally:
        await client.close()


@router.get("/targets")
async def list_targets(db: DbSession, _: CurrentUser) -> dict[str, Any]:
    return {"items": await ImageBuildMonitorService(db).list_targets()}


@router.get("/candidates")
async def list_candidates(db: DbSession, _: CurrentUser) -> dict[str, Any]:
    return {"items": await ImageBuildMonitorService(db).list_candidates()}


@router.post("/targets")
async def create_target(
    payload: TargetPayload,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    wf = await db.get(ReleaseWorkflow, payload.release_workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Release workflow 不存在")
    existing = (
        await db.execute(
            select(ImageBuildMonitorTarget).where(
                ImageBuildMonitorTarget.release_workflow_id == payload.release_workflow_id
            )
        )
    ).scalar_one_or_none()
    target = existing or ImageBuildMonitorTarget(
        release_workflow_id=payload.release_workflow_id
    )
    _apply_payload(target, payload)
    if not existing:
        db.add(target)
    await db.commit()
    await db.refresh(target)
    return {"success": True, "id": target.id}


@router.put("/targets/{target_id}")
async def update_target(
    target_id: int,
    payload: TargetPayload,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    target = await db.get(ImageBuildMonitorTarget, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="监控目标不存在")
    if target.release_workflow_id != payload.release_workflow_id:
        exists = (
            await db.execute(
                select(ImageBuildMonitorTarget).where(
                    ImageBuildMonitorTarget.release_workflow_id == payload.release_workflow_id,
                    ImageBuildMonitorTarget.id != target_id,
                )
            )
        ).scalar_one_or_none()
        if exists:
            raise HTTPException(status_code=400, detail="该 workflow 已被其他监控目标使用")
        if not await db.get(ReleaseWorkflow, payload.release_workflow_id):
            raise HTTPException(status_code=404, detail="Release workflow 不存在")
        target.release_workflow_id = payload.release_workflow_id
    _apply_payload(target, payload)
    await db.commit()
    return {"success": True}


@router.delete("/targets/{target_id}")
async def delete_target(
    target_id: int,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    target = await db.get(ImageBuildMonitorTarget, target_id)
    if not target:
        raise HTTPException(status_code=404, detail="监控目标不存在")
    await db.delete(target)
    await db.commit()
    return {"success": True}


@router.get("/targets/{target_id}/runs")
async def get_target_runs(
    target_id: int,
    db: DbSession,
    _: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {"items": await ImageBuildMonitorService(db).get_target_runs(target_id, limit)}


@router.get("/alerts")
async def list_alerts(
    db: DbSession,
    _: CurrentUser,
    status: str | None = Query(default=None),
    alert_type: str | None = Query(default=None),
    release_workflow_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await ImageBuildMonitorService(db).list_alerts(
        status=status,
        alert_type=alert_type,
        release_workflow_id=release_workflow_id,
        page=page,
        page_size=page_size,
    )


@router.get("/alerts/{alert_id}/detail")
async def get_alert_detail(
    alert_id: int,
    db: DbSession,
    _: CurrentUser,
) -> dict[str, Any]:
    detail = await ImageBuildMonitorService(db).get_alert_detail(alert_id)
    if not detail:
        raise HTTPException(status_code=404, detail="告警不存在")
    return detail


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, db: DbSession, user: CurrentAdminUser) -> dict[str, Any]:
    result = await ImageBuildMonitorService(db).ack_alert(alert_id, user.username)
    if not result:
        raise HTTPException(status_code=404, detail="告警不存在")
    return result


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, db: DbSession, user: CurrentAdminUser) -> dict[str, Any]:
    result = await ImageBuildMonitorService(db).resolve_alert(alert_id, user.username)
    if not result:
        raise HTTPException(status_code=404, detail="告警不存在")
    return result


@router.post("/alerts/{alert_id}/renotify")
async def renotify_alert(alert_id: int, db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    result = await ImageBuildMonitorService(db).renotify_alert(alert_id)
    if not result:
        raise HTTPException(status_code=404, detail="告警不存在")
    return result


@router.post("/check")
async def run_check(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return await ImageBuildMonitorService(db).evaluate_all(sync_remote=True)


@router.post("/test-email")
async def send_test_email(
    payload: TestEmailPayload,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    recipients = [e.strip() for e in payload.recipients if e.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="请至少填写一个收件邮箱")
    return await ImageBuildMonitorService(db).send_test_email(recipients, payload.target_id)
