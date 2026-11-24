"""Transactional tracking persistence, independent of mutable collection nodes."""
import json
from datetime import UTC, datetime

from sqlalchemy import or_, select, update

from infrastructure.persistence.daily_pipeline_merged_pr import link_merged_prs_to_issue
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as CollectionRun
from infrastructure.persistence.models import DailyPipelineConfig as Config
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution
from infrastructure.persistence.models import DailyPipelineTestCase as Case
from infrastructure.persistence.models import DailyPipelineTriggerHistory as History
from infrastructure.persistence.models import DailyPipelineVersionFlow as VersionFlow

ACTIVE = ("pending", "running", "unresolved")
AUTOMATIC_STAGES = ("revert_pr", "problem_pr_notification")
FAILURE_NOTIFICATION_STAGE = "failure_notification"
VERSION_BUILD_NOTICE = "version_build_notice"
STANDALONE_NOTICE_PURPOSES = ("problem_pr", "revert_deadline", VERSION_BUILD_NOTICE)
NOTIFICATION_STAGES = (FAILURE_NOTIFICATION_STAGE, "problem_pr_notification")
FAILURE_NOTIFICATION_RECIPIENTS = "failure_notification_recipients"
CATEGORIES = {"code": "代码问题", "version": "版本配套问题", "environment": "环境问题"}
STAGES = {FAILURE_NOTIFICATION_STAGE: "失败通知", "classify": "问题分类", "locating": "问题定位", "revert_pr": "回退PR", "problem_pr_notification": "问题PR通知", "pr_merge": "PR合入", "image_rebuild": "重新构建镜像", "mark_success": "标记成功"}
STAGES[VERSION_BUILD_NOTICE] = "版本配套镜像构建通知"
PATHS = {"code": ("revert_pr", "problem_pr_notification", "pr_merge"), "version": ("revert_pr", "pr_merge", "image_rebuild"), "environment": ("mark_success",)}

def now():
    return datetime.now(UTC).replace(tzinfo=None)

async def active_daily_failure_times(db):
    repository = await db.scalar(select(Config.config_value).where(Config.config_key == "repository"))
    rows = (await db.execute(
        select(SourceNode.name, SourceNode.hardware, Failure.discovered_at)
        .join(Failure, Failure.node_id == SourceNode.id)
        .join(CollectionRun, CollectionRun.run_id == SourceNode.run_id)
        .where(CollectionRun.repository == repository, SourceNode.case_type == "Daily", Failure.processing_status.in_(ACTIVE))
    )).tuples().all()
    latest = {}
    for name, hardware, discovered_at in rows:
        key = (name, (hardware or "").lower())
        latest[key] = max(latest.get(key, discovered_at), discovered_at)
    return latest


def is_case_fault_blocked(case, failure_times):
    discovered_at = failure_times.get((case.name, case.hardware.lower()))
    return discovered_at is not None and (case.manual_enabled_at is None or discovered_at > case.manual_enabled_at)


async def reconcile_case_availability(db, cases=None):
    """Manual enable overrides existing faults until a new failure is discovered."""
    await db.flush()
    if cases is None:
        cases = list(await db.scalars(select(Case).order_by(Case.name, Case.hardware).with_for_update().execution_options(populate_existing=True)))
    failure_times = await active_daily_failure_times(db)
    for case in cases:
        case.auto_disabled = is_case_fault_blocked(case, failure_times)
    await db.flush()

def checkpoint_time(value):
    return value.isoformat() if value else None


def checkpoint_datetime(value):
    return datetime.fromisoformat(value) if value else None


async def sync_shared_processing(db, owner):
    """Apply the common processing result to the still-active participants."""
    shared_id = getattr(owner, "shared_flow_owner_id", None)
    if shared_id is None or shared_id != owner.id:
        return
    await db.flush()
    members = list(await db.scalars(select(Failure).where(
        Failure.shared_flow_owner_id == owner.id, Failure.id != owner.id,
        Failure.processing_status.in_(ACTIVE),
    ).order_by(Failure.id).with_for_update().execution_options(populate_existing=True)))
    if members and owner.problem_category == "version" and owner.version_flow_id is None:
        flow = VersionFlow(status="waiting_resolution", updated_at=now())
        db.add(flow)
        await db.flush()
        owner.version_flow_id = flow.id
    for member in members:
        member.problem_category = owner.problem_category
        member.processing_status = owner.processing_status
        member.ended_at = owner.ended_at
        if owner.problem_category == "version":
            member.version_flow_id = owner.version_flow_id


