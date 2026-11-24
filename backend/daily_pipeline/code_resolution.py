"""Track code fixes and notify maintainers about pending PRs."""

import logging
import re
from datetime import UTC, timedelta

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.orm import aliased

from daily_pipeline.collection_source import utc_datetime
from daily_pipeline.cron import schedule_trigger
from daily_pipeline.pull_requests import located_pull_request
from daily_pipeline.trigger_config import TRIGGER_TASK
from infrastructure.clients.daily_pipeline_collection import CollectionSourceError
from infrastructure.persistence.daily_pipeline_schedules import enabled_schedule
from infrastructure.persistence.daily_pipeline_settings import (
    REVERT_DEADLINE_KEY,
    read_settings,
    revert_deadline_minutes,
)
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    OPTIMIZATION_MODE,
    add_failure_notification,
    completed_node_result,
    failure_notification_recipients,
    now,
    pr_recipients,
    sync_shared_processing,
)
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution
from infrastructure.persistence.models import DailyPipelineTriggerHistory as History

logger = logging.getLogger(__name__)
SOLVED_BY = re.compile(
    r"(.+?)\s+solved\s+by\s+PR\s+(#?[1-9][0-9]*|https://github\.com/[^\s<>]+)\s*", re.I
)
TRUSTED_COMMENT_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}
RESOLUTION_MODE = "daily_code_resolution"


def deadline_for_run(schedule, triggered_at, minutes):
    if schedule is None or triggered_at is None:
        return None
    anchor = triggered_at.replace(tzinfo=UTC) if triggered_at.tzinfo is None else triggered_at
    next_run = schedule_trigger(*schedule).get_next_fire_time(
        None, anchor + timedelta(microseconds=1)
    )
    return (
        next_run.astimezone(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)
        if next_run
        else None
    )


def case_labels(source):
    yield source.name
    if source.vllm_version:
        yield f"{source.name} ({source.vllm_version})"
    if source.hardware:
        yield f"{source.name} ({source.hardware})"
    if source.hardware and source.vllm_version:
        yield f"{source.name} ({source.hardware}, {source.vllm_version})"


def labels_for_cases(sources):
    labels = {}
    for failure_id, source in sources:
        for label in case_labels(source):
            labels.setdefault(label.strip().casefold(), set()).add(failure_id)
    return labels


def fix_references(comments, labels, repository):
    references = {}
    for comment in reversed(comments):
        for line in (comment.get("body") or "").splitlines():
            match = SOLVED_BY.fullmatch(line.strip().removeprefix("- ").strip())
            if not match:
                continue
            owners = labels.get(match[1].strip().strip("`").casefold(), ())
            if len(owners) != 1:
                continue
            value = match[2]
            try:
                ref = located_pull_request(
                    {"pr_url": value}
                    if value.startswith("https://")
                    else {"pr_number": int(value.lstrip("#"))},
                    repository,
                )
            except ValueError:
                continue
            references.setdefault(next(iter(owners)), []).append((comment, ref))
    return references


async def latest_repair(client, references, parties, original, revert):
    branch = ((revert or original).get("base") or {}).get("ref")
    original_merged = utc_datetime(original.get("merged_at"))
    for comment, ref in references:
        number = ref["pr_number"]
        if number == original["number"] or (revert and number == revert["number"]):
            continue
        repair = await client.pull_request(number, missing_ok=True)
        if not repair or (repair.get("base") or {}).get("ref") != branch:
            continue
        commenter = ((comment.get("user") or {}).get("login") or "").lower()
        repair_author = ((repair.get("user") or {}).get("login") or "").lower()
        if not commenter or not (
            commenter in parties.values()
            or commenter == repair_author
            or comment.get("author_association") in TRUSTED_COMMENT_ROLES
        ):
            continue
        merged_at = utc_datetime(repair.get("merged_at"))
        if repair["merged"] and (
            not merged_at or (original_merged and merged_at <= original_merged)
        ):
            continue
        return {**ref, "comment_id": comment.get("id"), "merged": repair["merged"]}
    return None


def ready_code_merge():
    revert = aliased(Node)
    blocked = select(revert.id).where(
        revert.failure_id == Failure.id,
        revert.stage_code == "revert_pr",
        revert.status != "completed",
    ).correlate(Failure).exists()
    return and_(Node.stage_code == "pr_merge", Node.status.in_(("pending", "waiting", "failed")), ~blocked)


