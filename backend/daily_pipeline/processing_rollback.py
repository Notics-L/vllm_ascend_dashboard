"""Roll back a processing timeline while retaining previous executions."""

from sqlalchemy import func, or_, select, update

from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    FAILURE_NOTIFICATION_STAGE,
    ROLLBACK_STAGE_PREFIX,
    STAGES,
    VERSION_BUILD_NOTICE,
    now,
    reconcile_case_availability,
)
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution
from infrastructure.persistence.models import DailyPipelineVersionFlow as VersionFlow

ROLLBACK_STATUSES = ("waiting", "running", "completed", "failed")
VERSION_STAGE_NAMES = {
    "version_resolution": "确认配套恢复",
    "version_build": "镜像构建",
}


def version_can_rollback(status, stage):
    if stage == "version_resolution":
        return status != "waiting_resolution"
    return stage == "version_build" and status in ("building", "build_failed", "resolved")


async def lock_rollback_flow(db, failure_id):
    snapshot = await db.get(Failure, failure_id)
    if snapshot is None:
        raise LookupError("失败用例不存在")
    issue_id, version_id, owner_id = (
        snapshot.issue_id,
        snapshot.version_flow_id,
        snapshot.shared_flow_owner_id,
    )
    if issue_id is not None:
        issue = await db.scalar(
            select(Issue)
            .where(Issue.id == issue_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if issue is None or issue.state != "open" or issue.deleted_at is not None:
            raise ValueError("请先打开关联 Issue，再回退处理节点")
    version = (
        await db.scalar(
            select(VersionFlow)
            .where(VersionFlow.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if version_id
        else None
    )
    matches = [Failure.id == failure_id]
    if owner_id:
        matches.append(Failure.shared_flow_owner_id == owner_id)
    if version_id:
        matches.append(Failure.version_flow_id == version_id)
    rows = list(
        await db.scalars(
            select(Failure)
            .where(
                or_(*matches),
                Failure.issue_id == issue_id,
                Failure.processing_status.in_((*ACTIVE, "resolved")),
            )
            .order_by(Failure.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    failure = next((row for row in rows if row.id == failure_id), None)
    if failure is None:
        raise ValueError("该处理流程已结束")
    if (failure.version_flow_id, failure.shared_flow_owner_id) != (version_id, owner_id):
        raise ValueError("流程已变化，请刷新后重试")
    return failure, rows, version


async def cancel_attempts(db, node_ids, point):
    if node_ids:
        await db.execute(
            update(Execution)
            .where(
                Execution.node_id.in_(node_ids),
                Execution.status.in_(("running", "waiting")),
            )
            .values(status="ended", finished_at=point)
        )


async def archive_nodes(db, nodes, target_label, operator, point, *, message=None):
    stage_codes = {node.id: node.stage_code for node in nodes}
    await cancel_attempts(db, list(stage_codes), point)
    deliveries = await db.scalars(
        select(Execution)
        .where(
            Execution.node_id.in_(stage_codes),
            Execution.request_data["delivery_mode"].as_string() == "pull",
        )
        .with_for_update()
    )
    for delivery in deliveries:
        delivery.request_data = {
            **delivery.request_data,
            "archived_stage_code": stage_codes[delivery.node_id],
        }
    for node in nodes:
        attempt = await db.scalar(
            select(func.max(Execution.attempt_no)).where(Execution.node_id == node.id)
        )
        db.add(
            Execution(
                node_id=node.id,
                attempt_no=(attempt or 0) + 1,
                status="completed",
                source="manual",
                operator=operator,
                request_data={"purpose": "process_rollback", "stage_code": node.stage_code},
                message=message or f"已回退到「{target_label}」，此节点的原结果已归档",
                started_at=point,
                finished_at=point,
            )
        )
        node.stage_code = f"{ROLLBACK_STAGE_PREFIX}{node.id}"
        node.status, node.finished_at, node.updated_at = "ended", point, point
        node.next_run_at = None
    await db.flush()


async def rollback_processing_node(db, node_id, operator):
    node = await db.get(Node, node_id)
    if node is None:
        raise LookupError("流程节点不存在")
    failure, members, version = await lock_rollback_flow(db, node.failure_id)
    nodes = list(
        await db.scalars(
            select(Node)
            .where(
                Node.failure_id == failure.id,
                Node.stage_code.in_(tuple(STAGES)),
                Node.stage_code != VERSION_BUILD_NOTICE,
                Node.status != "ended",
            )
            .order_by(Node.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    target = next((row for row in nodes if row.id == node_id), None)
    if target is None or target.status not in ROLLBACK_STATUSES:
        raise ValueError("仅可回退已开始的处理节点，请刷新后重试")
    if failure.problem_category == "version" and target.stage_code not in (
        FAILURE_NOTIFICATION_STAGE,
        "classify",
    ):
        raise ValueError("请在配套恢复或镜像构建节点回退版本配套流程")

    affected = [target]
    for parent in affected:
        affected.extend(
            row for row in nodes if row.previous_node_id == parent.id and row not in affected
        )
    reset_category = any(row.stage_code == "classify" for row in affected)
    individual_notice = target.stage_code == FAILURE_NOTIFICATION_STAGE and len(affected) == 1
    if (
        individual_notice
        and failure.processing_status == "resolved"
        and failure.shared_flow_owner_id is None
    ):
        raise ValueError("该处理流程已结束")
    if not individual_notice and failure.shared_flow_owner_id not in (None, failure.id):
        raise ValueError("请在共用流程的当前节点回退")
    target_code = target.stage_code
    restart = [(row.id, row.stage_code, row.previous_node_id) for row in affected]
    if target_code in ("classify", "locating"):
        restart = restart[:1]
    if target_code == FAILURE_NOTIFICATION_STAGE:
        restart = [row for row in restart if row[1] in (FAILURE_NOTIFICATION_STAGE, "classify")]
    point = now()
    if reset_category and version:
        notices = list(
            await db.scalars(
                select(Node)
                .where(
                    Node.failure_id.in_([member.id for member in members]),
                    Node.stage_code == VERSION_BUILD_NOTICE,
                )
                .with_for_update()
            )
        )
        affected.extend(notices)
    await archive_nodes(db, affected, STAGES[target_code], operator, point)
    replacements = {}
    for identity, code, previous_id in restart:
        parent = replacements.get(previous_id)
        replacement = Node(
            failure_id=failure.id,
            stage_code=code,
            previous_node_id=parent.id if parent else previous_id,
            status="reset" if identity == node_id else "pending",
            updated_at=point,
        )
        db.add(replacement)
        await db.flush()
        replacements[identity] = replacement
    if not individual_notice:
        for member in members:
            member.processing_status = "running"
            member.ended_at = member.end_reason = None
            if reset_category:
                member.problem_category = member.version_flow_id = None
                if version and len(members) > 1:
                    member.shared_flow_owner_id = failure.id
        await reconcile_case_availability(db)
    await db.commit()
    return {"nodeId": str(replacements[node_id].id)}


async def rollback_version_stage(db, failure_id, stage, operator):
    failure, members, flow = await lock_rollback_flow(db, failure_id)
    if (
        failure.problem_category != "version"
        or flow is None
        or not version_can_rollback(flow.status, stage)
    ):
        raise ValueError("该配套节点当前无法回退，请刷新后重试")
    members = [member for member in members if member.version_flow_id == flow.id]
    point = now()
    notices = list(
        await db.scalars(
            select(Node)
            .where(
                Node.failure_id.in_([member.id for member in members]),
                Node.stage_code == VERSION_BUILD_NOTICE,
            )
            .order_by(Node.id)
            .with_for_update()
        )
    )
    await cancel_attempts(db, [notice.id for notice in notices], point)
    audit_node = (
        notices[0]
        if notices
        else Node(
            failure_id=failure.id,
            stage_code=VERSION_BUILD_NOTICE,
            status="completed",
            updated_at=point,
        )
    )
    if not notices:
        db.add(audit_node)
        await db.flush()
    attempt = await db.scalar(
        select(func.max(Execution.attempt_no)).where(Execution.node_id == audit_node.id)
    )
    db.add(
        Execution(
            node_id=audit_node.id,
            attempt_no=(attempt or 0) + 1,
            status="completed",
            source="manual",
            operator=operator,
            request_data={"purpose": "process_rollback", "stage_code": stage},
            result_data={"resolution": flow.resolution, "build": flow.build},
            message=f"已回退到「{VERSION_STAGE_NAMES[stage]}」",
            started_at=point,
            finished_at=point,
        )
    )
    if stage == "version_resolution":
        flow.status = "waiting_resolution"
        flow.resolution = flow.ready_at = flow.notified_at = None
    else:
        flow.status = "waiting_build"
    flow.build = flow.message = None
    flow.updated_at = point
    for member in members:
        member.processing_status = "running"
        member.ended_at = member.end_reason = None
    await reconcile_case_availability(db)
    await db.commit()
    return {"status": flow.status}