async def transfer_shared_processing(db, owner):
    """Keep the same node and execution IDs when the owner is superseded."""
    shared_id = getattr(owner, "shared_flow_owner_id", None)
    if shared_id is None or shared_id != owner.id:
        return
    members = list(await db.scalars(select(Failure).where(
        Failure.shared_flow_owner_id == owner.id, Failure.id != owner.id,
    ).order_by(Failure.id).with_for_update().execution_options(populate_existing=True)))
    successor = next((member for member in members if member.processing_status in ACTIVE), None)
    if successor is None:
        successor = next((member for member in members if member.end_reason == "issue_closed"), None)
    if successor is None:
        return

    nodes = list(await db.scalars(select(Node).where(
        Node.failure_id.in_((owner.id, successor.id)),
    ).order_by(Node.id).with_for_update().execution_options(populate_existing=True)))
    notices = {node.failure_id: node for node in nodes if node.stage_code == FAILURE_NOTIFICATION_STAGE}
    old_notice, new_notice = notices.get(owner.id), notices.get(successor.id)
    common = [node for node in nodes if node.failure_id == owner.id and
              node.stage_code not in (FAILURE_NOTIFICATION_STAGE, VERSION_BUILD_NOTICE)]
    archived = [(node, node.stage_code) for node in nodes if node.failure_id == successor.id and
                node.stage_code not in (FAILURE_NOTIFICATION_STAGE, VERSION_BUILD_NOTICE)]

    # Vacate the successor's stage names before moving the live timeline.
    # Everything stays in one transaction and execution IDs are unaffected.
    for node, _ in archived:
        node.stage_code = f"move_{node.id}"
    if archived:
        await db.flush()
    for node in common:
        node.failure_id = successor.id
        if old_notice and node.previous_node_id == old_notice.id:
            node.previous_node_id = new_notice.id if new_notice and old_notice.status != "completed" else None
    if common:
        await db.flush()
    for node, stage in archived:
        node.failure_id, node.stage_code = owner.id, stage
        if new_notice and node.previous_node_id == new_notice.id:
            node.previous_node_id = old_notice.id if old_notice else None

    if successor.end_reason == "issue_closed" and owner.resume_state:
        owner_state, successor_state = owner.resume_state, successor.resume_state or {}
        moving_ids = {node.id for node in common}
        successor.resume_state = {
            "status": owner_state.get("status"),
            "nodes": [*successor_state.get("nodes", ()), *(
                item for item in owner_state.get("nodes", ()) if item["id"] in moving_ids
            )],
            "executions": [*successor_state.get("executions", ()), *owner_state.get("executions", ())],
        }
    for member in (owner, *members):
        member.shared_flow_owner_id = successor.id
    await db.flush()


async def end_failure(db, failure, reason, replacement=None):
    if reason == "superseded" and getattr(failure, "shared_flow_owner_id", None) and (
        failure.processing_status in ACTIVE or failure.end_reason == "issue_closed"
    ):
        await transfer_shared_processing(db, failure)
        if failure.end_reason == "issue_closed":
            failure.end_reason, failure.replaced_by_id, failure.resume_state = reason, replacement, None
    if failure.processing_status not in ACTIVE:
        return
    point = now()
    nodes = list(await db.scalars(select(Node).where(Node.failure_id == failure.id).with_for_update()))
    executions = list(await db.scalars(select(Execution).where(
        Execution.node_id.in_([node.id for node in nodes]), Execution.status.in_(("running", "waiting")),
    ))) if nodes else []
    if reason == "issue_closed":
        failure.resume_state = {
            "status": failure.processing_status,
            "nodes": [{"id": node.id, "status": node.status,
                       "finished_at": checkpoint_time(node.finished_at),
                       "next_run_at": checkpoint_time(node.next_run_at)}
                      for node in nodes if node.status in ("pending", "running", "waiting", "failed")],
            "executions": [{"id": execution.id, "status": execution.status,
                            "finished_at": checkpoint_time(execution.finished_at)} for execution in executions],
        }
    else:
        failure.resume_state = None
    failure.processing_status = "ended"
    failure.end_reason = reason
    failure.replaced_by_id = replacement
    failure.ended_at = point
    for node in nodes:
        if node.status in ("pending", "running", "waiting"):
            node.status = "ended"
            node.finished_at = node.updated_at = point
            node.next_run_at = None
    if nodes:
        purpose = Execution.request_data["purpose"].as_string()
        await db.execute(update(Execution).where(
            Execution.node_id.in_([n.id for n in nodes]), Execution.status.in_(("running", "waiting")),
            or_(purpose.is_(None), purpose.not_in(("revert_deadline", VERSION_BUILD_NOTICE))),
        ).values(status="ended", finished_at=point))


