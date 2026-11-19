"""Authenticated pull delivery with atomic claims and idempotent acknowledgements."""

import hashlib
import logging
import secrets

from daily_pipeline.tracking import DailyTrackingService, current_node
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    FAILURE_NOTIFICATION_STAGE,
    NOTIFICATION_STAGES,
    STAGES,
    STANDALONE_NOTICE_PURPOSES,
    latest_failure_notifications,
    now,
)

logger = logging.getLogger(__name__)
DELIVERY_MESSAGES = {
    "sent": "通知已发送（消费端确认）",
    "failed": "通知发送失败（消费端确认），可重试",
    "unknown": "通知送达情况未知，请确认后回传结果或人工处理",
}


def receipt_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_ready(execution):
    data = execution.request_data or {}
    return (
        execution.source == "auto"
        and execution.status == "waiting"
        and data.get("delivery_mode") == "pull"
        and data.get("ready") is True
        and not data.get("delivery_started")
    )


def consumption_status(execution):
    if execution.status == "ended":
        return "ended"
    if (execution.result_data or {}).get("delivery_status") in ("sent", "failed"):
        return "completed"
    if (execution.request_data or {}).get("delivery_started"):
        return "processing"
    return "pending"


class DailyNotificationConsumptionService:
    def __init__(self, repository):
        self.repository = repository
        self.db = repository.db

    async def records(self, page=1, page_size=10, outcome=None, status=None):
        rows, total = await self.repository.consumption_records(page, page_size, outcome, status)
        items = []
        for row, stage_code in rows:
            data = row.request_data or {}
            result = (row.result_data or {}).get("delivery_status", "waiting")
            if row.status == "ended":
                result = "ended"
            items.append({
                "id": str(row.id),
                "status": consumption_status(row),
                "createdAt": row.started_at.isoformat() + "Z" if row.started_at else None,
                "notificationName": "回退 PR 到期通知" if data.get("purpose") == "revert_deadline" else STAGES[stage_code],
                "githubName": data.get("github_name", ""),
                "name": data.get("recipient_name", ""),
                "content": data.get("content", ""),
                "consumer": data.get("claimed_by", ""),
                "claimedAt": data.get("claimed_at"),
                "confirmedAt": (row.result_data or {}).get("confirmed_at") or (row.finished_at.isoformat() + "Z" if row.finished_at and result in ("sent", "failed") else None),
                "result": result,
            })
        return {"items": items, "total": total, "page": page, "pageSize": page_size}

    async def claim(self, consumer):
        # Always lock the failure first, as manual completion/Issue close do.
        for identity in await self.repository.candidate_failures():
            failure = await self.repository.lock_failure(identity, skip_locked=True)
            if failure is None:
                continue
            execution = await self.repository.standalone_delivery(identity)
            if execution is not None:
                node = await self.repository.node(execution.node_id)
            else:
                if failure.processing_status not in ACTIVE:
                    continue
                node = current_node(await self.repository.nodes(identity))
                if node is None or node.stage_code not in NOTIFICATION_STAGES or node.status != "waiting":
                    continue
                if node.stage_code == FAILURE_NOTIFICATION_STAGE:
                    executions = await latest_failure_notifications(self.db, node.id)
                    execution = next((item for item in executions if is_ready(item)), None)
                else:
                    execution = await self.repository.latest_execution(node.id)
            if execution is None or not is_ready(execution):
                continue
            data = execution.request_data or {}
            receipt = secrets.token_urlsafe(32)
            point = now()
            execution.request_data = {
                **data,
                "delivery_started": True,
                "claimed_by": consumer,
                "claimed_at": point.isoformat() + "Z",
                "receipt_hash": receipt_digest(receipt),
            }
            execution.message = "通知已领取，等待消费端回传结果"
            if not data.get("purpose"):
                node.updated_at = point
            await self.db.commit()
            logger.info("Notification claimed execution_id=%s", execution.id)
            # This credential-bearing response is only exposed to authenticated admins.
            # The consumer owns account mapping; no employee account is returned here.
            return {
                "id": str(execution.id),
                "receiptToken": receipt,
                "content": data["content"],
                "githubName": data["github_name"],
            }
        await self.db.rollback()
        return None

    async def acknowledge(self, identity, receipt, outcome, consumer):
        execution = await self.repository.execution(identity)
        if execution is None:
            raise LookupError("通知不存在")
        node = await self.repository.node(execution.node_id)
        failure = await self.repository.lock_failure(node.failure_id)
        await self.db.refresh(node)
        await self.db.refresh(execution)
        data = execution.request_data or {}
        if (
            data.get("delivery_mode") != "pull"
            or not data.get("delivery_started")
            or data.get("claimed_by") != consumer
            or not secrets.compare_digest(data.get("receipt_hash", ""), receipt_digest(receipt))
        ):
            raise ValueError("领取凭据无效")
        if outcome not in DELIVERY_MESSAGES:
            raise ValueError("通知结果无效")
        recorded = (execution.result_data or {}).get("delivery_status")
        if execution.status in ("completed", "failed") and recorded in ("sent", "failed"):
            if recorded != outcome:
                raise ValueError("通知结果已经确认，不能更改")
            return {"id": str(identity), "result": outcome, "alreadyProcessed": True}
        if data.get("purpose") in STANDALONE_NOTICE_PURPOSES:
            if execution.status != "waiting" or execution.source != "auto":
                raise ValueError("通知执行已结束，结果回传已失效")
            point = now()
            execution.result_data = {
                "delivery_mode": "pull", "delivery_status": outcome, "confirmed_at": point.isoformat() + "Z",
            }
            execution.message = DELIVERY_MESSAGES[outcome]
            if outcome != "unknown":
                execution.status = "completed" if outcome == "sent" else "failed"
                execution.finished_at = point
            await self.db.commit()
            return {"id": str(identity), "result": outcome, "alreadyProcessed": False}
        current = current_node(await self.repository.nodes(failure.id))
        if (
            failure.processing_status not in ACTIVE
            or current is None or current.id != node.id
            or node.status != "waiting" or execution.status != "waiting"
            or execution.source != "auto"
        ):
            raise ValueError("流程或通知执行已结束，结果回传已失效")
        result = {"delivery_mode": "pull", "delivery_status": outcome, "confirmed_at": now().isoformat() + "Z"}
        if outcome == "unknown":
            execution.result_data = result
            execution.message = DELIVERY_MESSAGES[outcome]
            node.updated_at = now()
            await self.db.commit()
        elif node.stage_code == FAILURE_NOTIFICATION_STAGE:
            await self._complete_failure_notification(node, execution, outcome, result)
        else:
            await DailyTrackingService(self.db).finish_node(
                execution.id, outcome == "sent", result, DELIVERY_MESSAGES[outcome]
            )
        logger.info("Notification result execution_id=%s outcome=%s", identity, outcome)
        return {"id": str(identity), "result": outcome, "alreadyProcessed": False}


    async def _complete_failure_notification(self, node, execution, outcome, result):
        deliveries = await latest_failure_notifications(self.db, node.id)
        point = now()
        execution.status = "completed" if outcome == "sent" else "failed"
        execution.result_data = result
        execution.finished_at = point
        execution.message = f"{execution.request_data['github_name']}：{DELIVERY_MESSAGES[outcome]}"
        node.updated_at = point
        if all(delivery.status in ("completed", "failed") for delivery in deliveries):
            node.status = "completed" if all(delivery.status == "completed" for delivery in deliveries) else "failed"
            node.finished_at = point
        await self.db.commit()
