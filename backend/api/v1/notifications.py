"""通知中心 API：联系人、联系人组、通知事件与投递记录。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from api.deps import CurrentAdminUser, DbSession
from contracts.schemas.notifications import (
    ContactCreate,
    ContactUpdate,
    GroupCreate,
    GroupUpdate,
    SmsConfigUpdate,
    PolicyUpdate,
    SmsTestRequest,
    VoiceConfigUpdate,
    VoiceTestRequest,
)
from notifications.ivr import IvrService
from notifications.policy import get_policy_config, save_policy_config
from notifications.service import NotificationDispatcher
from notifications.sms import get_public_sms_config, save_sms_config
from notifications.voice import get_public_voice_config, save_voice_config

router = APIRouter()




# ----------------------------------------------------------------------
# Policy & metrics
# ----------------------------------------------------------------------

@router.get("/policy")
async def get_policy(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return await get_policy_config(db)


@router.put("/policy")
async def update_policy(
    payload: PolicyUpdate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await save_policy_config(db, payload.model_dump(exclude_unset=True))
    await db.commit()
    return result


@router.get("/metrics")
async def get_metrics(
    db: DbSession,
    _: CurrentAdminUser,
    days: int = Query(default=7, ge=1, le=90),
) -> dict[str, Any]:
    return await NotificationDispatcher(db).get_metrics(days)

# ----------------------------------------------------------------------
# SMS channel
# ----------------------------------------------------------------------

@router.get("/channels/sms")
async def get_sms_channel(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return await get_public_sms_config(db)


@router.put("/channels/sms")
async def update_sms_channel(
    payload: SmsConfigUpdate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await save_sms_config(db, payload.model_dump(exclude_unset=True))
    await db.commit()
    return result


@router.post("/channels/sms/test")
async def test_sms_channel(
    payload: SmsTestRequest,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await NotificationDispatcher(db).send_sms(
        recipients=payload.phones,
        text=payload.text,
        source="notification_test",
        event_type="sms_test",
        severity="info",
        title="短信通道测试",
        dedupe_key=None,
    )
    await db.commit()
    return result


# ----------------------------------------------------------------------
# Voice channel
# ----------------------------------------------------------------------

@router.get("/channels/voice")
async def get_voice_channel(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return await get_public_voice_config(db)


@router.put("/channels/voice")
async def update_voice_channel(
    payload: VoiceConfigUpdate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await save_voice_config(db, payload.model_dump(exclude_unset=True))
    await db.commit()
    return result


@router.post("/channels/voice/test")
async def test_voice_channel(
    payload: VoiceTestRequest,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await NotificationDispatcher(db).send_voice(
        recipients=payload.phones,
        text=payload.text,
        source="notification_test",
        event_type="voice_test",
        severity="info",
        title="语音通道测试",
    )
    await db.commit()
    return result

# ----------------------------------------------------------------------
# Contacts
# ----------------------------------------------------------------------

@router.get("/contacts")
async def list_contacts(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return {"items": await NotificationDispatcher(db).list_contacts()}


@router.post("/contacts")
async def create_contact(
    payload: ContactCreate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    try:
        result = await NotificationDispatcher(db).create_contact(payload.model_dump())
        await db.commit()
        return result
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="联系人保存失败，请检查关联数据") from exc


@router.put("/contacts/{contact_id}")
async def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    try:
        result = await NotificationDispatcher(db).update_contact(
            contact_id, payload.model_dump(exclude_unset=True)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="联系人不存在")
        await db.commit()
        return result
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="联系人保存失败，请检查关联数据") from exc


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: int, db: DbSession, _: CurrentAdminUser) -> dict[str, bool]:
    if not await NotificationDispatcher(db).delete_contact(contact_id):
        raise HTTPException(status_code=404, detail="联系人不存在")
    await db.commit()
    return {"success": True}


# ----------------------------------------------------------------------
# Groups
# ----------------------------------------------------------------------

@router.get("/groups")
async def list_groups(db: DbSession, _: CurrentAdminUser) -> dict[str, Any]:
    return {"items": await NotificationDispatcher(db).list_groups()}


@router.post("/groups")
async def create_group(
    payload: GroupCreate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    try:
        result = await NotificationDispatcher(db).create_group(payload.model_dump())
        await db.commit()
        return result
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="联系人组名称已存在") from exc


@router.put("/groups/{group_id}")
async def update_group(
    group_id: int,
    payload: GroupUpdate,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    try:
        result = await NotificationDispatcher(db).update_group(
            group_id, payload.model_dump(exclude_unset=True)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="联系人组不存在")
        await db.commit()
        return result
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="联系人组名称已存在") from exc


@router.delete("/groups/{group_id}")
async def delete_group(group_id: int, db: DbSession, _: CurrentAdminUser) -> dict[str, bool]:
    if not await NotificationDispatcher(db).delete_group(group_id):
        raise HTTPException(status_code=404, detail="联系人组不存在")
    await db.commit()
    return {"success": True}


# ----------------------------------------------------------------------
# Events & deliveries
# ----------------------------------------------------------------------

@router.get("/events")
async def list_events(
    db: DbSession,
    _: CurrentAdminUser,
    source: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await NotificationDispatcher(db).list_events(
        page=page, page_size=page_size, source=source, severity=severity, status=status
    )


@router.get("/deliveries")
async def list_deliveries(
    db: DbSession,
    _: CurrentAdminUser,
    status: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    source: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return await NotificationDispatcher(db).list_deliveries(
        page=page, page_size=page_size, status=status, channel=channel, source=source
    )


@router.get("/actions")
async def list_actions(
    db: DbSession,
    _: CurrentAdminUser,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return {"items": await IvrService(db).list_actions(limit)}


@router.post("/voice/callback")
async def voice_callback(
    payload: dict[str, Any],
    db: DbSession,
    token: str = Query(default=""),
) -> dict[str, Any]:
    result = await IvrService(db).handle_callback(payload, token)
    await db.commit()
    return result


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(
    delivery_id: int,
    db: DbSession,
    _: CurrentAdminUser,
) -> dict[str, Any]:
    result = await NotificationDispatcher(db).retry_delivery(delivery_id)
    if result is None:
        raise HTTPException(status_code=404, detail="投递记录不存在")
    await db.commit()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "重试发送失败")
    return result
