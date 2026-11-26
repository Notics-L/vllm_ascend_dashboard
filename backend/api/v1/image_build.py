"""Manage workflows and saved trigger tasks for the image build page."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import defer

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from daily_pipeline.cron import schedule_trigger
from daily_pipeline.source_recommendation import SourceRecommendation, get_source_recommendation
from image_build import collection
from image_build.config import ImageBuildRepositoryError, get_image_build_repository
from image_build.schedule_conflicts import BUILD_TIMEZONE, schedules_overlap
from image_build.trigger import (
    NO_SOURCE_RECOMMENDATION,
    SUBMISSION_WINDOW,
    UNKNOWN_MESSAGE,
    ImageBuildTriggerInProgress,
    submit_trigger_task,
)
from image_build.workflow_sync import (
    WORKFLOW_PATH,
    ImageBuildWorkflowSync,
    WorkflowSyncCredentialError,
    WorkflowSyncInProgress,
    WorkflowSyncResult,
)
from infrastructure.db.mysql_lock import mysql_named_lock
from infrastructure.persistence.models import (
    ImageBuildTriggerRecord,
    ImageBuildTriggerTask,
    ImageBuildWorkflowDefinition,
)
from infrastructure.persistence.models.image_build_trigger_task import (
    SourceRefMode,
    TriggerInputValue,
)

router = APIRouter()
logger = logging.getLogger(__name__)
MANUAL_SYNC_TIMEOUT_SECONDS = 300


class WorkflowDefinitionResponse(BaseModel):
    branch_name: str
    workflow_url: str
    inputs: list[dict[str, Any]]
    inputs_hash: str
    update_at: datetime


async def _get_repository(db: DbSession) -> str:
    try:
        return await get_image_build_repository(db)
    except ImageBuildRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/workflows")
async def list_workflows(db: DbSession, _: CurrentUser) -> list[WorkflowDefinitionResponse]:
    repository = await _get_repository(db)
    workflow_url = f"https://github.com/{repository}/actions/workflows/{WORKFLOW_PATH.rsplit('/', 1)[-1]}"
    definitions = await db.scalars(
        select(ImageBuildWorkflowDefinition)
        .where(
            ImageBuildWorkflowDefinition.repository == repository,
            ImageBuildWorkflowDefinition.is_available.is_(True),
        )
        .order_by(ImageBuildWorkflowDefinition.branch_name)
    )
    return [
        WorkflowDefinitionResponse(
            branch_name=definition.branch_name,
            workflow_url=workflow_url,
            inputs=definition.inputs_json,
            inputs_hash=definition.inputs_hash,
            update_at=definition.update_at.replace(tzinfo=UTC),
        )
        for definition in definitions
    ]


@router.post("/workflows/sync")
async def sync_workflows(_: CurrentAdminUser) -> WorkflowSyncResult:
    try:
        async with asyncio.timeout(MANUAL_SYNC_TIMEOUT_SECONDS):
            return await ImageBuildWorkflowSync().run()
    except WorkflowSyncInProgress as exc:
        raise HTTPException(status_code=409, detail="已有 Workflow 同步正在运行，请稍后刷新") from exc
    except (ImageBuildRepositoryError, WorkflowSyncCredentialError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Workflow 同步超时，请稍后重试") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Manual image build workflow sync failed: %s", exc)
        raise HTTPException(status_code=502, detail="读取 GitHub Workflow 失败，请稍后重试") from exc


class TriggerTaskPayload(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    workflow_branch: str = Field(min_length=1, max_length=255)
    cron_expression: str | None = Field(default=None, max_length=120)
    source_ref_mode: SourceRefMode | None = None
    inputs: dict[str, TriggerInputValue] = Field(default_factory=dict)


class TriggerTaskResponse(TriggerTaskPayload):
    cron_expression: str
    id: int
    source_ref_mode: SourceRefMode
    update_at: datetime


def _trigger_task_response(task: ImageBuildTriggerTask) -> TriggerTaskResponse:
    return TriggerTaskResponse(
        id=task.id,
        workflow_branch=task.workflow_branch,
        cron_expression=task.cron_expression,
        source_ref_mode=task.source_ref_mode,
        inputs=task.inputs_json,
        update_at=task.update_at.replace(tzinfo=UTC),
    )


def _source_branch(
    inputs: dict[str, TriggerInputValue], fields: list[dict[str, Any]] | None,
) -> str | None:
    value = inputs.get("vllm_ascend_branch")
    if not value:
        value = next(
            (field.get("default") for field in fields or [] if field["name"] == "vllm_ascend_branch"),
            None,
        )
    return value.removeprefix("refs/heads/") if isinstance(value, str) and value else None


@asynccontextmanager
async def _trigger_task_save_lock(db: DbSession) -> AsyncIterator[None]:
    # Close the authentication read transaction so conflict checks see the
    # writes committed by the previous holder of the advisory lock.
    await db.commit()
    async with mysql_named_lock("image-build-trigger-task-save", wait_seconds=5) as acquired:
        if not acquired:
            raise HTTPException(status_code=503, detail="有任务正在保存，请稍后重试")
        yield


def _normalize_source_ref(
    payload: TriggerTaskPayload, *, supports_source_ref: bool,
) -> tuple[SourceRefMode, dict[str, TriggerInputValue]]:
    inputs = dict(payload.inputs)
    if not supports_source_ref:
        return payload.source_ref_mode or "branch", inputs
    ref = inputs.pop("source_ref", None)
    ref = ref.strip() if isinstance(ref, str) else ""
    mode = payload.source_ref_mode or ("manual" if ref else "branch")
    if mode == "manual":
        if not ref:
            raise HTTPException(status_code=400, detail="请输入 source_ref")
        inputs["source_ref"] = ref
    return mode, inputs


async def _validate_trigger_task(
    db: DbSession, payload: TriggerTaskPayload, *, exclude_id: int | None = None,
) -> tuple[str, SourceRefMode, dict[str, TriggerInputValue]]:
    cron_expression = " ".join((payload.cron_expression or "").split())
    trigger = None
    if cron_expression:
        try:
            trigger = schedule_trigger(cron_expression, BUILD_TIMEZONE)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请输入有效的五段 Cron 表达式") from exc

    repository = await _get_repository(db)
    workflow = await _get_available_workflow(db, repository, payload.workflow_branch)
    mode, inputs = _normalize_source_ref(payload, supports_source_ref=_supports_source_ref(workflow))
    _validate_workflow_inputs(workflow, inputs, source_ref_mode=mode)
    source_branch = _source_branch(inputs, workflow.inputs_json)
    if mode == "recommended" and (
        not source_branch or await get_source_recommendation(db, source_branch, repository=repository) is None
    ):
        raise HTTPException(status_code=400, detail=NO_SOURCE_RECOMMENDATION)
    if trigger is None or source_branch is None:
        return cron_expression, mode, inputs

    query = (
        select(ImageBuildTriggerTask, ImageBuildWorkflowDefinition.inputs_json)
        .outerjoin(
            ImageBuildWorkflowDefinition,
            and_(
                ImageBuildWorkflowDefinition.repository == repository,
                ImageBuildWorkflowDefinition.branch_name == ImageBuildTriggerTask.workflow_branch,
            ),
        )
        .where(ImageBuildTriggerTask.cron_expression != "")
        .order_by(ImageBuildTriggerTask.id)
    )
    if exclude_id is not None:
        query = query.where(ImageBuildTriggerTask.id != exclude_id)
    for task, fields in (await db.execute(query)).all():
        if _source_branch(task.inputs_json, fields) != source_branch:
            continue
        try:
            overlaps = schedules_overlap(trigger, task.cron_expression)
        except ValueError:
            continue  # An invalid stored Cron cannot fire.
        if overlaps:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"源码分支 {source_branch} 的执行时间与任务 #{task.id} "
                    f"（{task.cron_expression}）冲突，请修改 Cron 表达式。"
                ),
            )
    return cron_expression, mode, inputs


async def _get_available_workflow(db: DbSession, repository: str, branch: str) -> ImageBuildWorkflowDefinition:
    workflow = await db.get(ImageBuildWorkflowDefinition, (repository, branch))
    if workflow is None or not workflow.is_available:
        raise HTTPException(status_code=400, detail="请选择可用的 Workflow 分支")
    return workflow


def _supports_source_ref(workflow: ImageBuildWorkflowDefinition) -> bool:
    return any(field["name"] == "source_ref" and field["type"] == "string" for field in workflow.inputs_json)


def _validate_workflow_inputs(
    workflow: ImageBuildWorkflowDefinition, inputs: dict[str, TriggerInputValue], *, source_ref_mode: SourceRefMode,
) -> None:
    fields = {field["name"]: field for field in workflow.inputs_json}
    ref_field = fields.get("source_ref")
    if (source_ref_mode == "recommended" and not _supports_source_ref(workflow)) or (
        source_ref_mode == "manual" and ref_field is None
    ):
        raise HTTPException(status_code=400, detail="该 Workflow 分支不支持所选的 source_ref 模式")
    if inputs.keys() - fields.keys():
        raise HTTPException(status_code=400, detail="Workflow 参数定义已变化，请刷新后重试")

    for name, field in fields.items():
        if name == "source_ref" and source_ref_mode == "recommended":
            continue
        value = inputs.get(name)
        if value is None or value == "":
            if field["required"]:
                raise HTTPException(status_code=400, detail=f"请填写 {name}")
            continue
        kind = field["type"]
        if kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "number":
            valid = type(value) in (int, float)
        elif kind in ("choice", "environment"):
            valid = isinstance(value, str) and value in field["options"]
        else:
            valid = isinstance(value, str)
        if not valid:
            raise HTTPException(status_code=400, detail=f"参数 {name} 的值无效")


async def _save_trigger_task(db: DbSession, task: ImageBuildTriggerTask) -> TriggerTaskResponse:
    await db.flush()
    await db.refresh(task)
    result = _trigger_task_response(task)
    await db.commit()
    return result


async def _get_trigger_task(db: DbSession, task_id: int) -> ImageBuildTriggerTask:
    task = await db.get(ImageBuildTriggerTask, task_id, with_for_update=True)
    if task is None:
        raise HTTPException(status_code=404, detail="构建任务不存在")
    return task


@router.get("/trigger-tasks")
async def list_trigger_tasks(db: DbSession, _: CurrentUser) -> list[TriggerTaskResponse]:
    tasks = await db.scalars(select(ImageBuildTriggerTask).order_by(ImageBuildTriggerTask.id.desc()))
    return [_trigger_task_response(task) for task in tasks]


@router.post("/trigger-tasks", status_code=201)
async def create_trigger_task(
    payload: TriggerTaskPayload, db: DbSession, _: CurrentAdminUser,
) -> TriggerTaskResponse:
    async with _trigger_task_save_lock(db):
        cron_expression, mode, inputs = await _validate_trigger_task(db, payload)
        task = ImageBuildTriggerTask(
            workflow_branch=payload.workflow_branch,
            cron_expression=cron_expression,
            source_ref_mode=mode,
            inputs_json=inputs,
        )
        db.add(task)
        return await _save_trigger_task(db, task)


@router.put("/trigger-tasks/{task_id}")
async def update_trigger_task(
    task_id: int, payload: TriggerTaskPayload, db: DbSession, _: CurrentAdminUser,
) -> TriggerTaskResponse:
    async with _trigger_task_save_lock(db):
        task = await _get_trigger_task(db, task_id)
        cron_expression, mode, inputs = await _validate_trigger_task(db, payload, exclude_id=task_id)
        task.cron_expression = cron_expression
        task.workflow_branch = payload.workflow_branch
        task.source_ref_mode = mode
        task.inputs_json = inputs
        return await _save_trigger_task(db, task)


class TriggerTaskSelection(BaseModel):
    ids: list[PositiveInt] = Field(min_length=1)


@router.delete("/trigger-tasks")
async def delete_trigger_tasks(
    payload: TriggerTaskSelection, db: DbSession, _: CurrentAdminUser,
) -> dict[str, int]:
    result = await db.execute(
        delete(ImageBuildTriggerTask).where(ImageBuildTriggerTask.id.in_(set(payload.ids)))
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.delete("/trigger-tasks/{task_id}", status_code=204)
async def delete_trigger_task(task_id: int, db: DbSession, _: CurrentAdminUser) -> Response:
    task = await _get_trigger_task(db, task_id)
    await db.delete(task)
    await db.commit()
    return Response(status_code=204)


class TriggerRecordResponse(BaseModel):
    id: int
    task_id: int
    workflow_branch: str
    username: str
    status: str
    message: str
    run_id: int | None
    run_url: str | None
    triggered_at: datetime


def _trigger_record_response(record: ImageBuildTriggerRecord) -> TriggerRecordResponse:
    triggered_at = record.triggered_at.replace(tzinfo=UTC)
    status, message = record.status, record.message
    if status == "submitting" and datetime.now(UTC) - triggered_at >= SUBMISSION_WINDOW:
        status, message = "unknown", UNKNOWN_MESSAGE
    run_url = f"https://github.com/{record.repository}/actions/runs/{record.run_id}" if record.run_id else None
    return TriggerRecordResponse(
        id=record.id,
        task_id=record.task_id,
        workflow_branch=record.workflow_branch,
        username=record.username,
        status=status,
        message=message,
        run_id=record.run_id,
        run_url=run_url,
        triggered_at=triggered_at,
    )


@router.get("/trigger-records")
async def list_trigger_records(db: DbSession, _: CurrentUser) -> list[TriggerRecordResponse]:
    records = await db.scalars(
        select(ImageBuildTriggerRecord)
        .options(defer(ImageBuildTriggerRecord.dispatch_snapshot))
        .order_by(ImageBuildTriggerRecord.id.desc())
    )
    return [_trigger_record_response(record) for record in records]


class TriggerDispatchSnapshot(BaseModel):
    repository: str
    workflow_path: str
    source_ref_mode: SourceRefMode | None = None
    source_recommendation: SourceRecommendation | None = None
    inputs: dict[str, TriggerInputValue]


class TriggerRecordDetailResponse(TriggerRecordResponse):
    dispatch_snapshot: TriggerDispatchSnapshot | None
    github_status_code: int | None


@router.get("/trigger-records/{record_id}")
async def get_trigger_record(record_id: int, db: DbSession, _: CurrentUser) -> TriggerRecordDetailResponse:
    record = await db.get(ImageBuildTriggerRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="触发记录不存在")
    snapshot = record.dispatch_snapshot
    return TriggerRecordDetailResponse(
        **_trigger_record_response(record).model_dump(),
        dispatch_snapshot=TriggerDispatchSnapshot.model_validate(snapshot) if snapshot is not None else None,
        github_status_code=record.github_status_code,
    )


@router.post("/trigger-tasks/{task_id}/trigger")
async def trigger_task(task_id: int, db: DbSession, user: CurrentAdminUser) -> TriggerRecordResponse:
    username = user.username
    # Reset the read snapshot opened by authentication before acquiring the task lock.
    await db.commit()
    task = await _get_trigger_task(db, task_id)
    repository = await _get_repository(db)
    workflow = await _get_available_workflow(db, repository, task.workflow_branch)
    _validate_workflow_inputs(workflow, task.inputs_json, source_ref_mode=task.source_ref_mode)
    try:
        record = await submit_trigger_task(
            db, task, username, repository=repository, supports_source_ref=_supports_source_ref(workflow),
            source_branch=_source_branch(task.inputs_json, workflow.inputs_json),
        )
    except ImageBuildTriggerInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _trigger_record_response(record)


@router.get("/collection/runs")
async def list_collection_runs(
    db: DbSession, _: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    return await collection.list_runs(db, page, page_size)


@router.get("/collection/runs/{run_id}")
async def get_collection_run(run_id: PositiveInt, db: DbSession, _: CurrentUser):
    detail = await collection.get_detail(db, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="构建采集记录不存在")
    return detail


@router.post("/collection/runs/{run_id}/refresh")
async def refresh_collection_run(run_id: PositiveInt, db: DbSession, _: CurrentUser):
    if await collection.get_detail(db, run_id) is None:
        raise HTTPException(status_code=404, detail="构建采集记录不存在")
    await db.rollback()
    await collection.collect_run(run_id, force=True)
    return await get_collection_run(run_id, db, _)


@router.post("/collection/runs/{run_id}/rerun-failed", status_code=202)
async def rerun_failed_collection_jobs(run_id: PositiveInt, db: DbSession, user: CurrentAdminUser):
    try:
        status = await collection.rerun_failed_jobs(db, run_id, user.username)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except collection.RerunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except collection.RerunUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": status}
