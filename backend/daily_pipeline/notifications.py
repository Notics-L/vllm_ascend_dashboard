"""Prepare durable notifications for an intranet consumer to pull and send."""

import logging

from sqlalchemy import select

from daily_pipeline.pull_requests import located_pull_request
from daily_pipeline.tracking import DailyTrackingService, current_node
from infrastructure.clients.daily_pipeline_collection import (
    CollectionSourceError,
    DailyCollectionClient,
)
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError, start_processing
from infrastructure.core.security import decrypt_api_key
from infrastructure.persistence.daily_pipeline_settings import (
    DEFAULT_REVERT_DEADLINE_MINUTES,
    REVERT_DEADLINE_KEY,
    read_settings,
    revert_deadline_minutes,
)
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    OPTIMIZATION_MODE,
    add_failure_notification,
    completed_node_result,
    now,
    pr_recipients,
)
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution

logger = logging.getLogger(__name__)


def notification_content(source, run, issue, original_url, revert_url, name, minutes=DEFAULT_REVERT_DEADLINE_MINUTES):
    job_run_id = source.source_run_id or run.run_id
    job_url = f"https://github.com/{run.repository}/actions/runs/{job_run_id}"
    if source.source_id:
        job_url += f"/job/{source.source_id}"
    case_label = source.name
    if qualifiers := ", ".join(filter(None, (source.hardware, getattr(source, "vllm_version", None)))):
        case_label = f"{case_label} ({qualifiers})"
    action = "修复" if revert_url else "优化修复"
    timing = f"请在下一轮 Daily 执行前 {minutes} 分钟完成{action}并合入。"
    lines = [
        f"{name}，Daily 测试发现 PR 引入的问题。",
        f"已创建回退 PR，{timing}" if revert_url else timing,
        f"请在关联 Issue 评论中填写「{case_label} solved by PR #编号」。",
    ]
    if revert_url:
        lines.append("到截止点修复 PR 仍未合入时，将通知原问题 PR 合入人处理回退 PR。")
    lines.extend([
        f"用例：{source.name}",
        f"硬件：{source.hardware or '—'}",
        f"原问题 PR：{original_url}",
    ])
    if revert_url:
        lines.append(f"回退 PR：{revert_url}")
    lines.extend([
        f"失败 Job 流水线：{job_url}",
        f"Daily 主流水线：https://github.com/{run.repository}/actions/runs/{run.run_id}",
    ])
    if issue:
        lines.append(f"Issue：https://github.com/{issue.repository}/issues/{issue.number}")
    return "\n".join(lines)


async def wake_notifications(db):
    """Configuration changes explicitly wake blocked notifications; no config polling."""
    nodes = list(
        await db.scalars(
            select(Node)
            .join(Failure, Failure.id == Node.failure_id)
            .where(
                Failure.processing_status.in_(ACTIVE),
                Node.stage_code == "problem_pr_notification",
                Node.status.in_(("pending", "waiting")),
            )
        )
    )
    for node in nodes:
        latest = await db.scalar(
            select(Execution)
            .where(Execution.node_id == node.id)
            .order_by(Execution.attempt_no.desc())
            .limit(1)
        )
        if latest and ((latest.request_data or {}).get("delivery_started") or (latest.request_data or {}).get("ready")):
            continue  # An uncertain delivery requires manual confirmation.
        try:
            await start_processing(node.id)
        except SchedulerUpdateError:
            logger.exception("Notification wake-up failed node_id=%s", node.id)


async def _waiting(db, node, execution, message):
    node.status = execution.status = "waiting"
    node.updated_at = now()
    execution.message = message
    await db.commit()
    logger.info("Notification waiting node_id=%s reason=%s", node.id, message)
    return True


