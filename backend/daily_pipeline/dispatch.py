"""Dispatch the saved Daily selection and record its completed outcome."""
import logging
from datetime import UTC, datetime
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.cases import HARDWARE, case_view
from daily_pipeline.history import history_detail
from daily_pipeline.recommendation_events import note_recommendation_round
from daily_pipeline.source_recommendation import begin_recommendation_round
from infrastructure.clients.daily_pipeline_config import dispatch_daily_workflow
from infrastructure.clients.daily_pipeline_scheduler import SchedulerUpdateError, start_collection
from infrastructure.core.github_audit import (
    GITHUB_AUDIT_UNAVAILABLE_MESSAGE,
    GitHubAuditUnavailable,
)
from infrastructure.core.github_credentials import daily_github_credential
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.daily_pipeline_cases import DailyCaseRepository
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository
from infrastructure.persistence.daily_pipeline_history import DailyTriggerHistoryRepository
from infrastructure.persistence.daily_pipeline_settings import read_settings
from infrastructure.persistence.daily_pipeline_tracking import (
    active_daily_failure_times,
    is_case_fault_blocked,
)

logger = logging.getLogger(__name__)
WORKFLOW = "schedule_daily_test.yaml"


async def trigger_daily_pipeline(
    db: AsyncSession, *, event: Literal["手动触发", "定时触发"] = "手动触发", cron: str | None = None,
    include_in_recommendation: bool = True,
):
    include_in_recommendation = event == "定时触发" or include_in_recommendation
    settings = await read_settings(db, "repository", "source_branch", "github_token")
    repository = settings.get("repository") or ""
    branch = settings.get("source_branch") or ""
    credential = daily_github_credential(settings)
    rows = await DailyCaseRepository(db).list_cases(is_daily=True)
    failure_times = await active_daily_failure_times(db)
    enabled_rows = [row for row in rows if row.enabled and not is_case_fault_blocked(row, failure_times)]
    selected = [case_view(row) for row in enabled_rows]
    cases = {hardware: [] for hardware in HARDWARE}
    for row in enabled_rows:
        cases[row.hardware].append(row.name)
    snapshots = [
        {key: case[key] for key in ("id", "name", "hardware", "testType")} for case in selected
    ]
    started_at = datetime.now(UTC)
    logs = [{
        "time": started_at.isoformat(), "level": "INFO",
        "message": f"{event} {repository}@{branch} 的 {WORKFLOW}，Daily 用例 {len(snapshots)} 条",
    }]
    logger.info(
        "Dispatching Daily workflow: event=%s repository=%s branch=%s workflow=%s enabled_cases=%d",
        event, repository, branch, WORKFLOW, len(snapshots),
    )
    run_id = None
    result = "触发成功"
    error = None
    try:
        if not (repository and branch and credential):
            raise ValueError("请先保存 GitHub Token 并配置目标仓库和分支")
        credential.validate()
        run_id = await dispatch_daily_workflow(repository, branch, credential, cases)
    except httpx.HTTPStatusError as exc:
        messages = {
            401: "GitHub Token 无效或已过期",
            403: "GitHub 拒绝触发，请检查 Token 权限或 API 限额",
            404: "目标仓库、分支或工作流不存在，或 Token 无权访问",
            422: "GitHub 拒绝触发，请检查工作流的分支和输入参数",
            429: "GitHub 请求受限，请稍后重试",
        }
        error = messages.get(exc.response.status_code, "GitHub 触发服务响应异常")
    except httpx.RequestError:
        error = "GitHub 触发请求未正常完成，请检查 GitHub 运行记录"
    except GitHubAuditUnavailable:
        error = GITHUB_AUDIT_UNAVAILABLE_MESSAGE
    except ValueError as exc:
        error = str(exc)
    if error:
        result = "触发失败"
        logger.error(
            "Daily workflow dispatch failed: event=%s repository=%s branch=%s workflow=%s error=%s",
            event, repository, branch, WORKFLOW, error,
        )
    else:
        logger.info(
            "Daily workflow dispatch succeeded: event=%s repository=%s branch=%s workflow=%s run_id=%s",
            event, repository, branch, WORKFLOW, run_id,
        )
    logs.append({
        "time": datetime.now(UTC).isoformat(), "level": "ERROR" if error else "INFO",
        "message": error or f"触发成功，Run ID {run_id}",
    })
    try:
        row = await DailyTriggerHistoryRepository(db).add_history(
            triggered_at=started_at.replace(tzinfo=None), event=event, result=result,
            repository=repository, source_branch=branch, workflow=WORKFLOW, cron_expression=cron,
            daily_cases=snapshots, run_id=run_id, logs=logs, commit=False,
            merged_prs_synced=False if run_id is not None else None,
        )
        if run_id is not None:
            await DailyCollectionRepository(db).ensure_root(run_id, WORKFLOW, commit=False)
            if include_in_recommendation:
                note_recommendation_round(db, row)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Could not persist Daily trigger history: event=%s repository=%s branch=%s result=%s run_id=%s",
            event, repository, branch, result, run_id,
        )
        raise
    logger.info(
        "Daily trigger history saved: history_id=%s event=%s result=%s run_id=%s",
        row.id, event, result, run_id,
    )
    if run_id is not None:
        try:
            await start_collection(run_id)
        except SchedulerUpdateError:
            # Keep the successful dispatch and its unfinished root persisted.
            # Re-register through recollect or recover it on scheduler startup.
            logger.warning("Daily collection registration failed; run remains pending for recovery or manual retry run_id=%s", run_id)
    if run_id is not None and include_in_recommendation:
        try:
            async with SessionLocal.begin() as recommendation_db:
                await begin_recommendation_round(recommendation_db, row)
        except Exception:
            logger.exception(
                "Could not initialize Daily recommendation round; refresh will retry history_id=%s run_id=%s",
                row.id, run_id,
            )
    return history_detail(row)
