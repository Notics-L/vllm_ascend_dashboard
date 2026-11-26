"""Version compatibility resolution, with explicitly shared failures and manual builds."""

import re
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select

from daily_pipeline.collection_source import utc_datetime
from daily_pipeline.processing_rollback import VERSION_STAGE_NAMES, version_can_rollback
from daily_pipeline.pull_requests import REPOSITORY_PATTERN, located_pull_request
from infrastructure.persistence.daily_pipeline_settings import (
    DEFAULT_VERSION_BUILD_WORKFLOW,
    read_settings,
)
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    VERSION_BUILD_NOTICE,
    add_failure_notification,
    failure_notification_recipients,
    now,
    reconcile_case_availability,
)
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution
from infrastructure.persistence.models import DailyPipelineVersionFlow as VersionFlow

NIGHTLY_BUILD_JOB = re.compile(r"^Build nightly-(a2|a3|a5|310p)(?:[ -]|$)", re.I)
BUILD_RUN_PATH = re.compile(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/([1-9][0-9]*)/?")
FLOW_MESSAGES = {
    "waiting_resolution": "等待登记修复或回退 PR，或人工确认外部配套已恢复",
    "waiting_pr": "等待修复或回退 PR 合入",
    "waiting_build": "等待登记镜像构建 Run 链接",
    "building": "跟踪镜像构建结果",
    "build_failed": "镜像构建未成功，可以重新构建并登记新的链接",
    "resolved": "镜像构建成功",
}


def new_flow(db):
    flow = VersionFlow(status="waiting_resolution", updated_at=now())
    db.add(flow)
    return flow


def parse_build_run(url, repository):
    parsed = urlsplit(url)
    match = BUILD_RUN_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or not match:
        raise ValueError("请填写完整的 GitHub Actions 构建 Run 链接")
    if match[1].lower() != repository.lower():
        raise ValueError("构建 Run 所属仓库与镜像构建配置不一致")
    return int(match[2]), f"https://github.com/{repository}/actions/runs/{match[2]}"


async def build_target(db, repository):
    settings = await read_settings(
        db, "version_image_build_repository", "version_image_build_workflow"
    )
    repository = settings.get("version_image_build_repository") or repository
    workflow = settings.get("version_image_build_workflow") or DEFAULT_VERSION_BUILD_WORKFLOW
    if not repository or not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("镜像构建仓库配置无效")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow):
        raise ValueError("镜像构建工作流配置无效")
    return {
        "repository": repository,
        "workflow": workflow,
        "trigger_url": f"https://github.com/{repository}/actions/workflows/{workflow}",
    }


async def flow_view(db, failure, run, issue):
    if failure.problem_category != "version":
        return None
    flow_id = getattr(failure, "version_flow_id", None)
    if not flow_id and failure.processing_status not in ACTIVE:
        return None  # Preserve completed legacy timelines.
    flow = await db.get(VersionFlow, flow_id) if flow_id else None
    count = (
        await db.scalar(
            select(func.count()).select_from(Failure).where(Failure.version_flow_id == flow_id)
        )
        if flow_id
        else 1
    )
    resolution = (flow.resolution or {}) if flow else {}
    try:
        target = resolution.get("build_target") or await build_target(db, run.repository)
    except ValueError:
        target = {}
    status = flow.status if flow else "waiting_resolution"
    issue_open = issue is None or issue.state == "open" and issue.deleted_at is None
    rollback_records = {}
    if flow:
        stage = Execution.request_data["stage_code"].as_string()
        for record in await db.scalars(
            select(Execution)
            .join(Node, Node.id == Execution.node_id)
            .join(Failure, Failure.id == Node.failure_id)
            .where(
                Failure.version_flow_id == flow.id,
                Execution.request_data["purpose"].as_string() == "process_rollback",
                stage.in_(tuple(VERSION_STAGE_NAMES)),
            )
            .order_by(Execution.id.desc())
        ):
            rollback_records.setdefault(record.request_data["stage_code"], []).append({
                "id": str(record.id), "author": record.operator,
                "createdAt": record.started_at.isoformat() + "Z",
                "source": "manual", "status": "completed", "content": record.message,
            })
    return {
        "failureId": str(failure.id),
        "status": status,
        "message": (flow.message if flow else None) or FLOW_MESSAGES[status],
        "triggerUrl": target.get("trigger_url"),
        "sourceBranch": run.source_branch,
        "resolution": resolution,
        "build": (flow.build or {}) if flow else {},
        "memberCount": count,
        "updatedAt": flow.updated_at.isoformat() + "Z" if flow else None,
        "canEdit": failure.processing_status in ACTIVE and issue_open,
        "rollbackStages": [
            stage for stage in VERSION_STAGE_NAMES if flow and issue_open
            and failure.processing_status in (*ACTIVE, "resolved") and version_can_rollback(status, stage)
        ],
        "rollbackRecords": rollback_records,
    }