async def execute_notification(db, node_id):
    node = await db.get(Node, node_id)
    if node is None:
        return True
    failure = await db.scalar(
        select(Failure)
        .where(Failure.id == node.failure_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    nodes = list(
        await db.scalars(
            select(Node)
            .where(Node.failure_id == failure.id)
            .order_by(Node.id)
            .execution_options(populate_existing=True)
        )
    )
    current = current_node(nodes)
    if (
        failure.processing_status not in ACTIVE
        or current is None
        or current.id != node.id
        or node.stage_code != "problem_pr_notification"
        or node.status not in ("pending", "running", "waiting")
    ):
        return True
    tracking = DailyTrackingService(db)
    if node.status == "pending":
        started = await tracking.start_node(node.id, {"delivery_started": False})
        execution = await db.get(Execution, started["executionId"])
    else:
        execution = await db.scalar(
            select(Execution)
            .where(
                Execution.node_id == node.id,
                Execution.source == "auto",
                Execution.status.in_(("running", "waiting")),
            )
            .order_by(Execution.attempt_no.desc())
            .limit(1)
        )
        if execution is None:
            return True
    failure = await db.scalar(
        select(Failure)
        .where(Failure.id == node.failure_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await db.refresh(node)
    await db.refresh(execution)
    if (
        failure.processing_status not in ACTIVE
        or node.status not in ("running", "waiting")
        or execution.status not in ("running", "waiting")
    ):
        return True
    if (execution.request_data or {}).get("delivery_mode") == "pull" and (execution.request_data or {}).get("ready"):
        return True
    if (execution.request_data or {}).get("delivery_started"):
        return await _waiting(
            db, node, execution, "上次通知送达情况未知，请人工确认，系统未重复发送"
        )
    config = await read_settings(db, "github_token", "repository", REVERT_DEADLINE_KEY)
    result = await completed_node_result(db, node.previous_node_id)
    optimize = result.get("revert_mode") == OPTIMIZATION_MODE
    repository = config.get("repository")
    original_data = result if optimize else {"pr_url": result.get("original_pr_url")}
    if not (original_data.get("pr_url") or original_data.get("pr_number")):
        locating = next((n for n in nodes if n.stage_code == "locating"), None)
        original_data = await completed_node_result(db, locating.id if locating else None)
    if not (original_data.get("pr_url") or original_data.get("pr_number")):
        return await _waiting(db, node, execution, "缺少原问题 PR 信息")
    if not optimize and not (result.get("pr_url") or result.get("pr_number")):
        return await _waiting(db, node, execution, "缺少回退 PR 信息")
    try:
        target = located_pull_request(original_data, repository)
        original_url = target["pr_url"]
        revert_url = None if optimize else located_pull_request(result, repository)["pr_url"]
        token = decrypt_api_key(config.get("github_token") or "")
        if not token:
            return await _waiting(db, node, execution, "请先配置 GitHub Token")
        async with DailyCollectionClient(target["pr_repository"], token) as client:
            original = await client.json(f"{client.prefix}/pulls/{target['pr_number']}")
        recipients = pr_recipients(original)
        if not recipients:
            raise ValueError("无法获取原问题 PR 的作者和合入人")
        minutes = revert_deadline_minutes(config)
        source = await db.get(SourceNode, failure.node_id)
        run = await db.get(Run, source.run_id)
        issue = await db.get(Issue, failure.issue_id) if failure.issue_id else None
        for attempt, github_name in enumerate(dict.fromkeys(recipients.values()), execution.attempt_no + 1):
            content = notification_content(source, run, issue, original_url, revert_url, github_name, minutes)
            add_failure_notification(db, node.id, attempt, github_name, content, now(), purpose="problem_pr")
        await tracking.finish_node(
            execution.id, True,
            {"original_pr_author": recipients.get("author"), "original_pr_merger": recipients.get("merger")},
            "已生成原问题 PR 作者和合入人的通知，送达结果见消息消费记录",
        )
    except (CollectionSourceError, ValueError) as exc:
        # Neither account nor auth is put into the audit payload or log.
        await tracking.finish_node(execution.id, False, {}, str(exc))
        logger.warning("Notification failed node_id=%s", node.id)
        return True
    logger.info("PR author notification ready node_id=%s execution_id=%s", node_id, execution.id)
    return True
