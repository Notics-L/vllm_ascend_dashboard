from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from daily_pipeline.cases import CatalogSourceError, DailyCaseService
from daily_pipeline.collection import DailyCollectionService
from daily_pipeline.collection_source import CollectionSourceError
from daily_pipeline.dispatch import trigger_daily_pipeline
from daily_pipeline.history import DailyTriggerHistoryService
from daily_pipeline.notification_config import (
    failure_notification_config,
    notification_config,
    save_failure_notification_config,
    save_notification_config,
)
from daily_pipeline.notification_consumption import DailyNotificationConsumptionService
from daily_pipeline.notification_recipients import DailyNotificationRecipientService
from daily_pipeline.overview import daily_overview
from daily_pipeline.shared_processing import share_processing_flow
from daily_pipeline.tracking import DailyTrackingService, ManualInputError
from daily_pipeline.trigger_config import (
    DEFAULT_TIMEZONE,
    TRIGGER_TASK,
    check_trigger_connection,
    preview_schedule,
    save_trigger_config,
    trigger_config,
)
from daily_pipeline.version_resolution import share_version_flow, submit_version_action
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError
from infrastructure.persistence.daily_pipeline_cases import DailyCaseRepository
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository
from infrastructure.persistence.daily_pipeline_history import DailyTriggerHistoryRepository
from infrastructure.persistence.daily_pipeline_notifications import (
    DailyNotificationOutboxRepository,
    DailyNotificationRecipientRepository,
)
from infrastructure.persistence.daily_pipeline_schedules import read_schedule

router = APIRouter(prefix="/daily-pipeline", tags=["Daily 流水线"])


@router.get("/overview")
async def overview(user: CurrentUser, db: DbSession, days: Annotated[int, Query(ge=7, le=14)] = 14):
    if days not in (7, 14):
        raise HTTPException(422, "统计范围支持 7 天或 14 天")
    return await daily_overview(db, days)


def get_case_service(db: DbSession) -> DailyCaseService:
    return DailyCaseService(DailyCaseRepository(db))


CaseService = Annotated[DailyCaseService, Depends(get_case_service)]


def get_history_service(db: DbSession) -> DailyTriggerHistoryService:
    return DailyTriggerHistoryService(DailyTriggerHistoryRepository(db))


HistoryService = Annotated[DailyTriggerHistoryService, Depends(get_history_service)]


class HistoryIds(BaseModel):
    ids: set[Annotated[int, Field(gt=0)]] = Field(min_length=1)


@router.post("/trigger")
async def manual_trigger(user: CurrentAdminUser, db: DbSession):
    try:
        return await trigger_daily_pipeline(db)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/trigger-history")
async def list_trigger_history(
    user: CurrentUser,
    service: HistoryService,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 5,
):
    return await service.list_history(page, page_size)


@router.get("/trigger-history/{history_id}")
async def get_trigger_history(
    history_id: Annotated[int, ApiPath(gt=0)], user: CurrentUser, service: HistoryService,
):
    try:
        return await service.get_history(history_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/trigger-history")
async def delete_trigger_history(body: HistoryIds, user: CurrentAdminUser, service: HistoryService):
    return await service.delete_history(body.ids)


class CaseSelection(BaseModel):
    nightlyCaseIds: list[str]


CaseStatus = Literal["启用", "禁用"]
TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CaseIds(BaseModel):
    ids: set[str] = Field(min_length=1)


class CaseStatusUpdate(CaseIds):
    status: CaseStatus


class CaseEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[TagName] | None = Field(default=None, min_length=1)
    status: CaseStatus | None = None


class ScheduleInput(BaseModel):
    cron: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class TriggerTokenInput(BaseModel):
    githubToken: SecretStr | None = Field(default=None, max_length=4096)


class TriggerConfigInput(ScheduleInput, TriggerTokenInput):
    pass


@router.post("/trigger-config/test-connection")
async def test_connection(body: TriggerTokenInput, user: CurrentAdminUser, db: DbSession):
    token = body.githubToken.get_secret_value().strip() if body.githubToken else None
    try:
        return await check_trigger_connection(db, token)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        messages = {
            401: "GitHub Token 无效或已过期",
            403: "GitHub 拒绝访问，请检查授权或 API 限额",
            404: "目标仓库或工作流不存在，或 Token 无权访问",
            429: "GitHub 请求受限，请稍后重试",
        }
        raise HTTPException(502, messages.get(exc.response.status_code, "GitHub 服务响应异常")) from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "无法连接 GitHub，请稍后重试") from exc


