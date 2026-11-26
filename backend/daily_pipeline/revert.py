"""Execute the pending revert stage using the completed locating result."""

import logging

from sqlalchemy import select

from daily_pipeline.pull_requests import located_pull_request
from daily_pipeline.tracking import DailyTrackingService, current_node
from infrastructure.clients.daily_pipeline_revert import (
    DailyRevertClient,
    RetryableRevertError,
    RevertError,
)
from infrastructure.core.github_audit import GitHubAuditUnavailable
from infrastructure.core.github_credentials import daily_github_credential
from infrastructure.persistence.daily_pipeline_settings import read_settings
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE, completed_node_result
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as Run
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution

logger = logging.getLogger(__name__)


async def execute_revert(db, node_id):
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
        or failure.problem_category == "version"
        or current is None
        or current.id != node.id
        or node.stage_code != "revert_pr"
        or node.status not in ("pending", "running")
    ):
        return True
    located = await completed_node_result(db, node.previous_node_id)
    if located.get("revert_mode") == "manual":
        return True
    config = await read_settings(db, "repository", "github_token")
    tracking = DailyTrackingService(db)
    if node.status == "pending":
        try:
            target = located_pull_request(located, config.get("repository"))
        except ValueError as exc:
            started = await tracking.start_node(node.id, {})
            await tracking.finish_node(started["executionId"], False, {}, str(exc))
            return True
        started = await tracking.start_node(node.id, target)
        execution = await db.get(Execution, started["executionId"])
    else:
        execution = await db.scalar(
            select(Execution)
            .where(
                Execution.node_id == node.id,
                Execution.source == "auto",
                Execution.status == "running",
            )
            .order_by(Execution.attempt_no.desc())
            .limit(1)
        )
        if execution is None:
            return True
    identity = execution.id
    # Keep the failure lock across the external action: manual completion or
    # supersession cannot invalidate this node between its check and PR creation.
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
        or node.status != "running"
        or execution.status != "running"
    ):
        return True
    try:
        target = located_pull_request(execution.request_data or {})
    except ValueError as exc:
        await tracking.finish_node(identity, False, {}, str(exc))
        return True
    source = await db.get(SourceNode, failure.node_id)
    run = await db.get(Run, source.run_id)
    run_url = f"https://github.com/{run.repository}/actions/runs/{run.run_id}"
    try:
        token = daily_github_credential(config)
        if not token:
            raise RevertError("请先配置 GitHub Token")
        async with DailyRevertClient(target["pr_repository"], token) as client:
            remote = await client.create_or_reuse(target["pr_number"], run_url)
        result = {
            "pr_repository": target["pr_repository"],
            "pr_number": remote["number"],
            "pr_url": remote["url"],
            "original_pr_number": target["pr_number"],
            "original_pr_url": target["pr_url"],
        }
    except (RetryableRevertError, GitHubAuditUnavailable):
        await db.rollback()
        logger.warning("Automatic revert will retry node_id=%s execution_id=%s", node_id, identity)
        raise
    except RevertError as exc:
        await tracking.finish_node(
            identity, False, {"original_pr_url": target.get("pr_url")}, str(exc)
        )
        logger.warning(
            "Automatic revert failed node_id=%s execution_id=%s reason=%s", node_id, identity, exc
        )
        return True
    await tracking.finish_node(identity, True, result, "已创建回退 PR")
    logger.info(
        "Automatic revert completed node_id=%s execution_id=%s original=%s revert=%s",
        node_id,
        identity,
        target["pr_url"],
        result["pr_url"],
    )
    return True