async def process_code_issue(tracking, issue_id, *, deadline_only=False):
    db = tracking.db
    issue = await db.get(Issue, issue_id)
    if issue is None or issue.state != "open" or issue.deleted_at is not None:
        return
    flow_identity = case(
        (Failure.processing_status.in_(ACTIVE), func.coalesce(Failure.shared_flow_owner_id, Failure.id)),
        else_=Failure.id,
    )
    sources = (
        await db.execute(
            select(flow_identity, SourceNode)
            .join(SourceNode, SourceNode.id == Failure.node_id)
            .where(Failure.issue_id == issue_id)
        )
    ).all()
    active_ids = list(
        await db.scalars(
            select(Failure.id)
            .join(Node, Node.failure_id == Failure.id)
            .where(
                Failure.issue_id == issue_id,
                Failure.problem_category == "code",
                Failure.processing_status.in_(ACTIVE),
                ready_code_merge(),
            )
        )
    )
    if not active_ids:
        return
    run = await db.get(Run, issue.run_id)
    if run is None or run.deleted_at is not None:
        return
    triggered_at = await db.scalar(
        select(func.max(History.triggered_at)).where(History.run_id == run.run_id)
    )
    schedule = await enabled_schedule(db, TRIGGER_TASK)
    config = await read_settings(db, REVERT_DEADLINE_KEY)
    deadline = deadline_for_run(
        schedule, triggered_at or run.started_at, revert_deadline_minutes(config)
    )
    if deadline_only and (deadline is None or now() < deadline):
        return
    repository, number = issue.repository, issue.number
    client = await tracking._client(repository)
    labels = labels_for_cases(sources)
    async with client:
        if (await client.issue(number)).get("state") != "open":
            return
        references = fix_references(await client.issue_comments(number), labels, repository)
        await db.rollback()
        for identity in active_ids:
            try:
                await advance_failure(
                    tracking,
                    client,
                    issue_id,
                    identity,
                    references.get(identity, ()),
                    labels,
                    deadline,
                )
            except (CollectionSourceError, ValueError):
                await db.rollback()
                logger.exception(
                    "Daily code failure sync failed failure_id=%s issue_id=%s", identity, issue_id
                )