async def latest_failure_for_case(db, failure):
    source = await db.get(SourceNode, failure.node_id)
    repository = await db.scalar(select(CollectionRun.repository).where(CollectionRun.run_id == source.run_id))
    peers = (await db.execute(select(Failure, SourceNode)
        .join(SourceNode, SourceNode.id == Failure.node_id)
        .join(CollectionRun, CollectionRun.run_id == SourceNode.run_id)
        .where(CollectionRun.repository == repository, SourceNode.name == source.name,
               SourceNode.hardware == source.hardware, SourceNode.case_type == source.case_type,
               SourceNode.vllm_ref == source.vllm_ref))).all()
    return max(peers, key=lambda pair: (pair[1].execution_started_at or pair[0].discovered_at,
                                        pair[1].source_id or 0, pair[0].id))[0]


async def failure_notification_recipients(db, *, for_update=False):
    query = select(Config.config_value).where(Config.config_key == FAILURE_NOTIFICATION_RECIPIENTS)
    value = await db.scalar(query.with_for_update() if for_update else query)
    return json.loads(value or "[]")


async def prepare_failure_notifications(db, failure, node, source, repository, issue, recipients):
    if not recipients:
        return
    job_url = f"https://github.com/{repository}/actions/runs/{source.source_run_id or source.run_id}"
    if source.source_id:
        job_url += f"/job/{source.source_id}"
    lines = [
        "Daily 测试发现失败用例，请及时关注处理进展。",
        f"用例：{source.name}",
        f"类型：{'Daily 用例' if source.case_type == 'Daily' else source.case_type}",
        f"硬件：{source.hardware or '—'}",
    ]
    if source.vllm_version:
        lines.append(f"vLLM 版本：{source.vllm_version}")
    lines.extend([
        f"失败 Job：{job_url}",
        f"Daily 主流水线：https://github.com/{repository}/actions/runs/{source.run_id}",
    ])
    if issue is not None:
        lines.append(f"Issue：https://github.com/{issue.repository}/issues/{issue.number}")
    point = now()
    content = "\n".join(lines)
    for attempt, github_name in enumerate(recipients, 1):
        add_failure_notification(db, node.id, attempt, github_name, content, point)
    node.status = "waiting"
    node.started_at = node.started_at or point
    node.updated_at = point
    failure.processing_status = "running"


def add_failure_notification(db, node_id, attempt, github_name, content, point, *, purpose=None):
    db.add(Execution(
        node_id=node_id, attempt_no=attempt, source="auto", status="waiting",
        request_data={
            "github_name": github_name, "content": content,
            "delivery_mode": "pull", "ready": True, "delivery_started": False,
            **({"purpose": purpose} if purpose else {}),
        },
        message=f"{github_name}：等待通知消费", started_at=point,
    ))


def pr_recipients(pr):
    return {
        role: login.lower()
        for role, user in (("author", pr.get("user")), ("merger", pr.get("merged_by")))
        if (login := (user or {}).get("login"))
    }


async def completed_node_result(db, node_id):
    if node_id is None:
        return {}
    return await db.scalar(
        select(Execution.result_data)
        .where(Execution.node_id == node_id, Execution.status == "completed")
        .order_by(Execution.attempt_no.desc())
        .limit(1)
    ) or {}


async def latest_failure_notifications(db, node_id):
    executions = await db.scalars(
        select(Execution).where(Execution.node_id == node_id, Execution.source == "auto")
        .order_by(Execution.attempt_no).with_for_update().execution_options(populate_existing=True)
    )
    latest = {}
    for execution in executions:
        data = execution.request_data or {}
        if data.get("delivery_mode") == "pull":
            latest[data["github_name"]] = execution
    return list(latest.values())