@router.get("/trigger-config")
async def get_trigger_config(user: CurrentUser, db: DbSession):
    return await trigger_config(db)


@router.put("/trigger-config")
async def update_trigger_config(body: TriggerConfigInput, user: CurrentAdminUser, db: DbSession):
    token = body.githubToken.get_secret_value().strip() if body.githubToken else None
    try:
        return await save_trigger_config(db, body.cron, token)
    except SchedulerUpdateError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "Cron 表达式无效") from exc


@router.get("/schedule")
async def get_schedule(user: CurrentUser, db: DbSession):
    schedule = await read_schedule(db, TRIGGER_TASK)
    return preview_schedule(*schedule) if schedule else preview_schedule(None)


@router.post("/schedule/preview")
async def schedule_preview(body: ScheduleInput, user: CurrentUser, db: DbSession):
    schedule = await read_schedule(db, TRIGGER_TASK)
    try:
        return preview_schedule(body.cron, schedule[1] if schedule else DEFAULT_TIMEZONE)
    except ValueError as exc:
        raise HTTPException(422, "Cron 表达式无效") from exc


@router.get("/cases")
async def list_cases(user: CurrentUser, service: CaseService, is_daily: bool = True):
    try:
        return await service.list_cases(is_daily=is_daily)
    except CatalogSourceError as exc:
        raise HTTPException(502, "无法读取 Nightly 用例配置") from exc


@router.put("/cases/selection")
async def save_selection(body: CaseSelection, user: CurrentAdminUser, service: CaseService):
    try:
        return await service.save_selection(set(body.nightlyCaseIds))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/cases")
async def remove_cases(body: CaseIds, user: CurrentAdminUser, service: CaseService):
    return await service.remove_cases(body.ids)


@router.patch("/cases/status")
async def set_case_status(body: CaseStatusUpdate, user: CurrentAdminUser, service: CaseService):
    try:
        return await service.set_enabled(body.ids, enabled=body.status == "启用")
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/cases/{case_id:path}")
async def edit_case(case_id: str, body: CaseEdit, user: CurrentAdminUser, service: CaseService):
    if body.status is None and body.tags is None:
        raise HTTPException(422, "请提供标签或状态")
    enabled = None if body.status is None else body.status == "启用"
    try:
        return await service.edit_case(case_id, tags=body.tags, enabled=enabled)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


def get_collection_service(db: DbSession) -> DailyCollectionService:
    return DailyCollectionService(DailyCollectionRepository(db))


CollectionService = Annotated[DailyCollectionService, Depends(get_collection_service)]


class CollectionRunIds(BaseModel):
    runIds: set[Annotated[int, Field(gt=0)]] = Field(min_length=1)


@router.get("/collection/runs")
async def list_collection_runs(user: CurrentUser, service: CollectionService):
    return await service.list_runs()


