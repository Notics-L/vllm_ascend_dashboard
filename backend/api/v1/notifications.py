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
)
from notifications.service import NotificationDispatcher

router = APIRouter()


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