async def retry_failure_notifications(db, node):
    executions = await latest_failure_notifications(db, node.id)
    retry = [execution for execution in executions if execution.status == "failed"]
    if not retry:
        raise ValueError("没有发送失败的通知可重试")
    attempt = max(execution.attempt_no for execution in executions)
    point = now()
    for previous in retry:
        attempt += 1
        data = previous.request_data
        add_failure_notification(db, node.id, attempt, data["github_name"], data["content"], point)
    node.status = "waiting"
    node.finished_at = None
    node.updated_at = point


async def resume_issue_failures(db, issue):
    failures = list(await db.scalars(select(Failure).where(
        Failure.issue_id == issue.id, Failure.processing_status == "ended", Failure.end_reason == "issue_closed",
    ).with_for_update().execution_options(populate_existing=True)))
    superseded = []
    for failure in failures:
        latest = await latest_failure_for_case(db, failure)
        if failure.replaced_by_id or latest.id != failure.id:
            failure.end_reason = "superseded"
            failure.replaced_by_id = failure.replaced_by_id or latest.id
            superseded.append(failure)
    if any(getattr(failure, "shared_flow_owner_id", None) for failure in superseded):
        await db.flush()
    for failure in superseded:
        await transfer_shared_processing(db, failure)
        failure.resume_state = None
    for failure in failures:
        if failure.end_reason == "superseded":
            continue
        saved = failure.resume_state or {}
        nodes = list(await db.scalars(select(Node).where(Node.failure_id == failure.id).order_by(Node.id).with_for_update().execution_options(populate_existing=True)))
        node_states = {item["id"]: item for item in saved.get("nodes", [])}
        execution_states = {item["id"]: item for item in saved.get("executions", [])}
        for node in nodes:
            previous = node_states.get(node.id)
            if previous:
                node.status = "pending" if previous["status"] == "running" else previous["status"]
                node.finished_at = checkpoint_datetime(previous.get("finished_at"))
                node.next_run_at = checkpoint_datetime(previous.get("next_run_at"))
            elif node.status == "ended" and not saved:
                node.status, node.finished_at, node.next_run_at = "pending", None, None
            else:
                continue
            node.updated_at = now()
            if node.stage_code == FAILURE_NOTIFICATION_STAGE:
                deliveries = await latest_failure_notifications(db, node.id)
                if node.status == "pending" and not deliveries:
                    source = await db.get(SourceNode, failure.node_id)
                    recipients = await failure_notification_recipients(db, for_update=True)
                    await prepare_failure_notifications(db, failure, node, source, issue.repository, issue, recipients)
                for execution in deliveries:
                    if execution.status != "ended" or execution_states.get(execution.id, {}).get("status") != "waiting":
                        continue
                    execution.status = "waiting"
                    execution.finished_at = None
                    data = execution.request_data
                    if data.get("delivery_started"):
                        execution.result_data = {
                            **(execution.result_data or {}), "delivery_mode": "pull", "delivery_status": "unknown",
                        }
                        execution.message = f"{data['github_name']}：请确认通知送达结果"
                continue
            purpose = Execution.request_data["purpose"].as_string()
            execution = await db.scalar(select(Execution).where(Execution.node_id == node.id, purpose.is_(None))
                .order_by(Execution.attempt_no.desc()).limit(1).execution_options(populate_existing=True))
            if execution is None or execution.status != "ended":
                continue
            data = execution.request_data or {}
            state = execution_states.get(execution.id, {})
            prepared_notification = node.stage_code == "problem_pr_notification" and data.get("delivery_mode") == "pull" and data.get("ready")
            waiting_external_result = node.stage_code in ("pr_merge", "image_rebuild") and bool(execution.result_data)
            if state.get("status") == "waiting" or prepared_notification or (not saved and waiting_external_result):
                # Reuse the original receipt/result wait. A restart must not resend
                # an already claimed notification or repeat an external mutation.
                node.status = execution.status = "waiting"
                node.finished_at = execution.finished_at = None
                if prepared_notification and data.get("delivery_started"):
                    execution.result_data = {**(execution.result_data or {}), "delivery_mode": "pull", "delivery_status": "unknown"}
                    execution.message = "Issue 重新打开，保留原领取记录，请确认通知送达结果"
        failure.processing_status = "pending" if saved.get("status") == "pending" and not any(
            node.stage_code == FAILURE_NOTIFICATION_STAGE and node.status == "waiting" for node in nodes
        ) else "running"
        failure.end_reason = failure.ended_at = failure.resume_state = None


