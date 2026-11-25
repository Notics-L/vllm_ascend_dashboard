"""Manage shared processing flows for failures under one open Issue."""

from sqlalchemy import or_, select, update

from daily_pipeline.processing_rollback import archive_nodes, cancel_attempts
from daily_pipeline.version_resolution import attach_version_flow, lock_version_flows
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    FAILURE_NOTIFICATION_STAGE,
    ROLLBACK_STAGE_PREFIX,
    VERSION_BUILD_NOTICE,
    now,
    transfer_shared_processing,
)
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node


def process_owner(failure):
    return failure.shared_flow_owner_id or failure.id


def flow_key(failure):
    if failure.version_flow_id is not None:
        return "version", failure.version_flow_id
    return "process", process_owner(failure)


async def lock_share_members(db, identities):
    selected = list(await db.scalars(select(Failure).where(Failure.id.in_(identities))))
    if len(selected) != len(identities):
        raise LookupError("失败用例不存在")
    issue_id = selected[0].issue_id
    if issue_id is None or any(row.issue_id != issue_id for row in selected):
        raise ValueError("只能共用同一个 Issue 下的失败用例")
    keys = {row.id: flow_key(row) for row in selected}
    roots = {process_owner(row) for row in selected}
    version_ids = {row.version_flow_id for row in selected if row.version_flow_id is not None}

    issue = await db.scalar(
        select(Issue)
        .where(Issue.id == issue_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if issue is None or issue.state != "open" or issue.deleted_at is not None:
        raise ValueError("关联 Issue 已结束")
    flows = await lock_version_flows(db, version_ids)
    members = {
        row.id: row
        for row in await db.scalars(
            select(Failure)
            .where(
                Failure.issue_id == issue_id,
                or_(
                    Failure.id.in_(roots),
                    Failure.shared_flow_owner_id.in_(roots),
                    Failure.version_flow_id.in_(version_ids),
                ),
            )
            .order_by(Failure.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    if any(
        identity not in members or flow_key(members[identity]) != key
        for identity, key in keys.items()
    ):
        raise ValueError("处理流程已变化，请刷新后重试")
    if any(members[identity].processing_status not in ACTIVE for identity in identities):
        raise ValueError("只能操作尚未结案的失败用例")
    return members, flows


async def share_processing_flow(db, identities, target_id):
    if len(identities) < 2 or target_id not in identities:
        raise ValueError("请选择至少两个用例并指定要保留的流程")
    members, flows = await lock_share_members(db, identities)
    target = members[target_id]
    target_key = flow_key(target)
    owner = members.get(process_owner(target))
    if target.version_flow_id is not None and (
        owner is None or owner.processing_status not in ACTIVE or flow_key(owner) != target_key
    ):
        owner = target
    if owner is None or owner.processing_status not in ACTIVE:
        raise ValueError("要保留的流程已结束")

    selected_keys = {flow_key(members[identity]) for identity in identities}
    joined = [
        row
        for row in members.values()
        if row.processing_status in ACTIVE and flow_key(row) in selected_keys
    ]
    replaced = [row for row in joined if flow_key(row) != target_key]
    if any(row.id not in identities for row in replaced):
        raise ValueError("请选中原共用流程的所有成员，或先对要移动的用例取消共用")
    if any(
        row.version_flow_id is not None
        and flows[row.version_flow_id].status != "waiting_resolution"
        for row in replaced
    ):
        raise ValueError("选中的其他流程已有处理进度，请选择保留已有进度的流程")

    retained_roots = {process_owner(row) for row in joined if flow_key(row) == target_key}
    replaced_roots = {process_owner(row) for row in replaced} - retained_roots
    if replaced_roots:
        progressed = await db.scalar(
            select(Node.id)
            .where(
                Node.failure_id.in_(replaced_roots),
                Node.stage_code.not_in(
                    (FAILURE_NOTIFICATION_STAGE, VERSION_BUILD_NOTICE, "classify")
                ),
                Node.status.not_in(("pending", "ended")),
            )
            .limit(1)
        )
        if progressed is not None:
            raise ValueError("选中的其他流程已有处理进度，请选择保留已有进度的流程")
        retired = list(
            await db.scalars(
                select(Node.id)
                .where(
                    Node.failure_id.in_(replaced_roots),
                    Node.stage_code.not_in((FAILURE_NOTIFICATION_STAGE, VERSION_BUILD_NOTICE)),
                    Node.status.not_in(("completed", "ended")),
                )
                .with_for_update()
            )
        )
        if retired:
            point = now()
            await cancel_attempts(db, retired, point)
            await db.execute(
                update(Node)
                .where(Node.id.in_(retired))
                .values(status="ended", finished_at=point, updated_at=point, next_run_at=None)
            )

    for row in joined:
        row.shared_flow_owner_id = owner.id
    flow_id = owner.id
    if owner.problem_category == "version":
        flow_id = await attach_version_flow(db, joined, flows.get(owner.version_flow_id))
    else:
        for row in joined:
            row.problem_category = owner.problem_category
            row.version_flow_id = None
            row.processing_status, row.ended_at = owner.processing_status, owner.ended_at
    await db.commit()
    return {"flowId": str(flow_id)}


async def unshare_processing_flow(db, failure_id, operator):
    members, _ = await lock_share_members(db, {failure_id})
    failure = members[failure_id]
    peers = [
        row
        for row in members.values()
        if row.id != failure.id
        and row.processing_status in ACTIVE
        and flow_key(row) == flow_key(failure)
    ]
    if not peers:
        raise ValueError("该失败用例当前没有其他共用成员")
    if failure.shared_flow_owner_id == failure.id:
        await transfer_shared_processing(db, failure)

    nodes = list(
        await db.scalars(
            select(Node)
            .where(Node.failure_id == failure.id)
            .order_by(Node.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    own_notice = next(
        (
            node
            for node in nodes
            if node.stage_code == FAILURE_NOTIFICATION_STAGE and node.status != "ended"
        ),
        None,
    )
    old_steps = [
        node
        for node in nodes
        if node.stage_code not in (FAILURE_NOTIFICATION_STAGE, VERSION_BUILD_NOTICE)
        and not node.stage_code.startswith(ROLLBACK_STAGE_PREFIX)
    ]
    point = now()
    if old_steps:
        await archive_nodes(
            db,
            old_steps,
            "问题分类",
            operator,
            point,
            message="已取消共用，原节点结果已归档",
        )
    failure.shared_flow_owner_id = failure.version_flow_id = None
    failure.problem_category = failure.ended_at = failure.end_reason = None
    failure.processing_status = "pending"
    db.add(
        Node(
            failure_id=failure.id,
            stage_code="classify",
            status="pending",
            previous_node_id=own_notice.id if own_notice else None,
            updated_at=point,
        )
    )
    await db.commit()
    return {"unshared": True}
