"""Explicit sharing of a Daily failure's active processing flow."""

from sqlalchemy import or_, select, update

from daily_pipeline.version_resolution import share_version_flow
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    FAILURE_NOTIFICATION_STAGE,
    now,
    sync_shared_processing,
)
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node


async def share_processing_flow(db, identities, target_id):
    if len(identities) < 2 or target_id not in identities:
        raise ValueError("请选择至少两个用例并指定要保留的流程")
    selected = {
        row.id: row for row in await db.scalars(select(Failure).where(Failure.id.in_(identities)))
    }
    if selected.keys() != identities:
        raise LookupError("失败用例不存在")
    if selected[target_id].problem_category == "version":
        return await share_version_flow(db, identities, target_id)

    issue_id = selected[target_id].issue_id
    if issue_id is None or any(row.issue_id != issue_id for row in selected.values()):
        raise ValueError("只能共用同一个 Issue 下的失败用例")
    issue = await db.scalar(
        select(Issue)
        .where(Issue.id == issue_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if issue is None or issue.state != "open" or issue.deleted_at is not None:
        raise ValueError("关联 Issue 已结束")

    roots = {row.shared_flow_owner_id or row.id for row in selected.values()}
    group = {
        row.id: row
        for row in await db.scalars(
            select(Failure)
            .where(or_(Failure.id.in_(roots), Failure.shared_flow_owner_id.in_(roots)))
            .order_by(Failure.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    if (
        not identities.issubset(group)
        or {group[identity].shared_flow_owner_id or identity for identity in identities} != roots
    ):
        raise ValueError("处理流程已变化，请刷新后重试")

    rows = [group[identity] for identity in identities]
    owner = group[group[target_id].shared_flow_owner_id or target_id]
    if any(
        row.issue_id != issue_id or row.processing_status not in ACTIVE for row in (*rows, owner)
    ):
        raise ValueError("只能共用同一个 Issue 下尚未结案的失败用例")
    if owner.problem_category == "version" or any(
        row.problem_category != owner.problem_category for row in rows
    ):
        raise ValueError("请选择分类相同的失败用例，或多条未分类的用例")

    other_roots = roots - {owner.id}
    if any(
        row.processing_status in ACTIVE
        and (row.shared_flow_owner_id or row.id) in other_roots
        and row.id not in identities
        for row in group.values()
    ):
        raise ValueError("共用流程不能拆分，请选中该流程的所有成员或选择保留该流程")
    if other_roots:
        progressed = await db.scalar(
            select(Node.id)
            .where(
                Node.failure_id.in_(other_roots),
                Node.stage_code != FAILURE_NOTIFICATION_STAGE,
                Node.status.not_in(("pending", "ended")),
                or_(Node.stage_code != "classify", Node.status != "completed"),
            )
            .limit(1)
        )
        if progressed is not None:
            raise ValueError("选中的其他流程已有处理进度，请选择保留已有进度的流程")
        point = now()
        await db.execute(
            update(Node)
            .where(
                Node.failure_id.in_(other_roots),
                Node.status == "pending",
                Node.stage_code != FAILURE_NOTIFICATION_STAGE,
            )
            .values(status="ended", finished_at=point, updated_at=point, next_run_at=None)
        )

    for row in group.values():
        row.shared_flow_owner_id = owner.id
    await sync_shared_processing(db, owner)
    await db.commit()
    return {"flowId": str(owner.id)}