async def advance_failure(tracking, client, issue_id, identity, references, labels, deadline):
    db = tracking.db
    failure = await db.scalar(
        select(Failure)
        .where(Failure.id == identity)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        failure is None
        or failure.issue_id != issue_id
        or failure.problem_category != "code"
        or failure.processing_status not in ACTIVE
    ):
        await db.rollback()
        return
    issue = await db.get(Issue, issue_id, populate_existing=True)
    if issue is None or issue.state != "open" or issue.deleted_at is not None:
        await db.rollback()
        return
    nodes = {
        node.stage_code: node
        for node in await db.scalars(
            select(Node)
            .where(Node.failure_id == identity)
            .execution_options(populate_existing=True)
        )
    }
    node = nodes.get("pr_merge")
    if node is None or node.status not in ("pending", "waiting", "failed"):
        await db.rollback()  # Respect an in-flight manually registered wait.
        return
    revert_node = nodes.get("revert_pr")
    revert_data = {}
    if revert_node:
        if revert_node.status != "completed":
            await db.rollback()
            return
        revert_data = await completed_node_result(db, revert_node.id)
        if not revert_data:
            await db.rollback()
            return
    original_data = {"pr_url": revert_data.get("original_pr_url")}
    if not original_data["pr_url"]:
        locating = nodes.get("locating")
        original_data = await completed_node_result(db, locating.id if locating else None)
        if not revert_node and original_data.get("revert_mode") != OPTIMIZATION_MODE:
            await db.rollback()
            return
    original_ref = located_pull_request(original_data, issue.repository)
    revert_ref = located_pull_request(revert_data, issue.repository) if revert_data else None
    if revert_ref and revert_ref["pr_number"] == original_ref["pr_number"]:
        raise ValueError("回退 PR 和原问题 PR 不能是同一个")
    executions = list(
        await db.scalars(
            select(Execution)
            .where(Execution.node_id == node.id)
            .order_by(Execution.attempt_no.desc())
        )
    )
    latest = next((row for row in executions if not (row.request_data or {}).get("purpose")), None)
    managed = (
        latest if latest and (latest.request_data or {}).get("mode") == RESOLUTION_MODE else None
    )
    if node.status == "waiting" and managed is None:
        await db.rollback()
        return
    prior_result = (managed.result_data if managed else None) or {}
    revert = await client.pull_request(revert_ref["pr_number"]) if revert_ref else None
    original = await client.pull_request(original_ref["pr_number"])
    branch = ((revert or original).get("base") or {}).get("ref")
    run = await db.get(Run, issue.run_id)
    if not branch or run is None or (run.source_branch and run.source_branch != branch):
        raise ValueError("PR 的目标分支与 Daily 执行分支不一致")
    parties = pr_recipients(original)
    repair = await latest_repair(client, references, parties, original, revert)
    point = now()
    due = deadline is not None and point >= deadline
    previous = nodes.get("problem_pr_notification")
    if (
        not (due or (revert and revert["merged"]) or (repair and repair["merged"]))
        and previous
        and previous.status != "completed"
    ):
        await db.rollback()
        return
    result = {
        "repair": repair,
        "deadline_at": deadline.isoformat() + "Z" if deadline else None,
        "original_pr_url": original_ref["pr_url"],
        "revert_pr_url": revert_ref["pr_url"] if revert_ref else None,
        "original_pr_author": parties.get("author"),
        "original_pr_merger": parties.get("merger"),
        "deadline_notified": bool(
            prior_result.get("deadline_notified") or prior_result.get("revert_attempted")
        ),
    }
    resolved = None
    alert = False
    if revert and revert["merged"]:
        resolved, message = True, "回退 PR 已合入"
    elif repair and repair["merged"]:
        resolved, message = True, "修复 PR 已合入"
    elif due and not result["deadline_notified"]:
        if (await client.issue(issue.number)).get("state") != "open":
            await db.rollback()
            return
        fresh_references = fix_references(
            await client.issue_comments(issue.number), labels, issue.repository
        )
        repair = result["repair"] = await latest_repair(
            client,
            fresh_references.get(identity, ()),
            parties,
            original,
            revert,
        )
        if repair and repair["merged"]:
            resolved, message = True, "修复 PR 已合入"
        else:
            if revert_ref:
                revert = await client.pull_request(revert_ref["pr_number"])
            if revert and revert["merged"]:
                resolved, message = True, "回退 PR 已合入"
            else:
                alert = result["deadline_notified"] = True
                message = deadline_message(revert, repair)
    elif result["deadline_notified"]:
        message = deadline_message(revert, repair)
    else:
        message = "等待修复 PR 合入" if repair else "等待 Issue 评论登记修复 PR"
    if (
        managed
        and managed.status == "waiting"
        and resolved is None
        and result == prior_result
        and message == managed.message
    ):
        await db.rollback()
        return
    attempt = executions[0].attempt_no + 1 if executions else 1
    if managed is None or managed.status != "waiting":
        managed = Execution(
            node_id=node.id,
            attempt_no=attempt,
            source="auto",
            status="waiting",
            request_data={"mode": RESOLUTION_MODE},
            started_at=point,
        )
        db.add(managed)
        attempt += 1
    if resolved is None:
        managed.result_data, managed.message = result, message
        node.status, node.updated_at = "waiting", point
        node.started_at = node.started_at or point
        if result["deadline_notified"]:
            failure.processing_status = "unresolved"
            await sync_shared_processing(db, failure)
        if alert:
            recipients = await failure_notification_recipients(db)
            source = await db.get(SourceNode, failure.node_id)
            enqueue_deadline_notices(
                db, node.id, attempt, source, issue, recipients, parties, result, message, point
            )
        await db.commit()
        return
    if previous and previous.status != "completed":
        previous.status = "completed"
        previous.updated_at = previous.finished_at = point
        await db.execute(
            update(Execution)
            .where(
                Execution.node_id == previous.id,
                Execution.status.in_(("waiting", "running")),
            )
            .values(status="ended", finished_at=point)
        )
    await tracking._complete_node(failure, node, managed, resolved, result, message)
    await tracking._commit_node_completion(failure, None)
    logger.info(
        "Daily code failure decision failure_id=%s resolved=%s result=%s",
        identity,
        resolved,
        message,
    )


def deadline_message(revert, repair):
    if revert is None:
        return "等待优化修复 PR 合入" if repair else "等待 Issue 评论登记优化修复 PR"
    if revert.get("state") == "closed":
        return "回退 PR 已关闭且未合入，请人工处理"
    return "等待人工合入回退 PR"


def enqueue_deadline_notices(
    db, node_id, attempt, source, issue, recipients, parties, result, message, point
):
    roles = {}
    revert_url = result.get("revert_pr_url")
    action = "回退" if revert_url else "优化修复"
    for name in recipients:
        roles.setdefault(name.lower(), []).append(f"请跟踪{action} PR 合入进展。")
    if merger := parties.get("merger"):
        roles.setdefault(merger, []).append(f"请处理{action} PR 合入。")
    if author := parties.get("author"):
        roles.setdefault(author, []).append(
            "请知悉，本次失败已进入回退处理。" if revert_url else "请更新优化修复进展。"
        )
    links = []
    if revert_url:
        links.append(f"回退 PR：{revert_url}")
    elif repair := result.get("repair"):
        links.append(f"优化修复 PR：{repair['pr_url']}")
    for offset, (name, actions) in enumerate(roles.items()):
        content = "\n".join(
            (
                f"{name}，Daily 用例 {source.name} 的修复 PR 到截止点仍未合入。",
                message,
                *actions,
                f"原问题 PR：{result['original_pr_url']}",
                *links,
                f"Issue：https://github.com/{issue.repository}/issues/{issue.number}",
            )
        )
        add_failure_notification(
            db, node_id, attempt + offset, name, content, point, purpose="revert_deadline"
        )
