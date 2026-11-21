"""Problem tracking queries and guarded processing transitions."""

import logging

from sqlalchemy import func, or_, select, update

from daily_pipeline.code_resolution import process_code_issue
from daily_pipeline.collection_source import utc_datetime
from daily_pipeline.pull_requests import located_pull_request
from daily_pipeline.version_resolution import flow_view
from infrastructure.clients.daily_pipeline_collection import (
    CollectionSourceError,
    DailyCollectionClient,
)
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError, start_processing
from infrastructure.core.security import decrypt_api_key
from infrastructure.persistence.daily_pipeline_settings import read_settings
from infrastructure.persistence.daily_pipeline_tracking import (
    ACTIVE,
    AUTOMATIC_STAGES,
    CATEGORIES,
    FAILURE_NOTIFICATION_STAGE,
    PATHS,
    STAGES,
    VERSION_BUILD_NOTICE,
    now,
    reconcile_case_availability,
    retry_failure_notifications,
    save_issue,
)
from infrastructure.persistence.models import DailyPipelineCollectionNode as SourceNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as SourceRun
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node
from infrastructure.persistence.models import DailyPipelineProcessNodeExecution as Execution

logger = logging.getLogger(__name__)

PROGRESS = {
    "pending": "待处理",
    "running": "处理中",
    "resolved": "已解决",
    "unresolved": "未解决",
    "ended": "已结束",
}
ENVIRONMENT_RECOVERY_STAGES = ("locating", "mark_success")
ENVIRONMENT_SUCCESS_MESSAGE = "已按环境问题策略标记成功"


class ManualInputError(ValueError):
    """Invalid human-supplied business result, without changing the flow."""


def current_node(nodes):
    completed = {node.id for node in nodes if node.status == "completed"}
    return next(
        (
            node
            for node in nodes
            if node.status in ("pending", "running", "waiting", "failed")
            and (node.previous_node_id is None or node.previous_node_id in completed)
        ),
        None,
    )


def stamp(value):
    return value.isoformat() + "Z" if value else None