@router.get("/collection/runs/{run_id}")
async def collection_detail(
    run_id: Annotated[int, ApiPath(gt=0)], user: CurrentUser, service: CollectionService,
):
    try:
        return await service.detail(run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/collection/runs/{run_id}/recollect")
async def recollect_run(
    run_id: Annotated[int, ApiPath(gt=0)], user: CurrentAdminUser, service: CollectionService,
):
    try:
        return await service.recollect(run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SchedulerUpdateError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.delete("/collection/runs")
async def delete_collection_runs(body: CollectionRunIds, user: CurrentAdminUser, service: CollectionService):
    return await service.delete(body.runIds)


@router.get("/issues/{number}")
async def collection_issue_detail(
    number: Annotated[int, ApiPath(gt=0)], user: CurrentUser, service: CollectionService,
):
    try:
        return await DailyTrackingService(service.repository.db).issue_detail(number)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except CollectionSourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/failed-cases")
async def failed_cases(user: CurrentUser, db: DbSession):
    return await DailyTrackingService(db).failures()


class ProcessingFlowShareInput(BaseModel):
    failureIds: set[Annotated[int, Field(gt=0)]]
    targetFailureId: Annotated[int, Field(gt=0)]


@router.post("/failed-cases/processing-flow/share")
async def share_failed_case_processing_flow(body: ProcessingFlowShareInput, user: CurrentAdminUser, db: DbSession):
    try:
        return await share_processing_flow(db, body.failureIds, body.targetFailureId)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/failed-cases/version-flow/share")
async def share_failed_case_version_flow(body: ProcessingFlowShareInput, user: CurrentAdminUser, db: DbSession):
    try:
        return await share_version_flow(db, body.failureIds, body.targetFailureId)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class VersionFlowActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["external_recovered", "track_pr", "track_build"]
    repair_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    revert_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    build_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] | None = None


@router.post("/failed-cases/{failure_id}/version-flow")
async def update_failed_case_version_flow(
    failure_id: Annotated[int, ApiPath(gt=0)], body: VersionFlowActionInput, user: CurrentAdminUser, db: DbSession,
):
    try:
        return await submit_version_action(
            DailyTrackingService(db), failure_id, body.action, user.username, body.model_dump(exclude_none=True),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except CollectionSourceError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/failed-cases/{failure_id}")
async def failed_case_detail(failure_id: int, user: CurrentUser, db: DbSession):
    try:
        return await DailyTrackingService(db).detail(failure_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/issues")
async def tracking_issues(user: CurrentUser, db: DbSession):
    return await DailyTrackingService(db).issues()


class IssueNumbers(BaseModel):
    numbers: set[Annotated[int, Field(gt=0)]] = Field(min_length=1)


@router.post("/issues/close")
async def close_tracking_issues(body: IssueNumbers, user: CurrentAdminUser, db: DbSession):
    try:
        return await DailyTrackingService(db).close(body.numbers)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, CollectionSourceError) as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/issues")
async def delete_tracking_issues(body: IssueNumbers, user: CurrentAdminUser, db: DbSession):
    try:
        return await DailyTrackingService(db).delete(body.numbers)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class NodeStart(BaseModel):
    requestData: dict = Field(default_factory=dict)


class NodeFinish(BaseModel):
    success: bool
    result: dict = Field(default_factory=dict)
    message: str = Field(max_length=10000)


@router.post("/processing/nodes/{node_id}/start")
async def start_processing_node(node_id: int, body: NodeStart, user: CurrentAdminUser, db: DbSession):
    try:
        return await DailyTrackingService(db).start_node(node_id, body.requestData)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/processing/executions/{execution_id}/finish")
async def finish_processing_node(execution_id: int, body: NodeFinish, user: CurrentAdminUser, db: DbSession):
    try:
        return await DailyTrackingService(db).finish_node(execution_id, body.success, body.result, body.message)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class NodeWait(BaseModel):
    result: dict
    message: str = Field(default="等待外部结果", max_length=10000)


@router.post("/processing/executions/{execution_id}/wait")
async def wait_processing_node(execution_id: int, body: NodeWait, user: CurrentAdminUser, db: DbSession):
    try:
        return await DailyTrackingService(db).wait_node(execution_id, body.result, body.message)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ManualNodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["code", "version", "environment"] | None = None
    revert_mode: Literal["auto", "manual"] | None = None
    pr_number: Annotated[int, Field(strict=True, gt=0)] | None = None
    pr_repository: Annotated[str, StringConstraints(max_length=255)] | None = None
    pr_url: Annotated[str, StringConstraints(max_length=1000)] | None = None
    run_id: Annotated[int, Field(strict=True, gt=0)] | None = None


class ManualNodeFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = True
    result: ManualNodeResult = Field(default_factory=ManualNodeResult)
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] = ""


@router.post("/processing/nodes/{node_id}/manual-complete")
async def manual_complete_processing_node(
    node_id: Annotated[int, ApiPath(gt=0)], body: ManualNodeFinish, user: CurrentAdminUser, db: DbSession,
):
    try:
        return await DailyTrackingService(db).manual_complete(
            node_id, body.success, body.result.model_dump(exclude_none=True), body.message, user.username,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ManualInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/processing/nodes/{node_id}/retry")
async def retry_processing_node(node_id: Annotated[int, ApiPath(gt=0)], user: CurrentAdminUser, db: DbSession):
    try:
        result = await DailyTrackingService(db).retry_processing(node_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ManualInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result["queued"]:
        raise HTTPException(503, "自动处理任务未送达，请稍后重试")
    return result


class NotificationConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr | None = Field(default=None, max_length=4096)


@router.get("/notification-config")
async def get_notification_config(user: CurrentUser, db: DbSession):
    return await notification_config(db)


@router.put("/notification-config")
async def update_notification_config(body: NotificationConfigInput, user: CurrentAdminUser, db: DbSession):
    return await save_notification_config(db, body.token.get_secret_value() if body.token else None)


NotificationGitHubName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$"),
]


class FailureNotificationConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    githubNames: list[NotificationGitHubName] = Field(max_length=100)


@router.get("/failure-notification-config")
async def get_failure_notification_config(user: CurrentUser, db: DbSession):
    return await failure_notification_config(db)


@router.put("/failure-notification-config")
async def update_failure_notification_config(body: FailureNotificationConfigInput, user: CurrentAdminUser, db: DbSession):
    return await save_failure_notification_config(db, body.githubNames)


class NotificationRecipientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    githubName: NotificationGitHubName
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    account: SecretStr | None = Field(default=None, max_length=100)


def get_recipient_service(db: DbSession):
    return DailyNotificationRecipientService(DailyNotificationRecipientRepository(db))


RecipientService = Annotated[DailyNotificationRecipientService, Depends(get_recipient_service)]


@router.get("/notification-recipients")
async def list_notification_recipients(user: CurrentUser, service: RecipientService):
    return await service.list()


@router.post("/notification-recipients")
async def add_notification_recipient(body: NotificationRecipientInput, user: CurrentAdminUser, service: RecipientService):
    try:
        return await service.save(body.githubName, body.name, body.account.get_secret_value().strip() if body.account else None)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/notification-recipients/{identity}")
async def edit_notification_recipient(identity: Annotated[int, ApiPath(gt=0)], body: NotificationRecipientInput, user: CurrentAdminUser, service: RecipientService):
    try:
        return await service.save(body.githubName, body.name, body.account.get_secret_value().strip() if body.account else None, identity)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/notification-recipients/{identity}")
async def delete_notification_recipient(identity: Annotated[int, ApiPath(gt=0)], user: CurrentAdminUser, service: RecipientService):
    try:
        return await service.delete(identity)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class NotificationRecipientIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: set[Annotated[int, Field(gt=0)]] = Field(min_length=1)


@router.delete("/notification-recipients")
async def delete_notification_recipients(body: NotificationRecipientIds, user: CurrentAdminUser, service: RecipientService):
    try:
        return await service.delete_many(body.ids)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


class NotificationMessage(BaseModel):
    id: str
    content: str
    githubName: str
    receiptToken: str


class NotificationResult(BaseModel):
    id: str
    result: Literal["sent", "failed", "unknown"]
    alreadyProcessed: bool


def get_notification_consumption_service(db: DbSession):
    return DailyNotificationConsumptionService(DailyNotificationOutboxRepository(db))


NotificationConsumptionService = Annotated[
    DailyNotificationConsumptionService, Depends(get_notification_consumption_service)
]


@router.get("/notifications/claim", response_model=NotificationMessage | None)
async def claim_notification(user: CurrentAdminUser, service: NotificationConsumptionService, response: Response):
    """领取一条通知，无消息返回 null。需管理员 Bearer Token。

    返回 GitHub 用户名和通知内容；账号映射和小鲁班 Token 由消费端管理。
    消费端保存 id 和 receiptToken，发送后回传结果。领取后不自动重复投递。
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Authorization"
    try:
        return await service.claim(user.username)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/notifications/{identity}/result", response_model=NotificationResult)
async def acknowledge_notification(
    identity: Annotated[int, ApiPath(gt=0)],
    result: Annotated[Literal["sent", "failed", "unknown"], Query()],
    receipt_token: Annotated[SecretStr, Header(alias="X-Notification-Receipt", min_length=32, max_length=128)],
    user: CurrentAdminUser, service: NotificationConsumptionService, response: Response,
):
    """通过 result 查询参数回传 sent/failed/unknown。

    使用领取时同一管理员的 Bearer Token，以及 X-Notification-Receipt 请求头。
    重复回传相同终态幂等；仅 sent 完成通知节点。failed 可重试，unknown 等待确认。
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Authorization"
    try:
        return await service.acknowledge(identity, receipt_token.get_secret_value(), result, user.username)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/notification-records")
async def notification_consumption_records(
    user: CurrentUser, service: NotificationConsumptionService,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    result: Literal["waiting", "sent", "failed", "unknown", "ended"] | None = None,
    status: Literal["pending", "processing", "completed", "ended"] | None = None,
):
    """展示待消费及已消费通知，可按消费状态及发送结果筛选，不返回任何凭据。"""
    return await service.records(page, page_size, result, status)