async def save_issue(db, repository, run_id, payload, *, allow_reopen=False):
    # Acquire the run before the repository lock, as collection and auto-close do.
    await db.scalar(select(CollectionRun.run_id).where(CollectionRun.run_id == run_id).with_for_update())
    await db.scalar(select(Config.id).where(Config.config_key == "repository").with_for_update())
    issue = await db.scalar(select(Issue).where(
        Issue.repository == repository, Issue.number == payload["number"],
    ).with_for_update().execution_options(populate_existing=True))
    if issue is None:
        issue = Issue(repository=repository, number=payload["number"], run_id=run_id, title=payload.get("title") or str(payload["number"]), state=payload["state"])
        db.add(issue)
        await db.flush()
    # A poll started before closure may still return an open Issue.
    if issue.state != "closed" or payload["state"] != "open" or allow_reopen:
        issue.state = payload["state"]
        if payload.get("title"):
            issue.title = payload["title"]
        if "closed_at" in payload:
            issue.closed_at = payload["closed_at"]
        elif issue.state == "closed" and issue.closed_at is None:
            issue.closed_at = now()
    await db.execute(update(CollectionRun).where(CollectionRun.run_id == run_id).values(issue_number=issue.number, issue_state=issue.state))
    await link_merged_prs_to_issue(db, repository, run_id, issue.id)
    await db.execute(update(Failure).where(Failure.node_id.in_(select(SourceNode.id).where(SourceNode.run_id == run_id)), Failure.issue_id.is_(None)).values(issue_id=issue.id))
    if issue.state == "closed":
        failures = list(await db.scalars(select(Failure).where(Failure.issue_id == issue.id, Failure.processing_status.in_(ACTIVE)).with_for_update()))
        for failure in failures:
            await end_failure(db, failure, "issue_closed")
    elif issue.state == "open":
        if allow_reopen:
            await resume_issue_failures(db, issue)
        issue.closed_at = None
    await reconcile_case_availability(db)
    return issue

async def ingest_snapshot(db, run_id, snapshot, resolved):
    history = await db.scalar(select(History).where(History.run_id == run_id).order_by(History.id.desc()).limit(1))
    if history is None:
        return
    repository = history.repository
    await db.scalar(select(Config.id).where(Config.config_key == "repository").with_for_update())
    root = snapshot["run"]
    recipients = await failure_notification_recipients(db, for_update=True)
    issue = None
    if root.get("issue_number"):
        issue = await save_issue(db, repository, run_id, {"number": root["issue_number"], "state": root["issue_state"], "title": root.get("issue_title"), "closed_at": root.get("issue_closed_at")})
    for item in snapshot["nodes"]:
        if item["node_type"] != "test_job" or item["result"] != "failure":
            continue
        source = resolved[item["ref"]]
        if await db.scalar(select(Failure.id).where(Failure.node_id == source.id)):
            continue
        failure = Failure(node_id=source.id, issue_id=issue.id if issue else None, processing_status="pending", discovered_at=now())
        db.add(failure)
        await db.flush()
        previous = (await db.execute(select(Failure, SourceNode).join(SourceNode, SourceNode.id == Failure.node_id).join(CollectionRun, CollectionRun.run_id == SourceNode.run_id).where(CollectionRun.repository == repository, SourceNode.name == source.name, SourceNode.hardware == source.hardware, SourceNode.case_type == source.case_type, SourceNode.vllm_ref == source.vllm_ref, Failure.id != failure.id).with_for_update())).all()
        point = source.execution_started_at or history.triggered_at
        newer = [(f,n) for f,n in previous if (n.execution_started_at or f.discovered_at,n.source_id) > (point,source.source_id)]
        if newer:
            latest = max(newer,key=lambda pair:(pair[1].execution_started_at or pair[0].discovered_at,pair[1].source_id))[0]
            await end_failure(db,failure,"superseded",latest.id)
            continue
        for old,_ in previous:
            await end_failure(db,old,"superseded",failure.id)
        point = now()
        notification = Node(
            failure_id=failure.id, stage_code=FAILURE_NOTIFICATION_STAGE, status="pending", updated_at=point,
        )
        db.add(notification)
        await db.flush()
        db.add(Node(
            failure_id=failure.id, previous_node_id=notification.id,
            stage_code="classify", status="pending", updated_at=point,
        ))
        await db.flush()
        if issue and issue.state == "closed":
            await end_failure(db, failure, "issue_closed")
        else:
            await prepare_failure_notifications(db, failure, notification, source, repository, issue, recipients)

    await reconcile_case_availability(db)
