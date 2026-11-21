"""
告警规则评估服务（条件组模型）

组间 AND，组内按 logic 字段（AND/OR）。支持 is_exclude (NOT)。
"""
import logging
import operator as _op
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import (
    AlertCondition,
    AlertConditionGroup,
    AlertHistory,
    AlertRule,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
    User,
)
from notifications.service import NotificationDispatcher

logger = logging.getLogger(__name__)

_OPERATOR_MAP = {">": _op.gt, "<": _op.lt, ">=": _op.ge, "<=": _op.le, "==": _op.eq}


class AlertEvaluator:

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def evaluate_all_rules(self) -> int:
        stmt = select(AlertRule).where(AlertRule.enabled.is_(True))
        result = await self.db.execute(stmt)
        rules = list(result.scalars().all())
        if not rules:
            return 0

        triggered_count = 0
        for rule in rules:
            try:
                if await self._evaluate_rule(rule):
                    triggered_count += 1
            except Exception as exc:
                logger.error(f"Error evaluating rule {rule.id}: {exc}", exc_info=True)

        if triggered_count > 0:
            await self.db.commit()
        return triggered_count

    # ── Rule evaluation ──

    async def _evaluate_rule(self, rule: AlertRule) -> bool:
        # Load groups with conditions
        g_stmt = select(AlertConditionGroup).where(AlertConditionGroup.rule_id == rule.id).order_by(AlertConditionGroup.display_order)
        groups = list((await self.db.execute(g_stmt)).scalars().all())
        if not groups:
            logger.debug(f"Rule {rule.id}: no condition groups, skipping")
            return False

        metrics = await self._get_latest_metrics(rule)
        if not metrics:
            return False

        trigger_records = []
        for metrics_row in metrics:
            all_groups_pass = True
            group_details = []

            for group in groups:
                c_stmt = select(AlertCondition).where(AlertCondition.group_id == group.id).order_by(AlertCondition.display_order)
                conditions = list((await self.db.execute(c_stmt)).scalars().all())
                if not conditions:
                    continue

                group_pass = self._evaluate_group(conditions, metrics_row)
                group_details.append({
                    "group_id": group.id,
                    "logic": group.logic,
                    "passed": group_pass,
                    "conditions": [
                        {"metric": c.metric_field, "operator": c.operator, "threshold": c.threshold, "is_exclude": c.is_exclude}
                        for c in conditions
                    ],
                })

                if not group_pass:
                    all_groups_pass = False
                    break

            if all_groups_pass:
                actual = getattr(metrics_row, conditions[0].metric_field if conditions else "npu_utilization", 0)
                trigger_records.append({
                    "cluster_id": metrics_row.cluster_id,
                    "cluster_name": metrics_row.cluster_name,
                    "actual_value": float(actual),
                    "condition_details": {"groups": group_details, "matched": True},
                })

        if not trigger_records:
            if rule.last_triggered_at is not None:
                rule.last_triggered_at = None  # recovered
            return False

        if rule.last_triggered_at is not None:
            logger.debug(f"Rule {rule.id}: still in alert, silent")
            return False

        # Fire!
        now = datetime.now(UTC)
        rule.last_triggered_at = now
        for rec in trigger_records:
            history = AlertHistory(
                rule_id=rule.id, rule_name=rule.name,
                actual_value=rec["actual_value"],
                cluster_id=rec["cluster_id"], cluster_name=rec["cluster_name"],
                node_name=rule.node_name, condition_details=rec["condition_details"],
                triggered_at=now,
            )
            self.db.add(history)
            await self.db.flush()
            if rule.notify_email:
                success, error = await self._send_notification(rule, history)
                history.notification_sent = success
                history.notification_error = error

        return True

    def _evaluate_group(self, conditions: list[AlertCondition], metrics_row) -> bool:
        logic = conditions[0].group.logic if hasattr(conditions[0], 'group') else "AND"
        # Determine logic from first condition's group (they all share the same group)
        for c in conditions:
            check = _OPERATOR_MAP.get(c.operator)
            if check is None:
                continue
            value = getattr(metrics_row, c.metric_field, 0)
            cond_met = check(value, c.threshold)
            if c.is_exclude:
                cond_met = not cond_met

            if logic == "AND":
                if not cond_met:
                    return False
            else:  # OR
                if cond_met:
                    return True

        return logic == "AND"

    # ── Metrics fetching ──

    async def _get_latest_metrics(self, rule: AlertRule) -> list:
        if rule.node_name is not None:
            return await self._get_latest_node_metrics(rule)
        return await self._get_latest_cluster_metrics(rule)

    async def _get_latest_cluster_metrics(self, rule: AlertRule) -> list:
        stmt = select(ResourceNpuMetrics).order_by(ResourceNpuMetrics.collected_at.desc())
        if rule.cluster_id is not None:
            stmt = stmt.where(ResourceNpuMetrics.cluster_id == rule.cluster_id).limit(1)
        else:
            from sqlalchemy import func
            subq = (
                select(ResourceNpuMetrics.cluster_id, func.max(ResourceNpuMetrics.collected_at).label("max_c"))
                .group_by(ResourceNpuMetrics.cluster_id).subquery()
            )
            stmt = select(ResourceNpuMetrics).join(subq, (ResourceNpuMetrics.cluster_id == subq.c.cluster_id) & (ResourceNpuMetrics.collected_at == subq.c.max_c))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _get_latest_node_metrics(self, rule: AlertRule) -> list:
        stmt = select(ResourceNodeMetrics).where(ResourceNodeMetrics.node_name == rule.node_name).order_by(ResourceNodeMetrics.collected_at.desc())
        if rule.cluster_id is not None:
            stmt = stmt.where(ResourceNodeMetrics.cluster_id == rule.cluster_id)
        stmt = stmt.limit(1)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Notification ──

    async def _send_notification(self, rule: AlertRule, history: AlertHistory) -> tuple:
        recipient_email = rule.notification_email
        if not recipient_email:
            user_stmt = select(User.email).where(User.id == rule.user_id)
            user_row = (await self.db.execute(user_stmt)).scalar_one_or_none()
            if user_row and user_row.email:
                recipient_email = user_row.email
            else:
                return False, f"User {rule.user_id} has no email"

        dispatcher = NotificationDispatcher(self.db)
        smtp_config = await dispatcher.get_email_config()
        if not smtp_config or not smtp_config.get("smtp_host"):
            return False, "SMTP not configured"

        cluster_info = f"集群: {history.cluster_name or history.cluster_id}" if history.cluster_id else "所有集群"
        subject = f"[vLLM Ascend 告警] {rule.name}"
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;padding:20px;">
<h2 style="color:#ff4d4f;">⚠ 告警触发</h2>
<table style="border-collapse:collapse;width:100%;max-width:500px;">
<tr><td style="padding:8px;border:1px solid #eee;background:#fafafa;"><strong>规则</strong></td><td style="padding:8px;border:1px solid #eee;">{rule.name}</td></tr>
<tr><td style="padding:8px;border:1px solid #eee;background:#fafafa;"><strong>实际值</strong></td><td style="padding:8px;border:1px solid #eee;">{history.actual_value}</td></tr>
<tr><td style="padding:8px;border:1px solid #eee;background:#fafafa;"><strong>{cluster_info}</strong></td><td style="padding:8px;border:1px solid #eee;">{history.triggered_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</td></tr>
</table>
<p style="color:#888;font-size:12px;">vLLM Ascend Dashboard 自动发送</p></body></html>"""

        summary = (
            f"实际值 {history.actual_value}，{cluster_info}，"
            f"触发时间 {history.triggered_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        try:
            result = await dispatcher.dispatch(
                email_subject=subject,
                email_html=html,
                email_recipients=[recipient_email],
                config=smtp_config,
                sms_text=f"【vLLM Ascend 告警】{rule.name}：{summary}"[:180],
                channels=await dispatcher.resolve_channels("critical"),
                source="alert_rules",
                event_type="resource_alert",
                severity="critical",
                title=rule.name,
                summary=summary,
                payload={
                    "rule_id": rule.id,
                    "history_id": history.id,
                    "cluster_id": history.cluster_id,
                    "node_name": history.node_name,
                },
                dedupe_key=f"alert_rules:{history.id}",
            )
            if result.get("success"):
                return True, None
            return False, result.get("error", "Unknown")
        except Exception as exc:
            return False, str(exc)