class DailyTrackingService:
    def __init__(self, db):
        self.db = db

    async def _issue(self, number):
        rows = list(await self.db.scalars(select(Issue).where(Issue.number == number)))
        if not rows:
            raise LookupError("Issue 不存在")
        if len(rows) != 1:
            raise ValueError("Issue 编号跨仓库重复，需要指定仓库")
        return rows[0]

    async def failures(self):
        flow_size = (
            select(Failure.version_flow_id.label("flow_id"), func.count().label("member_count"))
            .where(Failure.version_flow_id.is_not(None)).group_by(Failure.version_flow_id).subquery()
        )
        rows = (
            await self.db.execute(
                select(Failure, Issue, SourceNode, flow_size.c.member_count)
                .join(SourceNode, SourceNode.id == Failure.node_id)
                .outerjoin(Issue, Issue.id == Failure.issue_id)
                .outerjoin(flow_size, flow_size.c.flow_id == Failure.version_flow_id)
                .order_by(Failure.discovered_at.desc(), Failure.id.desc())
            )
        ).all()
        return [self.failure_view(f, i, n, members) for f, i, n, members in rows]

    def failure_view(self, f, issue, source, members=None):
        return {
            "id": str(f.id),
            "caseId": str(f.id),
            "name": source.name,
            "hardware": source.hardware or "—",
            "group": source.case_type,
            "vllmVersion": source.vllm_version,
            "runId": source.run_id,
            "result": "失败",
            "collectedAt": stamp(f.discovered_at),
            "processingProgress": PROGRESS[f.processing_status],
            "problemCategory": CATEGORIES.get(f.problem_category),
            "versionFlowId": str(f.version_flow_id) if getattr(f, "version_flow_id", None) else None,
            "versionFlowSize": members,
            "endReason": f.end_reason,
            "replacedById": str(f.replaced_by_id) if f.replaced_by_id else None,
            "issue": {"number": issue.number, "state": issue.state} if issue else None,
        }

    async def detail(self, failure_id):
        f = await self.db.get(Failure, failure_id)
        if f is None:
            raise LookupError("失败记录不存在")
        source = await self.db.get(SourceNode, f.node_id)
        run = await self.db.get(SourceRun, source.run_id)
        issue = await self.db.get(Issue, f.issue_id) if f.issue_id else None
        version_flow = await flow_view(self.db, f, run, issue)
        nodes = list(
            await self.db.scalars(select(Node).where(Node.failure_id == f.id).order_by(Node.id))
        )
        executions = (
            list(
                await self.db.scalars(
                    select(Execution)
                    .where(Execution.node_id.in_([n.id for n in nodes]))
                    .order_by(Execution.attempt_no)
                )
            )
            if nodes
            else []
        )
        current = current_node(nodes) if f.processing_status in ACTIVE else None
        stages = []
        for node in nodes:
            if node.stage_code == VERSION_BUILD_NOTICE:
                continue
            records = [e for e in executions if e.node_id == node.id]
            latest = next((item for item in reversed(records) if not (item.request_data or {}).get("purpose")), None)
            name = (
                "回退版本PR"
                if node.stage_code == "revert_pr" and f.problem_category == "version"
                else STAGES[node.stage_code]
            )
            result_message = latest.message if latest else None
            if node.stage_code == FAILURE_NOTIFICATION_STAGE:
                result_message = {
                    "pending": "等待配置失败通知人",
                    "waiting": "等待接收人通知结果",
                    "failed": "部分通知发送失败，可重试",
                    "completed": "通知已完成",
                }.get(node.status, result_message)
            stages.append(
                {
                    "id": str(node.id),
                    "stageCode": node.stage_code,
                    "name": name,
                    "status": node.status,
                    "canManuallyComplete": current is not None and current.id == node.id and (
                        not version_flow or node.stage_code in (FAILURE_NOTIFICATION_STAGE, "classify")
                    ),
                    "problemCategory": f.problem_category,
                    "result": result_message,
                    "resultData": latest.result_data if latest else None,
                    "startedAt": stamp(node.started_at),
                    "updatedAt": stamp(node.updated_at),
                    "records": [
                        {
                            "id": str(e.id),
                            "author": e.operator if e.source == "manual" else "系统",
                            "source": e.source,
                            "createdAt": stamp(e.started_at),
                            "content": e.message or "",
                            "status": e.status,
                            "resultData": e.result_data,
                        }
                        for e in records
                    ],
                }
            )
        if f.ended_at and f.processing_status in ("resolved", "ended"):
            label = {"resolved": "已解决", "ended": "已结束"}[
                f.processing_status
            ]
            reason = {"superseded": "被新的失败替代", "issue_closed": "Issue 已关闭"}.get(
                f.end_reason, label
            )
            stages.append(
                {
                    "id": "end-" + str(f.id),
                    "stageCode": "end",
                    "name": label,
                    "status": "completed" if f.processing_status == "resolved" else "ended",
                    "result": reason,
                    "updatedAt": stamp(f.ended_at),
                    "records": [],
                }
            )
        return {
            **self.failure_view(f, issue, source),
            "source": {
                "repository": run.repository,
                "runId": source.source_run_id,
                "jobId": source.source_id,
                "version": source.vllm_ref,
                "context": {"source_branch": run.source_branch},
            },
            "processingFlow": {
                "currentStageId": str(current.id) if current and (
                    not version_flow or current.stage_code in (FAILURE_NOTIFICATION_STAGE, "classify")
                ) else None,
                "stages": stages,
                "versionFlow": version_flow,
            },
        }

    async def issues(self):
        rows = list(
            await self.db.scalars(
                select(Issue).where(Issue.deleted_at.is_(None)).order_by(Issue.id.desc())
            )
        )
        return [
            {
                "number": i.number,
                "title": i.title,
                "state": i.state,
                "runId": i.run_id,
                "processingProgress": None,
            }
            for i in rows
        ]

    async def _client(self, repository):
        settings = await read_settings(self.db, "github_token")
        token = decrypt_api_key(settings.get("github_token") or "")
        if not token:
            raise CollectionSourceError("请先配置 GitHub Token")
        return DailyCollectionClient(repository, token)

    async def sync_issue(self, issue, close=False):
        identity, repository, run_id, number = issue.id, issue.repository, issue.run_id, issue.number
        client = await self._client(repository)
        await self.db.rollback()
        async with client:
            data = await client.close_issue(number) if close else await client.issue(number)
        await save_issue(
            self.db, repository, run_id, {**data, "closed_at": utc_datetime(data.get("closed_at"))}
        )
        await self.db.commit()
        if not close and data["state"] == "open":
            await process_code_issue(self, identity)
            await self.db.refresh(issue)

    async def close_resolved_issue(self, issue_id, run_id):
        async with self.db.begin():
            # Serialize with collection writes while checking the complete run and closing its Issue.
            collected_run_id = await self.db.scalar(
                select(SourceRun.run_id).where(
                    SourceRun.run_id == run_id,
                    SourceRun.finished_at.is_not(None),
                    SourceRun.deleted_at.is_(None),
                ).with_for_update()
            )
            if collected_run_id is None:
                return False
            issue = await self.db.scalar(select(Issue).where(
                Issue.id == issue_id, Issue.run_id == run_id,
                Issue.state == "open", Issue.deleted_at.is_(None),
            ).with_for_update().execution_options(populate_existing=True))
            if issue is None:
                return False
            statuses = list(await self.db.scalars(
                select(Failure.processing_status).where(Failure.issue_id == issue_id)
            ))
            if not statuses or any(status not in ("resolved", "ended") for status in statuses):
                return False
            repository, number = issue.repository, issue.number
            async with await self._client(repository) as client:
                data = await client.close_issue(number)
            if data.get("state") != "closed":
                raise CollectionSourceError("GitHub 未确认 Issue 已关闭")
            await save_issue(
                self.db, repository, run_id,
                {**data, "closed_at": utc_datetime(data.get("closed_at"))},
            )
        logger.info("Daily Issue automatically closed repository=%s issue_number=%s", repository, number)
        return True

    async def issue_detail(self, number):
        issue = await self._issue(number)
        repository, run_id = issue.repository, issue.run_id
        allow_reopen = issue.state == "closed"
        client = await self._client(repository)
        await self.db.rollback()
        async with client:
            data = await client.issue_detail(number)
        issue = await save_issue(self.db, repository, run_id, data, allow_reopen=allow_reopen)
        await self.db.commit()
        if issue.state == "open":
            await self._queue_issue_processing(issue.id)
        return {**data, "state": issue.state}

    async def _queue_issue_processing(self, issue_id):
        failures = list(await self.db.scalars(select(Failure).where(
            Failure.issue_id == issue_id, Failure.processing_status.in_(ACTIVE),
        )))
        for failure in failures:
            nodes = list(await self.db.scalars(select(Node).where(Node.failure_id == failure.id).order_by(Node.id)))
            node = current_node(nodes)
            if node and node.status == "pending" and node.stage_code in AUTOMATIC_STAGES:
                await self._queue_processing(node.id)

    async def close(self, numbers):
        count = 0
        for number in sorted(numbers):
            await self.sync_issue(await self._issue(number), close=True)
            count += 1
        return {"closed": count}

    async def delete(self, numbers):
        for number in sorted(numbers):
            issue = await self._issue(number)
            issue.deleted_at = now()
        await self.db.commit()
        return {"deleted": len(numbers)}

    async def start_node(self, node_id, request_data):
        node = await self.db.get(Node, node_id)
        if not node:
            raise LookupError("流程节点不存在")
        failure = await self.db.scalar(
            select(Failure)
            .where(Failure.id == node.failure_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self.db.refresh(node)
        if failure.processing_status not in ACTIVE:
            raise ValueError("流程已经结束")
        if failure.problem_category == "version" and node.stage_code not in (FAILURE_NOTIFICATION_STAGE, "classify"):
            raise ValueError("请在版本配套流程登记处理结果")
        if node.status not in ("pending", "failed"):
            raise ValueError("节点已开始或已结束")
        if node.previous_node_id:
            previous = await self.db.get(Node, node.previous_node_id)
            if previous.failure_id != failure.id or previous.status != "completed":
                raise ValueError("前置节点尚未完成")
        attempt = (
            await self.db.scalar(
                select(func.max(Execution.attempt_no)).where(Execution.node_id == node.id)
            )
            or 0
        ) + 1
        execution = Execution(
            node_id=node.id,
            attempt_no=attempt,
            status="running",
            request_data=request_data,
            started_at=now(),
        )
        self.db.add(execution)
        node.status = "running"
        node.started_at = node.started_at or now()
        node.updated_at = now()
        node.finished_at = None
        failure.processing_status = "running"
        failure.ended_at = None
        await self.db.commit()
        return {"executionId": execution.id, "attempt": attempt}

    async def finish_node(self, execution_id, success, result, message):
        execution = await self.db.get(Execution, execution_id)
        if not execution:
            raise LookupError("节点执行不存在")
        node = await self.db.get(Node, execution.node_id)
        failure = await self.db.scalar(
            select(Failure)
            .where(Failure.id == node.failure_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self.db.refresh(node)
        await self.db.refresh(execution)
        if failure.problem_category == "version" and node.stage_code not in (FAILURE_NOTIFICATION_STAGE, "classify"):
            raise ValueError("请在版本配套流程登记处理结果")
        if (
            failure.processing_status != "running"
            or execution.status not in ("running", "waiting")
            or node.status not in ("running", "waiting")
        ):
            raise ValueError("执行已结束或回调已失效")
        automatic_node = await self._complete_node(
            failure, node, execution, success, result, message
        )
        status = failure.processing_status
        queued = await self._commit_node_completion(failure, automatic_node)
        return {"status": status, "automaticQueued": queued}

    async def _commit_node_completion(self, failure, automatic_node):
        issue_id = failure.issue_id if failure.processing_status == "resolved" else None
        run_id = await self.db.scalar(select(Issue.run_id).where(Issue.id == issue_id)) if issue_id else None
        await self.db.commit()
        if run_id is not None:
            try:
                await self.close_resolved_issue(issue_id, run_id)
            except Exception:
                logger.exception("Automatic Issue close failed; will retry during Issue sync issue_id=%s", issue_id)
        return await self._queue_processing(automatic_node) if automatic_node else None

    async def _complete_node(self, failure, node, execution, success, result, message):
        """Shared transitions; caller holds the failure lock and commits once."""
        category = result.get("category") or failure.problem_category
        if success and node.stage_code in ("classify", "locating"):
            if category not in PATHS:
                raise ManualInputError("请选择代码问题、版本配套问题或环境问题")
            if (
                node.stage_code == "locating"
                and failure.problem_category
                and category != failure.problem_category
            ):
                raise ManualInputError("已完成的问题分类不能修改")
        if success and node.stage_code == "locating" and category == "code":
            try:
                config = await read_settings(self.db, "repository")
                repository = config.get("repository")
                if not repository:
                    raise ValueError("请先配置 Daily 全局仓库")
                result = {**result, **located_pull_request(result, repository)}
            except ValueError as exc:
                raise ManualInputError(str(exc)) from exc
        automatic_node = None
        point = now()
        execution.status = node.status = "completed" if success else "failed"
        execution.result_data, execution.message = result, message
        execution.finished_at = node.finished_at = node.updated_at = point
        node.started_at = node.started_at or point
        node.next_run_at = None
        failure.processing_status, failure.ended_at = "running", None
        if not success:
            # An attempt failure is retryable. It does not end the business flow.
            # The node/execution retain their failed status and full error history.
            pass
        elif category == "environment" and node.stage_code in ("classify", "locating"):
            failure.problem_category = category
            await self._mark_environment_success(failure, node, point)
        elif node.stage_code in ("classify", "locating"):
            failure.problem_category = category
            codes = () if category == "version" else (
                ("locating",) if node.stage_code == "classify" else PATHS[category]
            )
            previous = node
            for code in codes:
                step = Node(
                    failure_id=failure.id,
                    previous_node_id=previous.id,
                    stage_code=code,
                    status="pending",
                    updated_at=point,
                )
                self.db.add(step)
                await self.db.flush()
                if code == "revert_pr":
                    automatic_node = step.id
                previous = step
        else:
            child = await self.db.scalar(select(Node).where(Node.previous_node_id == node.id))
            if child is None:
                failure.processing_status, failure.ended_at = "resolved", point
            elif child.stage_code in AUTOMATIC_STAGES and child.status == "pending":
                automatic_node = child.id
        await reconcile_case_availability(self.db)
        return automatic_node

    async def _mark_environment_success(self, failure, previous, point):
        step = Node(
            failure_id=failure.id,
            previous_node_id=previous.id,
            stage_code="mark_success",
            status="completed",
            started_at=point,
            finished_at=point,
            updated_at=point,
        )
        self.db.add(step)
        await self.db.flush()
        self.db.add(Execution(
            node_id=step.id,
            attempt_no=1,
            source="auto",
            status="completed",
            result_data={"category": "environment"},
            message=ENVIRONMENT_SUCCESS_MESSAGE,
            started_at=point,
            finished_at=point,
        ))
        failure.processing_status, failure.ended_at = "resolved", point

    async def complete_environment_node(self, node_id):
        """Complete an environment node left open by an older process definition."""
        node = await self.db.get(Node, node_id)
        if node is None or node.stage_code not in ENVIRONMENT_RECOVERY_STAGES:
            return True
        failure = await self.db.scalar(
            select(Failure).where(Failure.id == node.failure_id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if failure is None or failure.problem_category != "environment" or failure.processing_status not in ACTIVE:
            return True
        nodes = list(await self.db.scalars(
            select(Node).where(Node.failure_id == failure.id).order_by(Node.id)
            .execution_options(populate_existing=True)
        ))
        current = current_node(nodes)
        if current is None or current.id != node_id:
            return True
        point = now()
        await self.db.execute(
            update(Execution)
            .where(Execution.node_id == node_id, Execution.status.in_(("running", "waiting")))
            .values(status="ended", finished_at=point)
        )
        attempt = (await self.db.scalar(
            select(func.max(Execution.attempt_no)).where(Execution.node_id == node_id)
        ) or 0) + 1
        execution = Execution(
            node_id=node_id, attempt_no=attempt, source="auto", status="running", started_at=point,
        )
        self.db.add(execution)
        automatic_node = await self._complete_node(
            failure, current, execution, True, {"category": "environment"}, ENVIRONMENT_SUCCESS_MESSAGE
        )
        await self._commit_node_completion(failure, automatic_node)
        return True

    async def _queue_processing(self, node_id):
        try:
            await start_processing(node_id)
            return True
        except SchedulerUpdateError:
            logger.exception("Automatic processing notification failed node_id=%s", node_id)
            return False

    async def retry_processing(self, node_id):
        node = await self.db.get(Node, node_id)
        if node is None:
            raise LookupError("流程节点不存在")
        failure = await self.db.scalar(
            select(Failure)
            .where(Failure.id == node.failure_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        nodes = list(
            await self.db.scalars(
                select(Node)
                .where(Node.failure_id == failure.id)
                .order_by(Node.id)
                .execution_options(populate_existing=True)
            )
        )
        current = current_node(nodes)
        if failure.processing_status not in ACTIVE or current is None or current.id != node.id:
            raise ValueError("仅可重试当前未完成的自动处理节点")
        if failure.problem_category == "version" and node.stage_code != FAILURE_NOTIFICATION_STAGE:
            raise ValueError("请在版本配套流程登记处理结果")
        if node.stage_code == FAILURE_NOTIFICATION_STAGE and node.status == "failed":
            await retry_failure_notifications(self.db, node)
            await self.db.commit()
            return {"queued": True}
        if node.stage_code not in AUTOMATIC_STAGES or node.status not in ("pending", "failed"):
            raise ValueError("仅可重试当前未完成的自动处理节点")
        node.status, node.finished_at, node.updated_at = "pending", None, now()
        failure.processing_status, failure.ended_at = "running", None
        await self.db.commit()
        return {"queued": await self._queue_processing(node.id)}

    async def manual_complete(self, node_id, success, result, message, operator):
        node = await self.db.get(Node, node_id)
        if node is None:
            raise LookupError("流程节点不存在")
        failure = await self.db.scalar(
            select(Failure)
            .where(Failure.id == node.failure_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        nodes = list(
            await self.db.scalars(
                select(Node)
                .where(Node.failure_id == failure.id)
                .order_by(Node.id)
                .execution_options(populate_existing=True)
            )
        )
        current = current_node(nodes)
        if failure.processing_status not in ACTIVE or current is None or current.id != node.id:
            raise ValueError("仅可处理当前未完成节点，请刷新后重试")
        if failure.problem_category == "version" and node.stage_code not in (FAILURE_NOTIFICATION_STAGE, "classify"):
            raise ManualInputError("请在版本配套流程登记处理结果")
        message = message.strip()
        if node.stage_code == FAILURE_NOTIFICATION_STAGE and not success:
            raise ManualInputError("人工处理失败通知时，请确认已通知接收人")
        if node.stage_code == "classify":
            if not success or result.get("category") not in PATHS:
                raise ManualInputError("请选择问题分类")
            message = CATEGORIES[result["category"]]
        elif not message:
            raise ManualInputError("请填写处理说明")
        if not operator:
            raise ManualInputError("缺少操作人")
        if success:
            required = {
                "revert_pr": "pr_number",
                "pr_merge": "pr_number",
                "image_rebuild": "run_id",
            }.get(node.stage_code)
            if required and (type(result.get(required)) is not int or result[required] <= 0):
                raise ManualInputError("请填写有效的 PR 编号或构建 Run ID")
            if node.stage_code == "classify" and result.get("category") not in PATHS:
                raise ManualInputError("请选择问题分类")
        point = now()
        # Preserve previous attempts. Ending their execution invalidates late callbacks.
        purpose = Execution.request_data["purpose"].as_string()
        await self.db.execute(
            update(Execution)
            .where(Execution.node_id == node.id, Execution.status.in_(("running", "waiting")))
            .where(or_(purpose.is_(None), purpose != "revert_deadline"))
            .values(status="ended", finished_at=point)
        )
        attempt = (
            await self.db.scalar(
                select(func.max(Execution.attempt_no)).where(Execution.node_id == node.id)
            )
            or 0
        ) + 1
        execution = Execution(
            node_id=node.id,
            attempt_no=attempt,
            source="manual",
            operator=operator,
            status="running",
            request_data={"success": success, "result": result, "message": message},
            started_at=point,
        )
        self.db.add(execution)
        automatic_node = await self._complete_node(
            failure, node, execution, success, result, message
        )
        status, execution_id = failure.processing_status, execution.id
        queued = await self._commit_node_completion(failure, automatic_node)
        return {
            "status": status,
            "executionId": execution_id,
            "automaticQueued": queued,
        }

    async def wait_node(self, execution_id, result, message):
        execution = await self.db.get(Execution, execution_id)
        if not execution:
            raise LookupError("节点执行不存在")
        node = await self.db.get(Node, execution.node_id)
        failure = await self.db.scalar(
            select(Failure)
            .where(Failure.id == node.failure_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self.db.refresh(execution)
        await self.db.refresh(node)
        if failure.processing_status != "running" or execution.status != "running":
            raise ValueError("流程或执行已经结束")
        key = (
            "pr_number"
            if node.stage_code == "pr_merge"
            else "run_id"
            if node.stage_code == "image_rebuild"
            else None
        )
        if (
            not key
            or not isinstance(result.get(key), int)
            or isinstance(result[key], bool)
            or result[key] <= 0
        ):
            raise ValueError("等待节点必须提供有效的 PR 编号或构建 Run ID")
        execution.status = node.status = "waiting"
        execution.result_data, execution.message = result, message
        node.next_run_at = node.updated_at = now()
        await self.db.commit()
        return {"status": "waiting"}

    async def poll_waiting(self):
        identities = list(
            await self.db.scalars(
                select(Execution.id)
                .join(Node, Node.id == Execution.node_id)
                .join(Failure, Failure.id == Node.failure_id)
                .where(Execution.status == "waiting", Node.status == "waiting")
                .where(or_(Failure.problem_category.is_(None), Failure.problem_category != "version"))
                .where(or_(
                    (Node.stage_code == "pr_merge") & Execution.result_data["pr_number"].as_integer().is_not(None),
                    (Node.stage_code == "image_rebuild") & Execution.result_data["run_id"].as_integer().is_not(None),
                ))
            )
        )
        await self.db.rollback()
        for identity in identities:
            execution = await self.db.get(Execution, identity)
            node = await self.db.get(Node, execution.node_id)
            failure = await self.db.get(Failure, node.failure_id)
            source = await self.db.get(SourceNode, failure.node_id)
            run = await self.db.get(SourceRun, source.run_id)
            repository = run.repository
            result = dict(execution.result_data or {})
            client = await self._client(repository)
            await self.db.rollback()
            async with client:
                if "pr_number" in result:
                    remote = await client.json(f"{client.prefix}/pulls/{result['pr_number']}")
                    complete = bool(remote.get("merged")) or remote.get("state") == "closed"
                    success = bool(remote.get("merged"))
                    result.update(merged=success, merged_at=remote.get("merged_at"))
                else:
                    remote = await client.run(result["run_id"])
                    complete = remote.get("status") == "completed"
                    success = remote.get("conclusion") == "success"
                    result.update(conclusion=remote.get("conclusion"))
            if complete:
                try:
                    await self.finish_node(
                        identity,
                        success,
                        result,
                        "外部任务已完成" if success else "外部任务失败或 PR 未合入即关闭",
                    )
                except ValueError:
                    await self.db.rollback()  # superseded/closed while request was in flight
        await self.db.commit()
