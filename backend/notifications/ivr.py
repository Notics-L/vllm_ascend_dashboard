"""语音 IVR 回调、按键动作执行与审计。"""
from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.github_credentials import global_github_configured
from infrastructure.persistence.models import (
    NotificationAction,
    NotificationDelivery,
    NotificationEvent,
    ReleaseWorkflow,
)
from notifications.voice import get_voice_config

logger = logging.getLogger(__name__)

_CONFIRM_ACTIONS = {"retry", "abort"}
_ACTION_LABELS = {
    "retry": "重试失败 Job",
    "abort": "取消运行",
    "ack": "确认告警",
    "ignore": "忽略告警",
}


class IvrService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def handle_callback(self, payload: dict[str, Any], token: str) -> dict[str, Any]:
        config = await get_voice_config(self.db)
        if not config.get("enabled") or not config.get("ivr_enabled"):
            return {"success": False, "message": "IVR 未启用"}
        expected = str(config.get("callback_token") or "")
        if not expected or not hmac.compare_digest(expected, token or ""):
            return {"success": False, "message": "回调 token 校验失败"}

        call_id = str(
            payload.get("call_id") or payload.get("CallId") or payload.get("callId") or ""
        ).strip()
        digit = str(
            payload.get("digit") or payload.get("digits") or payload.get("Dtmf") or ""
        ).strip()
        phone = str(
            payload.get("phone") or payload.get("CalledNumber") or payload.get("caller") or ""
        ).strip()
        if not call_id or not digit:
            return {"success": False, "message": "缺少 call_id 或按键"}

        allowed_phones = config.get("ivr_allowed_phones") or []
        if allowed_phones and phone not in allowed_phones:
            return {"success": False, "message": "该号码无 IVR 操作权限"}

        delivery = (
            await self.db.execute(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.channel == "voice",
                    NotificationDelivery.provider_message_id == call_id,
                )
                .order_by(NotificationDelivery.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if delivery is None:
            return {"success": False, "message": "未找到对应的语音投递记录"}

        pending = (
            await self.db.execute(
                select(NotificationAction).where(
                    NotificationAction.delivery_id == delivery.id,
                    NotificationAction.stage == "request",
                    NotificationAction.status == "pending_confirmation",
                )
            )
        ).scalar_one_or_none()

        if pending is not None and digit == "0":
            pending.status = "cancelled"
            pending.executed_at = datetime.now(UTC)
            pending.result = {"message": "用户取消操作"}
            await self.db.flush()
            return {"success": True, "action": pending.action, "message": "已取消操作"}

        action_map = config.get("ivr_actions") or {}
        action = str(action_map.get(digit, ""))
        if not action:
            return {"success": False, "message": f"未配置按键 {digit} 的动作"}

        if action in _CONFIRM_ACTIONS:
            if pending is None:
                record = await self._get_or_create_action(
                    delivery=delivery,
                    call_id=call_id,
                    digit=digit,
                    action=action,
                    stage="request",
                    status="pending_confirmation",
                    result={"message": "等待二次确认"},
                )
                label = _ACTION_LABELS.get(action, action)
                return {
                    "success": True,
                    "action": action,
                    "action_id": record.id,
                    "message": f"已收到{label}请求，按 1 确认，按 0 取消",
                }
            if digit != "1":
                return {"success": True, "action": pending.action, "message": "请按 1 确认或按 0 取消"}
            record = pending
        else:
            record = await self._get_or_create_action(
                delivery=delivery,
                call_id=call_id,
                digit=digit,
                action=action,
                stage="execute",
                status="pending",
            )

        result = await self._execute(record, delivery)
        await self.db.flush()
        return result

    async def _get_or_create_action(
        self,
        *,
        delivery: NotificationDelivery,
        call_id: str,
        digit: str,
        action: str,
        stage: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> NotificationAction:
        existing = (
            await self.db.execute(
                select(NotificationAction).where(
                    NotificationAction.provider_call_id == call_id,
                    NotificationAction.digit == digit,
                    NotificationAction.stage == stage,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        record = NotificationAction(
            delivery_id=delivery.id,
            provider_call_id=call_id,
            digit=digit,
            action=action,
            stage=stage,
            status=status,
            result=result,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def _execute(
        self, record: NotificationAction, delivery: NotificationDelivery
    ) -> dict[str, Any]:
        event = await self.db.get(NotificationEvent, delivery.event_id)
        if event is None:
            return await self._finish(record, False, {"message": "通知事件不存在"})

        config = await get_voice_config(self.db)
        allowed_sources = config.get("ivr_allowed_sources") or []
        if record.action in _CONFIRM_ACTIONS and allowed_sources and event.source not in allowed_sources:
            return await self._finish(record, False, {"message": f"来源 {event.source} 不允许该操作"})

        if record.action == "ack":
            event.status = "acknowledged"
            return await self._finish(record, True, {"message": "告警已确认"})
        if record.action == "ignore":
            event.status = "ignored"
            return await self._finish(record, True, {"message": "告警已忽略"})
        if record.action not in _CONFIRM_ACTIONS:
            return await self._finish(record, False, {"message": f"未知动作 {record.action}"})

        payload = event.payload or {}
        workflow_id = payload.get("workflow_id")
        run_ids = payload.get("run_ids") or []
        if not workflow_id or not run_ids:
            return await self._finish(record, False, {"message": "事件缺少 Workflow 或 Run 信息"})
        workflow = await self.db.get(ReleaseWorkflow, int(workflow_id))
        if workflow is None or not global_github_configured():
            return await self._finish(record, False, {"message": "缺少 GitHub 配置，无法执行"})

        client = GitHubClient(
            owner=workflow.repo_owner,
            repo=workflow.repo_name,
        )
        executed: list[dict[str, Any]] = []
        try:
            for run_id in run_ids[:3]:
                if record.action == "retry":
                    await client.rerun_failed_jobs(int(run_id))
                else:
                    await client.cancel_run(int(run_id))
                executed.append({"run_id": run_id, "success": True})
        except Exception as exc:  # noqa: BLE001
            logger.warning("IVR action %s failed: %s", record.action, exc)
            return await self._finish(
                record, False, {"message": str(exc), "executed": executed}
            )
        finally:
            await client.close()
        return await self._finish(
            record,
            True,
            {
                "message": f"{_ACTION_LABELS.get(record.action, record.action)}已执行",
                "executed": executed,
            },
        )

    async def _finish(
        self, record: NotificationAction, success: bool, result: dict[str, Any]
    ) -> dict[str, Any]:
        record.status = "success" if success else "failed"
        record.result = result
        record.executed_at = datetime.now(UTC)
        await self.db.flush()
        return {
            "success": success,
            "action": record.action,
            "action_id": record.id,
            "message": result.get("message"),
        }

    async def list_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            await self.db.execute(
                select(NotificationAction, NotificationDelivery, NotificationEvent)
                .join(NotificationDelivery, NotificationAction.delivery_id == NotificationDelivery.id)
                .join(NotificationEvent, NotificationDelivery.event_id == NotificationEvent.id)
                .order_by(NotificationAction.id.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "id": action.id,
                "delivery_id": action.delivery_id,
                "provider_call_id": action.provider_call_id,
                "digit": action.digit,
                "action": action.action,
                "stage": action.stage,
                "status": action.status,
                "result": action.result,
                "event_title": event.title,
                "event_source": event.source,
                "event_id": event.id,
                "created_at": action.created_at.isoformat() if action.created_at else None,
                "executed_at": action.executed_at.isoformat() if action.executed_at else None,
            }
            for action, _delivery, event in rows
        ]
