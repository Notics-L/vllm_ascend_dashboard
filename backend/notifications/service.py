"""通知调度器与通知中心服务。

Phase 1：
- 邮件发送写入 notification_events / notification_deliveries
- 提供联系人、联系人组、事件与投递记录的查询/维护能力
- 短信、语音电话在后续阶段接入
"""
from __future__ import annotations

import html as html_lib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.email import get_smtp_config, send_email
from infrastructure.persistence.models import (
    NotificationContact,
    NotificationDelivery,
    NotificationEvent,
    NotificationGroup,
    NotificationGroupMember,
)

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """统一通知调度器。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Email channel
    # ------------------------------------------------------------------

    async def get_email_config(self) -> dict[str, Any]:
        return await get_smtp_config(self.db)

    async def send_email(
        self,
        *,
        subject: str,
        html_content: str,
        recipients: list[str],
        cc_recipients: list[str] | None = None,
        images: dict[str, bytes] | None = None,
        config: dict[str, Any] | None = None,
        source: str = "notification",
        event_type: str = "email",
        severity: str = "info",
        title: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """通过统一配置发送 HTML 邮件，并记录事件与投递结果。"""
        if not recipients:
            return {"success": False, "error": "No recipients configured"}

        smtp_config = config if config is not None else await self.get_email_config()
        event = await self._get_or_create_event(
            source=source,
            event_type=event_type,
            severity=severity,
            title=title or subject,
            summary=summary,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        deliveries = []
        for recipient in recipients + (cc_recipients or []):
            deliveries.append(await self._create_delivery(event, "email", recipient, "smtp"))

        result = await send_email(
            subject=subject,
            html_content=html_content,
            recipients=recipients,
            cc_recipients=cc_recipients,
            smtp_host=smtp_config.get("smtp_host", ""),
            smtp_port=int(smtp_config.get("smtp_port", 587)),
            smtp_username=smtp_config.get("smtp_username", ""),
            smtp_password=smtp_config.get("smtp_password", ""),
            smtp_use_tls=bool(smtp_config.get("smtp_use_tls", True)),
            from_email=smtp_config.get("from_email", ""),
            images=images,
        )
        success = bool(result.get("success"))
        now = datetime.now(UTC)
        for delivery in deliveries:
            delivery.status = "sent" if success else "failed"
            delivery.sent_at = now
            delivery.delivered_at = now if success else None
            delivery.error = None if success else result.get("error")
        event.status = "sent" if success else "failed"
        await self.db.flush()
        return {
            **result,
            "event_id": event.id,
            "delivery_ids": [delivery.id for delivery in deliveries],
        }

    async def _get_or_create_event(
        self,
        *,
        source: str,
        event_type: str,
        severity: str,
        title: str,
        summary: str | None,
        payload: dict[str, Any] | None,
        dedupe_key: str | None,
    ) -> NotificationEvent:
        key = dedupe_key or f"{source}:{uuid.uuid4().hex}"
        event = (
            await self.db.execute(
                select(NotificationEvent).where(NotificationEvent.dedupe_key == key)
            )
        ).scalar_one_or_none()
        if event is not None:
            return event
        event = NotificationEvent(
            source=source,
            event_type=event_type,
            severity=severity,
            title=title[:500],
            summary=summary,
            payload=payload,
            dedupe_key=key[:255],
            status="pending",
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def _create_delivery(
        self,
        event: NotificationEvent,
        channel: str,
        recipient: str,
        provider: str | None,
    ) -> NotificationDelivery:
        max_attempt = (
            await self.db.execute(
                select(func.max(NotificationDelivery.attempt)).where(
                    NotificationDelivery.event_id == event.id,
                    NotificationDelivery.channel == channel,
                    NotificationDelivery.recipient == recipient,
                )
            )
        ).scalar_one_or_none()
        delivery = NotificationDelivery(
            event_id=event.id,
            channel=channel,
            recipient=recipient,
            attempt=int(max_attempt or 0) + 1,
            status="pending",
            provider=provider,
        )
        self.db.add(delivery)
        await self.db.flush()
        return delivery

    async def retry_delivery(self, delivery_id: int) -> dict[str, Any] | None:
        delivery = await self.db.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return None
        if delivery.channel != "email":
            return {"success": False, "error": "当前仅支持重试邮件投递"}
        event = await self.db.get(NotificationEvent, delivery.event_id)
        if event is None:
            return {"success": False, "error": "通知事件不存在"}

        smtp_config = await self.get_email_config()
        summary = event.summary or ""
        body = (
            f"<h2>{html_lib.escape(event.title)}</h2>"
            f"<p>{html_lib.escape(summary)}</p>"
            f"<p style='color:#888;font-size:12px;'>vLLM Ascend Dashboard 通知重试</p>"
        )
        result = await send_email(
            subject=f"[重试] {event.title}",
            html_content=body,
            recipients=[delivery.recipient],
            smtp_host=smtp_config.get("smtp_host", ""),
            smtp_port=int(smtp_config.get("smtp_port", 587)),
            smtp_username=smtp_config.get("smtp_username", ""),
            smtp_password=smtp_config.get("smtp_password", ""),
            smtp_use_tls=bool(smtp_config.get("smtp_use_tls", True)),
            from_email=smtp_config.get("from_email", ""),
        )
        now = datetime.now(UTC)
        new_delivery = await self._create_delivery(event, "email", delivery.recipient, "smtp")
        success = bool(result.get("success"))
        new_delivery.status = "sent" if success else "failed"
        new_delivery.sent_at = now
        new_delivery.delivered_at = now if success else None
        new_delivery.error = None if success else result.get("error")
        event.status = "sent" if success else "failed"
        await self.db.flush()
        return {**result, "event_id": event.id, "delivery_ids": [new_delivery.id]}

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(self) -> list[dict[str, Any]]:
        contacts = (
            await self.db.execute(
                select(NotificationContact).order_by(NotificationContact.id.desc())
            )
        ).scalars().all()
        memberships = (
            await self.db.execute(select(NotificationGroupMember))
        ).scalars().all()
        group_map: dict[int, list[int]] = {}
        for member in memberships:
            group_map.setdefault(member.contact_id, []).append(member.group_id)
        return [
            self._serialize_contact(contact, group_map.get(contact.id, []))
            for contact in contacts
        ]

    async def create_contact(self, data: dict[str, Any]) -> dict[str, Any]:
        contact = NotificationContact(
            user_id=data.get("user_id"),
            name=data["name"].strip(),
            email=(data.get("email") or "").strip() or None,
            phone=(data.get("phone") or "").strip() or None,
            enabled=bool(data.get("enabled", True)),
        )
        self.db.add(contact)
        await self.db.flush()
        await self._replace_contact_groups(contact.id, data.get("group_ids") or [])
        await self.db.flush()
        return await self._contact_detail(contact.id)

    async def update_contact(self, contact_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        contact = await self.db.get(NotificationContact, contact_id)
        if contact is None:
            return None
        if "name" in data:
            contact.name = str(data["name"]).strip()
        if "email" in data:
            contact.email = (data.get("email") or "").strip() or None
        if "phone" in data:
            contact.phone = (data.get("phone") or "").strip() or None
        if "enabled" in data:
            contact.enabled = bool(data["enabled"])
        if "user_id" in data:
            contact.user_id = data.get("user_id")
        if "group_ids" in data:
            await self._replace_contact_groups(contact.id, data.get("group_ids") or [])
        await self.db.flush()
        return await self._contact_detail(contact.id)

    async def delete_contact(self, contact_id: int) -> bool:
        contact = await self.db.get(NotificationContact, contact_id)
        if contact is None:
            return False
        await self.db.delete(contact)
        await self.db.flush()
        return True

    async def _contact_detail(self, contact_id: int) -> dict[str, Any]:
        contact = await self.db.get(NotificationContact, contact_id)
        groups = (
            await self.db.execute(
                select(NotificationGroupMember.group_id).where(
                    NotificationGroupMember.contact_id == contact_id
                )
            )
        ).scalars().all()
        return self._serialize_contact(contact, list(groups))

    async def _replace_contact_groups(self, contact_id: int, group_ids: list[int]) -> None:
        await self.db.execute(
            delete(NotificationGroupMember).where(
                NotificationGroupMember.contact_id == contact_id
            )
        )
        for group_id in {int(g) for g in group_ids}:
            self.db.add(NotificationGroupMember(group_id=group_id, contact_id=contact_id))

    @staticmethod
    def _serialize_contact(contact: NotificationContact, group_ids: list[int]) -> dict[str, Any]:
        return {
            "id": contact.id,
            "user_id": contact.user_id,
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "phone_verified_at": contact.phone_verified_at.isoformat()
            if contact.phone_verified_at
            else None,
            "enabled": contact.enabled,
            "group_ids": group_ids,
            "created_at": contact.created_at.isoformat() if contact.created_at else None,
            "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
        }

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def list_groups(self) -> list[dict[str, Any]]:
        groups = (
            await self.db.execute(
                select(NotificationGroup).order_by(NotificationGroup.id.desc())
            )
        ).scalars().all()
        members = (
            await self.db.execute(select(NotificationGroupMember))
        ).scalars().all()
        contacts = (
            await self.db.execute(select(NotificationContact))
        ).scalars().all()
        contact_map = {contact.id: contact for contact in contacts}
        member_map: dict[int, list[dict[str, Any]]] = {}
        for member in members:
            contact = contact_map.get(member.contact_id)
            if contact is not None:
                member_map.setdefault(member.group_id, []).append(
                    {"id": contact.id, "name": contact.name, "email": contact.email}
                )
        return [
            {
                "id": group.id,
                "name": group.name,
                "description": group.description,
                "enabled": group.enabled,
                "contacts": member_map.get(group.id, []),
                "contact_ids": [item["id"] for item in member_map.get(group.id, [])],
                "created_at": group.created_at.isoformat() if group.created_at else None,
            }
            for group in groups
        ]

    async def create_group(self, data: dict[str, Any]) -> dict[str, Any]:
        group = NotificationGroup(
            name=data["name"].strip(),
            description=(data.get("description") or "").strip() or None,
            enabled=bool(data.get("enabled", True)),
        )
        self.db.add(group)
        await self.db.flush()
        await self._replace_group_members(group.id, data.get("contact_ids") or [])
        await self.db.flush()
        return await self._group_detail(group.id)

    async def update_group(self, group_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        group = await self.db.get(NotificationGroup, group_id)
        if group is None:
            return None
        if "name" in data:
            group.name = str(data["name"]).strip()
        if "description" in data:
            group.description = (data.get("description") or "").strip() or None
        if "enabled" in data:
            group.enabled = bool(data["enabled"])
        if "contact_ids" in data:
            await self._replace_group_members(group.id, data.get("contact_ids") or [])
        await self.db.flush()
        return await self._group_detail(group.id)

    async def delete_group(self, group_id: int) -> bool:
        group = await self.db.get(NotificationGroup, group_id)
        if group is None:
            return False
        await self.db.delete(group)
        await self.db.flush()
        return True

    async def _replace_group_members(self, group_id: int, contact_ids: list[int]) -> None:
        await self.db.execute(
            delete(NotificationGroupMember).where(NotificationGroupMember.group_id == group_id)
        )
        for contact_id in {int(c) for c in contact_ids}:
            self.db.add(NotificationGroupMember(group_id=group_id, contact_id=contact_id))

    async def _group_detail(self, group_id: int) -> dict[str, Any]:
        groups = await self.list_groups()
        return next(group for group in groups if group["id"] == group_id)

    # ------------------------------------------------------------------
    # Events & deliveries
    # ------------------------------------------------------------------

    async def list_deliveries(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        channel: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        stmt = select(NotificationDelivery, NotificationEvent).join(
            NotificationEvent, NotificationDelivery.event_id == NotificationEvent.id
        )
        if status:
            stmt = stmt.where(NotificationDelivery.status == status)
        if channel:
            stmt = stmt.where(NotificationDelivery.channel == channel)
        if source:
            stmt = stmt.where(NotificationEvent.source == source)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows = (
            await self.db.execute(
                stmt.order_by(NotificationDelivery.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        items = []
        for delivery, event in rows:
            items.append(
                {
                    "id": delivery.id,
                    "event_id": event.id,
                    "source": event.source,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "title": event.title,
                    "summary": event.summary,
                    "channel": delivery.channel,
                    "recipient": delivery.recipient,
                    "attempt": delivery.attempt,
                    "status": delivery.status,
                    "provider": delivery.provider,
                    "error": delivery.error,
                    "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                    "delivered_at": delivery.delivered_at.isoformat()
                    if delivery.delivered_at
                    else None,
                    "created_at": delivery.created_at.isoformat()
                    if delivery.created_at
                    else None,
                }
            )
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    async def list_events(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        source: str | None = None,
        severity: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        stmt = select(NotificationEvent)
        if source:
            stmt = stmt.where(NotificationEvent.source == source)
        if severity:
            stmt = stmt.where(NotificationEvent.severity == severity)
        if status:
            stmt = stmt.where(NotificationEvent.status == status)
        total = int(
            (await self.db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        )
        events = (
            await self.db.execute(
                stmt.order_by(NotificationEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": event.id,
                    "source": event.source,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "title": event.title,
                    "summary": event.summary,
                    "status": event.status,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                for event in events
            ],
        }