async def lock_version_flows(db, flow_ids):
    return (
        {
            flow.id: flow
            for flow in await db.scalars(
                select(VersionFlow)
                .where(VersionFlow.id.in_(flow_ids))
                .order_by(VersionFlow.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        if flow_ids
        else {}
    )


async def lock_failures_and_flows(db, identities):
    query = select(Failure).where(Failure.id.in_(identities)).order_by(Failure.id)
    rows = list(await db.scalars(query))
    flows = await lock_version_flows(db, {row.version_flow_id for row in rows if row.version_flow_id})
    rows = list(await db.scalars(query.with_for_update().execution_options(populate_existing=True)))
    if len(rows) != len(identities):
        raise LookupError("失败用例不存在")
    if any(row.version_flow_id is not None and row.version_flow_id not in flows for row in rows):
        raise ValueError("处理流程已变化，请刷新后重试")
    return rows, flows


async def share_version_flow(db, identities, target_id):
    if len(identities) < 2 or target_id not in identities:
        raise ValueError("请选择至少两个用例并指定要保留的流程")
    rows, flows = await lock_failures_and_flows(db, identities)
    issue_ids = {row.issue_id for row in rows}
    if (
        len(issue_ids) != 1
        or None in issue_ids
        or any(
            row.problem_category != "version" or row.processing_status not in ACTIVE for row in rows
        )
    ):
        raise ValueError("只能共用同一个 Issue 下尚未结案的版本配套失败用例")
    issue = await db.get(Issue, rows[0].issue_id)
    if issue is None or issue.state != "open" or issue.deleted_at is not None:
        raise ValueError("关联 Issue 已结束")
    target = next(row for row in rows if row.id == target_id)
    flow = flows.get(target.version_flow_id)
    for source_id, source_flow in flows.items():
        if source_id == target.version_flow_id:
            continue
        if source_flow.status != "waiting_resolution":
            raise ValueError("选中的其他流程已有处理进度，请选择保留已有进度的流程")
        selected_count = sum(row.version_flow_id == source_id for row in rows)
        member_count = await db.scalar(
            select(func.count()).select_from(Failure).where(Failure.version_flow_id == source_id)
        )
        if member_count != selected_count:
            raise ValueError("共用流程不能拆分，请选中该流程的所有成员或选择保留该流程")
    flow_id = await attach_version_flow(db, rows, flow)
    await db.commit()
    return {"flowId": str(flow_id)}


async def attach_version_flow(db, rows, flow):
    if flow is None:
        flow = new_flow(db)
        await db.flush()
    added = any(row.version_flow_id != flow.id for row in rows)
    for row in rows:
        row.version_flow_id = flow.id
        row.problem_category = "version"
    if added and flow.notified_at and flow.status in ("waiting_build", "building"):
        await enqueue_build_notice(db, flow, shared_update=True)
    await update_member_status(db, flow)
    return flow.id


async def active_members(db, flow, *, for_update=False):
    query = (
        select(Failure)
        .where(
            Failure.version_flow_id == flow.id,
            Failure.problem_category == "version",
            Failure.processing_status.in_(ACTIVE),
        )
        .order_by(Failure.id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        query = query.with_for_update()
    return list(await db.scalars(query))


async def update_member_status(db, flow):
    await db.flush()
    for failure in await active_members(db, flow, for_update=True):
        if flow.status == "resolved":
            failure.processing_status, failure.ended_at = "resolved", now()
        elif flow.status == "build_failed":
            failure.processing_status = "unresolved"
        else:
            failure.processing_status = "running"
    if flow.status == "resolved":
        await reconcile_case_availability(db)


async def submit_version_action(tracking, failure_id, action, actor, data):
    db = tracking.db
    (failure,), flows = await lock_failures_and_flows(db, {failure_id})
    if failure.problem_category != "version" or failure.processing_status not in ACTIVE:
        raise ValueError("仅可处理尚未结案的版本配套失败")
    issue = await db.get(Issue, failure.issue_id) if failure.issue_id else None
    if issue and (issue.state != "open" or issue.deleted_at is not None):
        raise ValueError("关联 Issue 已结束")
    source = await db.get(SourceNode, failure.node_id)
    run = await db.get(Run, source.run_id)
    flow = flows.get(failure.version_flow_id)
    if flow is None:
        flow = new_flow(db)
        await db.flush()
        failure.version_flow_id = flow.id
    point = now()
    if action == "external_recovered" and flow.status == "waiting_resolution":
        note = (data.get("message") or "").strip()
        flow.resolution = {
            "kind": action,
            "confirmed_by": actor,
            "note": note,
            "build_target": await build_target(db, run.repository),
        }
        flow.status, flow.ready_at = "waiting_build", point
    elif action == "track_pr" and flow.status in ("waiting_resolution", "waiting_pr"):
        references = {
            kind: located_pull_request({"pr_url": data[f"{kind}_url"]})
            for kind in ("repair", "revert")
            if data.get(f"{kind}_url")
        }
        if not references:
            raise ValueError("请填写修复 PR 或回退 PR 的完整链接")
        flow.resolution = {"kind": action, "confirmed_by": actor, "references": references}
        flow.status = "waiting_pr"
    elif action == "track_build" and flow.status in ("waiting_build", "build_failed"):
        target = flow.resolution["build_target"]
        run_id, url = parse_build_run(data.get("build_url") or "", target["repository"])
        client = await tracking._client(target["repository"])
        async with client:
            remote = await client.run(run_id)
        verify_build_run(remote, target)
        flow.build = {"run_id": run_id, "url": url, "registered_by": actor}
        flow.status = "building"
    else:
        raise ValueError("当前流程状态不接受该操作，请刷新后重试")
    flow.message, flow.updated_at = FLOW_MESSAGES[flow.status], point
    if flow.status == "waiting_build":
        await enqueue_build_notice(db, flow)
    await update_member_status(db, flow)
    await db.commit()
    return {"status": flow.status}


def verify_build_run(remote, target):
    path = (remote.get("path") or "").split("@", 1)[0]
    if path != f".github/workflows/{target['workflow']}":
        raise ValueError("构建链接不是配置的镜像构建工作流")


async def enqueue_build_notice(db, flow, *, shared_update=False):
    if flow.notified_at and not shared_update:
        return
    recipients = await failure_notification_recipients(db)
    members = await active_members(db, flow)
    if not recipients or not members:
        return
    first = members[0]
    source = await db.get(SourceNode, first.node_id)
    run = await db.get(Run, source.run_id)
    issue = await db.get(Issue, first.issue_id) if first.issue_id else None
    point = now()
    node = await db.scalar(
        select(Node).where(Node.failure_id == first.id, Node.stage_code == VERSION_BUILD_NOTICE)
    )
    if node is None:
        node = Node(
            failure_id=first.id,
            stage_code=VERSION_BUILD_NOTICE,
            status="completed",
            updated_at=point,
            started_at=point,
            finished_at=point,
        )
        db.add(node)
        await db.flush()
    last = (
        await db.scalar(select(func.max(Execution.attempt_no)).where(Execution.node_id == node.id))
        or 0
    )
    target = flow.resolution["build_target"]
    content = [
        (
            f"Daily Run #{run.run_id} 的版本配套流程已合并。如已触发构建，登记该 Run 即可。"
            if shared_update
            else f"Daily Run #{run.run_id} 的版本配套已具备构建条件，请登记对应的镜像构建 Run。"
        ),
        f"构建入口：{target['trigger_url']}",
        f"待构建分支：{run.source_branch or '以合入或恢复的目标分支为准'}",
        "请在「问题跟踪 → 失败用例 → 处理进展」登记实际构建 Run 链接。",
    ]
    if len(members) > 1:
        content.append(f"本次有 {len(members)} 条失败用例共用版本配套流程，只需构建和登记一次。")
    if flow.build and flow.build.get("url"):
        content.append(f"正在跟踪的构建：{flow.build['url']}")
    if issue:
        content.append(f"Issue：https://github.com/{issue.repository}/issues/{issue.number}")
    for attempt, recipient in enumerate(dict.fromkeys(recipients), last + 1):
        add_failure_notification(
            db, node.id, attempt, recipient, "\n".join(content), point, purpose=VERSION_BUILD_NOTICE
        )
    flow.notified_at = point


async def version_work_ids(db):
    return list(
        await db.scalars(
            select(VersionFlow.id)
            .join(Failure, Failure.version_flow_id == VersionFlow.id)
            .where(
                Failure.processing_status.in_(ACTIVE),
                Failure.problem_category == "version",
                or_(
                    VersionFlow.status.in_(("waiting_pr", "building")),
                    (VersionFlow.status == "waiting_build") & VersionFlow.notified_at.is_(None),
                ),
            )
            .distinct()
            .order_by(VersionFlow.id)
        )
    )


async def advance_version_flow(tracking, flow_id):
    db = tracking.db
    flow = await db.scalar(select(VersionFlow).where(VersionFlow.id == flow_id).with_for_update())
    if flow is None or flow.status not in ("waiting_pr", "waiting_build", "building"):
        return
    members = await active_members(db, flow, for_update=True)
    if not members:
        return
    first = members[0]
    issue = await db.get(Issue, first.issue_id) if first.issue_id else None
    if issue and (issue.state != "open" or issue.deleted_at is not None):
        return
    source = await db.get(SourceNode, first.node_id)
    run = await db.get(Run, source.run_id)
    point = now()
    if flow.status == "waiting_pr":
        for kind, ref in flow.resolution["references"].items():
            client = await tracking._client(ref["pr_repository"])
            async with client:
                remote = await client.pull_request(ref["pr_number"], missing_ok=True)
            if not remote or not remote["merged"]:
                continue
            flow.resolution = {
                **flow.resolution,
                "merged_pr": ref,
                "merged_kind": kind,
                "build_target": await build_target(db, run.repository),
            }
            flow.status, flow.ready_at = (
                "waiting_build",
                utc_datetime(remote.get("merged_at")) or point,
            )
            flow.message = (
                "修复 PR" if kind == "repair" else "回退 PR"
            ) + " 已合入，等待登记镜像构建链接"
            break
        else:
            return
    if flow.status == "waiting_build":
        await enqueue_build_notice(db, flow)
    elif flow.status == "building":
        target = flow.resolution["build_target"]
        client = await tracking._client(target["repository"])
        async with client:
            remote = await client.run(flow.build["run_id"])
            verify_build_run(remote, target)
            if remote.get("status") != "completed":
                return
            conclusion = remote.get("conclusion")
            build_error = None
            if conclusion == "success" and target["workflow"] in (
                DEFAULT_VERSION_BUILD_WORKFLOW,
                "nightly_image_build.yaml",
            ):
                jobs = await client.jobs(flow.build["run_id"], remote.get("run_attempt") or 1)
                sources = [await db.get(SourceNode, member.node_id) for member in members]
                build_error = missing_nightly_images(jobs, sources)
        flow.build = {**flow.build, "conclusion": conclusion}
        flow.status = "resolved" if conclusion == "success" and not build_error else "build_failed"
        flow.message = build_error or FLOW_MESSAGES[flow.status]
    flow.updated_at = point
    await update_member_status(db, flow)
    issue_ref = (issue.id, issue.run_id) if issue and flow.status == "resolved" else None
    await db.commit()
    if issue_ref:
        await tracking.close_resolved_issue(*issue_ref)


def missing_nightly_images(jobs, sources):
    built = set()
    for job in jobs:
        if job.get("conclusion") == "success":
            name = (job.get("name") or "").rsplit(" / ", 1)[-1]
            if match := NIGHTLY_BUILD_JOB.match(name):
                built.add(match[1].lower())
    if not built:
        return "工作流已结束，未找到构建成功的 Nightly 镜像"
    hardware = [(source.hardware or "").lower() for source in sources]
    required = {
        chip
        for chip in ("a2", "a3", "a5", "310p")
        if any(name == chip or name.startswith(chip + "-") for name in hardware)
    }
    if missing := required - built:
        return f"缺少构建成功的 Nightly 镜像：{', '.join(sorted(missing))}"
    return None
