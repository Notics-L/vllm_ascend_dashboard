"""Submit saved image build tasks and retain the GitHub result."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.source_recommendation import get_source_recommendation
from image_build.workflow_sync import WORKFLOW_PATH
from infrastructure.persistence.models import ImageBuildTriggerRecord, ImageBuildTriggerTask
from workflow_actions.credentials import get_github_token
from workflow_actions.service import dispatch_workflow, redact_workflow_data

DISPATCH_TIMEOUT_SECONDS = 45
NO_SOURCE_RECOMMENDATION = "该源码分支暂无可用的 Daily 推荐 SHA"
SUBMISSION_WINDOW = timedelta(minutes=2)
UNKNOWN_MESSAGE = "GitHub 可能已接收请求，请先查看 GitHub 运行记录"
ERROR_MESSAGES = {
    401: "Actions Token 无效或已过期",
    403: "Actions Token 无触发权限或 GitHub 限制了请求",
    404: "GitHub 无法访问目标工作流，请检查 Actions Token 的仓库权限",
    422: "GitHub 拒绝了该分支或参数，请同步 Workflow 后检查任务",
    429: "GitHub 请求已达限额，请稍后重试",
}


class ImageBuildTriggerInProgress(RuntimeError):
    pass


async def submit_trigger_task(
    db: AsyncSession, task: ImageBuildTriggerTask, username: str, *,
    repository: str, supports_source_ref: bool = False, source_branch: str | None = None,
) -> ImageBuildTriggerRecord:
    # The caller holds the task row lock until this history row is committed.
    pending = await db.scalar(
        select(ImageBuildTriggerRecord.id)
        .where(
            ImageBuildTriggerRecord.task_id == task.id,
            ImageBuildTriggerRecord.status == "submitting",
            ImageBuildTriggerRecord.triggered_at > datetime.now(UTC) - SUBMISSION_WINDOW,
        )
        .limit(1)
    )
    if pending is not None:
        raise ImageBuildTriggerInProgress("该任务正在提交，请稍后查看触发记录")

    inputs = {name: value for name, value in task.inputs_json.items() if value is not None}
    if task.source_ref_mode == "branch" and supports_source_ref:
        inputs["source_ref"] = ""
    recommendation = None
    if task.source_ref_mode == "recommended" and source_branch:
        recommendation = await get_source_recommendation(db, source_branch, repository=repository)
        if recommendation is not None:
            inputs["source_ref"] = recommendation.commit_sha
    record = ImageBuildTriggerRecord(
        task_id=task.id,
        repository=repository,
        workflow_branch=task.workflow_branch,
        username=username,
        status="submitting",
        message="正在提交给 GitHub",
        dispatch_snapshot={
            "repository": repository,
            "workflow_path": WORKFLOW_PATH,
            "source_ref_mode": task.source_ref_mode,
            "source_recommendation": asdict(recommendation) if recommendation is not None else None,
            "inputs": redact_workflow_data(inputs),
        },
    )
    db.add(record)
    if task.source_ref_mode == "recommended" and recommendation is None:
        record.status = "failed"
        record.message = NO_SOURCE_RECOMMENDATION
        await db.flush()
        await db.refresh(record)
        await db.commit()
        return record

    await db.commit()

    try:
        token = await get_github_token(db)
    except ValueError as exc:
        record.status = "failed"
        record.message = str(exc)
    else:
        await db.commit()
        try:
            async with asyncio.timeout(DISPATCH_TIMEOUT_SECONDS):
                result = await dispatch_workflow(
                    token,
                    repository=repository,
                    workflow_path=WORKFLOW_PATH,
                    ref=record.workflow_branch,
                    inputs=inputs,
                    return_run_details=True,
                )
        except TimeoutError:
            record.status = "unknown"
            record.message = UNKNOWN_MESSAGE
        else:
            record.github_status_code = result["status_code"]
            if result["success"]:
                record.status = "submitted"
                record.message = "GitHub 已接收请求"
                record.run_id = result.get("run_id")
            elif result.get("submission_status") == "unknown":
                record.status = "unknown"
                record.message = UNKNOWN_MESSAGE
            else:
                record.status = "failed"
                record.message = ERROR_MESSAGES.get(result["status_code"], "提交到 GitHub 失败，请稍后重试")
    await db.flush()
    await db.refresh(record)
    await db.commit()
    return record
