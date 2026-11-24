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
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.email import get_smtp_config, send_email
from infrastructure.persistence.models import (
    NotificationAction,
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
        """仅通过邮件通道发送（向后兼容入口）。"""
        return await self.dispatch(
            email_subject=subject,
            email_html=html_content,
            email_recipients=recipients,
            cc_recipients=cc_recipients,
            images=images,
            config=config,
            channels=["email"],
            source=source,
            event_type=event_type,
            severity=severity,
            title=title,
            summary=summary,
            payload=payload,
            dedupe_key=dedupe_key,
        )

    async def resolve_channels(self, severity: str) -> list[str]:
        """返回渠道顺序：语音电话 → 短信 → 邮件，未启用的渠道自动跳过。"""
        from notifications.sms import get_sms_config
        from notifications.voice import get_voice_config

        sms_config = await get_sms_config(self.db)
        voice_config = await get_voice_config(self.db)
        channels = ["email"]
        sms_apply = sms_config.get("apply_to") or "critical"
        voice_apply = voice_config.get("apply_to") or "critical"
        if (
            sms_config.get("enabled")
            and sms_config.get("fallback_enabled", True)
            and (sms_apply == "all" or severity == "critical")
        ):
            channels.insert(0, "sms")
        if (
            voice_config.get("enabled")
            and voice_config.get("fallback_enabled", True)
            and (voice_apply == "all" or severity == "critical")
        ):
            channels.insert(0, "voice")
        return channels

    async def send_sms(
        self,
        *,
        recipients: list[str],
        text: str,
        source: str = "notification",
        event_type: str = "sms",
        severity: str = "info",
        title: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """仅通过短信通道发送。"""
        return await self.dispatch(
            sms_text=text,
            sms_recipients=recipients,
            channels=["sms"],
            source=source,
            event_type=event_type,
            severity=severity,
            title=title or text[:80],
            summary=summary or text,
            payload=payload,
            dedupe_key=dedupe_key,
        )

    async def send_voice(
        self,
        *,
        recipients: list[str],
        text: str,
        source: str = "notification",
        event_type: str = "voice",
        severity: str = "info",
        title: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """仅通过语音电话通道发起播报。"""
        return await self.dispatch(
            voice_text=text,
            voice_recipients=recipients,
            channels=["voice"],
            source=source,
            event_type=event_type,
            severity=severity,
            title=title or text[:80],
            summary=summary or text,
            payload=payload,
            dedupe_key=dedupe_key,
        )

    async def dispatch(
        self,
        *,
        email_subject: str | None = None,
        email_html: str | None = None,
        email_recipients: list[str] | None = None,
        cc_recipients: list[str] | None = None,
        images: dict[str, bytes] | None = None,
        config: dict[str, Any] | None = None,
        sms_text: str | None = None,
        sms_recipients: list[str] | None = None,
        voice_text: str | None = None,
        voice_recipients: list[str] | None = None,
        channels: list[str] | None = None,
        source: str = "notification",
        event_type: str = "notification",
        severity: str = "info",
        title: str | None = None,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """按渠道顺序发送，成功即停止，失败自动降级到下一渠道。"""
        from notifications.sms import get_sms_config
        from notifications.voice import get_voice_config

        email_recipients = [r for r in (email_recipients or []) if r]
        sms_recipients = [p for p in (sms_recipients or []) if p]
        sms_config = await get_sms_config(self.db)
        voice_config = await get_voice_config(self.db)
        if not sms_recipients and email_recipients:
            sms_recipients = await self._phones_for_emails(email_recipients)
        voice_text = voice_text or sms_text
        voice_recipients = voice_recipients or sms_recipients

        planned = list(channels or ["email"])
        requested_channels = list(planned)
        if "sms" in planned and (
            not sms_config.get("enabled") or not sms_text or not sms_recipients
        ):
            planned = [channel for channel in planned if channel != "sms"]
        if "voice" in planned and (
            not voice_config.get("enabled") or not voice_text or not voice_recipients
        ):
            planned = [channel for channel in planned if channel != "voice"]
        if not planned:
            return {"success": False, "error": "没有可用的通知渠道"}

        from notifications.policy import get_policy_config

        policy = await get_policy_config(self.db)
        suppressed_reasons = await self._policy_suppression_reasons(
            source=source, dedupe_key=dedupe_key, policy=policy
        )
        if suppressed_reasons:
            planned = [channel for channel in planned if channel == "email"]
            if not planned:
                planned = ["email"]

        event = await self._get_or_create_event(
            source=source,
            event_type=event_type,
            severity=severity,
            title=title or email_subject or (sms_text or "")[:80],
            summary=summary,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        delivery_ids: list[int] = []
        results: list[dict[str, Any]] = []
        used_channel: str | None = None
        any_success = False

        for channel in planned:
            if channel == "email":
                if not (email_subject and email_html and email_recipients):
                    continue
                result, ids = await self._send_email_channel(
                    event=event,
                    subject=email_subject,
                    html_content=email_html,
                    recipients=email_recipients,
                    cc_recipients=cc_recipients or [],
                    images=images,
                    config=config,
                )
            elif channel == "sms":
                result, ids = await self._send_sms_channel(
                    event=event,
                    recipients=sms_recipients,
                    text=sms_text or "",
                    config=sms_config,
                )
            elif channel == "voice":
                result, ids = await self._send_voice_channel(
                    event=event,
                    recipients=voice_recipients,
                    text=voice_text or "",
                    config=voice_config,
                )
            else:
                continue
            result = {**result, "channel": channel}
            results.append(result)
            delivery_ids.extend(ids)
            if result.get("success"):
                any_success = True
                used_channel = channel
                break

        escalated = False
        if not any_success and policy.get("escalation_enabled"):
            group_ids = [int(g) for g in (policy.get("escalation_group_ids") or [])]
            esc_emails, esc_phones = await self._group_recipients(group_ids)
            if esc_emails or esc_phones:
                for channel in requested_channels:
                    if channel == "email":
                        if not (email_subject and email_html and esc_emails):
                            continue
                        result, ids = await self._send_email_channel(
                            event=event,
                            subject=email_subject,
                            html_content=email_html,
                            recipients=esc_emails,
                            cc_recipients=[],
                            images=images,
                            config=config,
                        )
                    elif channel == "sms":
                        if not (sms_text and esc_phones and sms_config.get("enabled")):
                            continue
                        result, ids = await self._send_sms_channel(
                            event=event,
                            recipients=esc_phones,
                            text=sms_text,
                            config=sms_config,
                        )
                    elif channel == "voice":
                        if not (voice_text and esc_phones and voice_config.get("enabled")):
                            continue
                        result, ids = await self._send_voice_channel(
                            event=event,
                            recipients=esc_phones,
                            text=voice_text,
                            config=voice_config,
                        )
                    else:
                        continue
                    result = {**result, "channel": channel, "escalation": True}
                    results.append(result)
                    delivery_ids.extend(ids)
                    if result.get("success"):
                        any_success = True
                        used_channel = f"escalation:{channel}"
                        escalated = True
                        break

        event.status = "sent" if any_success else "failed"
        await self.db.flush()
        last_error = next(
            (item.get("error") for item in reversed(results) if item.get("error")), None
        )
        return {
            "success": any_success,
            "channel": used_channel,
            "error": None if any_success else (last_error or "所有渠道发送失败"),
            "event_id": event.id,
            "delivery_ids": delivery_ids,
            "results": results,
            "suppressed": bool(suppressed_reasons),
            "suppressed_reasons": suppressed_reasons,
            "escalated": escalated,
        }

    async def _group_recipients(self, group_ids: list[int]) -> tuple[list[str], list[str]]:
        if not group_ids:
            return [], []
        rows = (
            await self.db.execute(
                select(NotificationContact)
                .join(
                    NotificationGroupMember,
                    NotificationGroupMember.contact_id == NotificationContact.id,
                )
                .where(
                    NotificationGroupMember.group_id.in_(group_ids),
                    NotificationContact.enabled.is_(True),
                )
            )
        ).scalars().all()
        emails = sorted({contact.email for contact in rows if contact.email})
        phones = sorted({contact.phone for contact in rows if contact.phone})
        return emails, phones

    async def _policy_suppression_reasons(
        self, *, source: str, dedupe_key: str | None, policy: dict[str, Any]
    ) -> list[str]:
        reasons: list[str] = []
        now = datetime.now(UTC)
        if policy.get("quiet_hours_enabled"):
            local = now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
            start = str(policy.get("quiet_hours_start") or "22:00")
            end = str(policy.get("quiet_hours_end") or "08:00")
            in_quiet = start <= local < end if start <= end else (local >= start or local < end)
            if in_quiet:
                reasons.append("quiet_hours")

        cooldown = int(policy.get("cooldown_minutes") or 0)
        if dedupe_key and cooldown > 0:
            existing = (
                await self.db.execute(
                    select(NotificationEvent).where(NotificationEvent.dedupe_key == dedupe_key)
                )
            ).scalar_one_or_none()
            if existing and existing.created_at and existing.created_at > now - timedelta(minutes=cooldown):
                reasons.append("cooldown")

        window = int(policy.get("storm_window_minutes") or 0)
        threshold = int(policy.get("storm_threshold") or 0)
        if window > 0 and threshold > 0:
            count = (
                await self.db.execute(
                    select(func.count(NotificationEvent.id)).where(
                        NotificationEvent.source == source,
                        NotificationEvent.created_at >= now - timedelta(minutes=window),
                    )
                )
            ).scalar_one()
            if int(count) >= threshold:
                reasons.append("storm")
        return reasons

    async def _phones_for_emails(self, emails: list[str]) -> list[str]:
        if not emails:
            return []
        rows = (
            await self.db.execute(
                select(NotificationContact.phone).where(
                    NotificationContact.enabled.is_(True),
                    NotificationContact.phone.is_not(None),
                    NotificationContact.email.in_(emails),
                )
            )
        ).scalars().all()
        return [phone for phone in rows if phone]

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

    async def _send_email_channel(
        self,
        *,
        event: NotificationEvent,
        subject: str,
        html_content: str,
        recipients: list[str],
        cc_recipients: list[str],
        images: dict[str, bytes] | None,
        config: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[int]]:
        smtp_config = config if config is not None else await self.get_email_config()
        deliveries = [
            await self._create_delivery(event, "email", recipient, "smtp")
            for recipient in recipients + cc_recipients
        ]
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
        return result, [delivery.id for delivery in deliveries]

    async def _send_sms_channel(
        self,
        *,
        event: NotificationEvent,
        recipients: list[str],
        text: str,
        config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[int]]:
        from notifications.sms import send_sms

        now = datetime.now(UTC)
        deliveries = [
            await self._create_delivery(event, "sms", phone, config.get("provider"))
            for phone in recipients
        ]
        results = []
        for delivery, phone in zip(deliveries, recipients, strict=False):
            result = await send_sms(config, phone, text)
            results.append(result)
            success = bool(result.get("success"))
            delivery.status = "sent" if success else "failed"
            delivery.provider = result.get("provider") or delivery.provider
            delivery.provider_message_id = result.get("provider_message_id")
            delivery.error = None if success else result.get("error")
            delivery.sent_at = now if success else None
            delivery.delivered_at = now if success else None
        success = any(item.get("success") for item in results)
        error = next((item.get("error") for item in results if item.get("error")), None)
        return (
            {
                "success": success,
                "error": None if success else error,
                "provider": results[0].get("provider") if results else None,
            },
            [delivery.id for delivery in deliveries],
        )

    async def _send_voice_channel(
        self,
        *,
        event: NotificationEvent,
        recipients: list[str],
        text: str,
        config: dict[str, Any],
    ) -> tuple[dict[str, Any], list[int]]:
        from notifications.voice import send_voice

        now = datetime.now(UTC)
        deliveries = [
            await self._create_delivery(event, "voice", phone, config.get("provider"))
            for phone in recipients
        ]
        results = []
        for delivery, phone in zip(deliveries, recipients, strict=False):
            result = await send_voice(config, phone, text)
            results.append(result)
            success = bool(result.get("success"))
            delivery.status = "sent" if success else "failed"
            delivery.provider = result.get("provider") or delivery.provider
            delivery.provider_message_id = result.get("provider_message_id")
            delivery.error = None if success else result.get("error")
            delivery.sent_at = now if success else None
        success = any(item.get("success") for item in results)
        error = next((item.get("error") for item in results if item.get("error")), None)
        return (
            {
                "success": success,
                "error": None if success else error,
                "provider": results[0].get("provider") if results else None,
            },
            [delivery.id for delivery in deliveries],
        )

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

    async def get_metrics(self, days: int = 7) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        deliveries = (
            await self.db.execute(
                select(NotificationDelivery, NotificationEvent)
                .join(NotificationEvent, NotificationDelivery.event_id == NotificationEvent.id)
                .where(NotificationEvent.created_at >= since)
            )
        ).all()
        events_count = int(
            (
                await self.db.execute(
                    select(func.count(NotificationEvent.id)).where(
                        NotificationEvent.created_at >= since
                    )
                )
            ).scalar_one()
        )
        actions = (
            await self.db.execute(
                select(NotificationAction).where(NotificationAction.created_at >= since)
            )
        ).scalars().all()

        channels: dict[str, dict[str, int]] = {}
        for delivery, _event in deliveries:
            stat = channels.setdefault(
                delivery.channel, {"total": 0, "sent": 0, "failed": 0, "pending": 0}
            )
            stat["total"] += 1
            if delivery.status in ("sent", "delivered", "answered"):
                stat["sent"] += 1
            elif delivery.status == "failed":
                stat["failed"] += 1
            else:
                stat["pending"] += 1
        for stat in channels.values():
            stat["success_rate"] = round(stat["sent"] / stat["total"] * 100, 1) if stat["total"] else 0

        action_stats: dict[str, dict[str, int]] = {}
        for action in actions:
            stat = action_stats.setdefault(action.action, {"total": 0, "success": 0, "failed": 0})
            stat["total"] += 1
            if action.status == "success":
                stat["success"] += 1
            elif action.status == "failed":
                stat["failed"] += 1

        total_deliveries = sum(item["total"] for item in channels.values())
        total_sent = sum(item["sent"] for item in channels.values())
        return {
            "days": days,
            "events": events_count,
            "deliveries": total_deliveries,
            "sent": total_sent,
            "failed": sum(item["failed"] for item in channels.values()),
            "success_rate": round(total_sent / total_deliveries * 100, 1) if total_deliveries else 0,
            "channels": channels,
            "actions": action_stats,
        }

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

    async def list_inbox(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """统一收件箱：展示短信与语音的发送情况（含真实与模拟渠道）。"""
        stmt = select(NotificationDelivery, NotificationEvent).join(
            NotificationEvent, NotificationDelivery.event_id == NotificationEvent.id
        ).where(NotificationDelivery.channel.in_(("sms", "voice")))
        if channel in ("sms", "voice"):
            stmt = stmt.where(NotificationDelivery.channel == channel)
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
            content = event.summary or event.title or ""
            created = delivery.created_at.isoformat() if delivery.created_at else None
            item: dict[str, Any] = {
                "id": delivery.id,
                "event_id": event.id,
                "channel": delivery.channel,
                "recipient": delivery.recipient,
                "content": content,
                "title": event.title,
                "severity": event.severity,
                "status": delivery.status,
                "provider": delivery.provider,
                "provider_message_id": delivery.provider_message_id,
                "error": delivery.error,
                "created_at": created,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
            }
            if delivery.channel == "sms":
                item["segments"] = max(1, (len(content) + 69) // 70)
            else:
                item["answered"] = delivery.status == "sent"
                item["duration_seconds"] = (
                    8 + delivery.id % 20
                    if delivery.provider == "mock" and delivery.status == "sent"
                    else None
                )
            items.append(item)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }

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
