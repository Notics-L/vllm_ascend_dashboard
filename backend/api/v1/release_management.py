"""
发布件管理 API 路由
"""
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from starlette.responses import StreamingResponse
from sqlalchemy import func, select

from api.deps import CurrentUser, DbSession
from infrastructure.clients.github_client import GitHubAuthenticationError, GitHubClient
from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    LLMProviderConfig,
    ReleaseAnalysisTask,
    ReleaseAIEvaluation,
    ReleaseBuildRun,
    ReleaseSyncSchedule,
    ReleaseWorkflow,
)
from release_management.ai_evaluator import TrustworthinessEvaluator
from release_management.report_generator import ReportGenerator
from release_management.collector import ReleaseWorkflowCollector
from release_management.service import ReleaseManagementService
from release_management.third_party_service import ThirdPartyService

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== Dependency Factories ====================

async def get_github_client() -> GitHubClient:
    token = settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(400, "GitHub Token 未配置")
    return GitHubClient(
        token=token,
        owner=settings.GITHUB_OWNER or "vllm-project",
        repo=settings.GITHUB_REPO or "vllm-ascend",
    )


async def get_release_service(
    db: DbSession,
    github_client: Annotated[GitHubClient, Depends(get_github_client)],
) -> ReleaseManagementService:
    return ReleaseManagementService(db, github_client)


async def get_third_party_service(
    db: DbSession,
) -> ThirdPartyService:
    return ThirdPartyService(db)


async def get_evaluator(
    db: DbSession,
) -> TrustworthinessEvaluator:
    return TrustworthinessEvaluator(db)


async def get_report_generator() -> ReportGenerator:
    return ReportGenerator()


def _make_github() -> GitHubClient:
    token = settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(400, "GitHub Token 未配置")
    return GitHubClient(
        token=token,
        owner=settings.GITHUB_OWNER or "vllm-project",
        repo=settings.GITHUB_REPO or "vllm-ascend",
    )


# ==================== Config Check ====================

@router.get("/release-management/config-check")
async def check_config(db: DbSession):
    result = {
        "github_configured": bool(settings.GITHUB_TOKEN),
        "github_owner": settings.GITHUB_OWNER or "vllm-project",
        "github_repo": settings.GITHUB_REPO or "vllm-ascend",
        "llm_configured": False,
        "llm_providers": [],
    }

    try:
        stmt = select(LLMProviderConfig).where(LLMProviderConfig.enabled)
        result_query = await db.execute(stmt)
        providers = result_query.scalars().all()
        result["llm_providers"] = [
            {
                "id": p.id,
                "provider": p.provider,
                "display_name": p.display_name,
                "is_active": p.is_active,
                "default_model": p.default_model,
            }
            for p in providers
        ]
        result["llm_configured"] = any(p.is_active for p in providers)
    except Exception as e:
        logger.warning(f"Failed to check LLM config: {e}")

    return result


# ==================== Workflow Management ====================

@router.get("/release-management/workflows")
async def list_workflows(
    db: DbSession,
    enabled_only: bool = Query(True),
):
    svc = ReleaseManagementService(db, _make_github())
    return await svc.list_workflows(enabled_only=enabled_only)


@router.post("/release-management/workflows/discover")
async def discover_workflows(
    db: DbSession,
    current_user: CurrentUser,
    owner: str = Query(default="vllm-project"),
    repo: str = Query(default="vllm-ascend"),
):
    token = settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(400, "GitHub Token 未配置")
    svc = ReleaseManagementService(db, GitHubClient(
        token=token,
        owner=owner,
        repo=repo,
    ))
    return await svc.discover_and_register(owner, repo)


@router.put("/release-management/workflows/{workflow_id}/toggle")
async def toggle_workflow(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int,
    enabled: bool = Query(True),
):
    svc = ReleaseManagementService(db, _make_github())
    return await svc.toggle_workflow(workflow_id, enabled)


# ==================== Sync ====================

def _task_to_dict(task: ReleaseAnalysisTask, workflow_name: str | None = None) -> dict[str, Any]:
    duration = 0.0
    if task.started_at and task.completed_at:
        try:
            s = task.started_at.replace(tzinfo=None) if task.started_at.tzinfo is None else task.started_at
            e = task.completed_at.replace(tzinfo=None) if task.completed_at.tzinfo is None else task.completed_at
            if s.tzinfo != e.tzinfo:
                s = s.replace(tzinfo=None)
                e = e.replace(tzinfo=None)
            duration = (e - s).total_seconds()
        except Exception:
            duration = task.duration_seconds or 0.0
    return {
        "id": task.id,
        "workflow_id": task.workflow_id,
        "workflow_name": workflow_name or "",
        "task_type": task.task_type,
        "trigger_source": task.trigger_source or "manual",
        "status": task.status,
        "progress": task.progress or 0,
        "progress_message": task.progress_message,
        "total_items": task.total_items,
        "processed_items": task.processed_items,
        "collected_items": task.collected_items or 0,
        "days_back": task.days_back,
        "max_runs": task.max_runs,
        "duration_seconds": round(duration, 1),
        "error_message": task.error_message,
        "errors_json": task.errors_json,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


async def _run_sync_task(task_id: int, workflow_id: int, days_back: int, max_runs: int, sync_mode: str = "incremental", since_date: str | None = None, until_date: str | None = None):
    """后台执行同步任务 — 支持全量/增量/范围三种模式"""
    from infrastructure.db.base import SessionLocal as AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            task = await db.get(ReleaseAnalysisTask, task_id)
            if not task:
                return

            # 检查是否已被取消
            if task.status == "cancelled":
                logger.info(f"Sync task {task_id} was cancelled before execution")
                return

            # 标记为 running
            task.status = "running"
            task.progress_message = f"初始化同步任务 (模式: {sync_mode})..."
            await db.commit()

            wf = await db.get(ReleaseWorkflow, workflow_id)
            if not wf:
                task.status = "failed"
                task.error_message = "Workflow not found"
                task.progress_message = "同步失败: Workflow 不存在"
                task.completed_at = datetime.now(UTC)
                await db.commit()
                return

            github = GitHubClient(
                token=settings.GITHUB_TOKEN or "",
                owner=wf.repo_owner,
                repo=wf.repo_name,
            )
            collector = ReleaseWorkflowCollector(db, github)
            collected = await collector.sync_workflow_runs(
                wf, api_task_id=task_id, days_back=days_back, max_runs=max_runs,
                sync_mode=sync_mode, since_date=since_date, until_date=until_date
            )

            # 检查是否在执行过程中被取消
            task = await db.get(ReleaseAnalysisTask, task_id)
            if task and task.status == "cancelled":
                logger.info(f"Sync task {task_id} was cancelled during execution")
                return

            # 更新 API 任务为完成状态
            if task and task.status != "cancelled":
                task.collected_items = collected or 0
                task.total_items = max_runs
                task.processed_items = max_runs
                task.status = "completed"
                task.progress = 100
                task.progress_message = f"同步完成，采集 {collected or 0} 条新记录"
                task.completed_at = datetime.now(UTC)
                await db.commit()
        except Exception as e:
            # collector 中已处理失败标记，这里做兜底
            try:
                task = await db.get(ReleaseAnalysisTask, task_id)
                if task and task.status not in ("failed", "cancelled"):
                    task.status = "failed"
                    task.error_message = str(e)
                    task.progress_message = f"同步失败: {str(e)[:200]}"
                    task.completed_at = datetime.now(UTC)
                    await db.commit()
            except Exception:
                pass
            logger.error(f"Background sync task {task_id} failed: {e}")


@router.post("/release-management/sync")
async def trigger_sync(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(...),
    days_back: int = Query(30, ge=1, le=365),
    max_runs: int = Query(100, ge=1, le=500),
    sync_mode: str = Query("incremental"),
    since_date: str | None = Query(None),
    until_date: str | None = Query(None),
):
    """触发异步同步任务 - 支持 全量/增量/范围 三种模式"""
    import asyncio
    token = settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(400, "GitHub Token 未配置")

    # 检查是否有 running/pending 任务
    active_stmt = select(ReleaseAnalysisTask).where(
        ReleaseAnalysisTask.workflow_id == workflow_id,
        ReleaseAnalysisTask.status.in_(["running", "pending"]),
    )
    active_result = await db.execute(active_stmt)
    if active_result.scalar_one_or_none():
        raise HTTPException(409, "该工作流已有同步任务正在执行中，请稍后再试")

    task = ReleaseAnalysisTask(
        workflow_id=workflow_id,
        task_type="sync_runs",
        trigger_source="manual",
        status="pending",
        progress=0,
        progress_message="等待执行...",
        total_items=max_runs,
        days_back=days_back,
        max_runs=max_runs,
        started_at=datetime.now(UTC),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    asyncio.create_task(_run_sync_task(task.id, workflow_id, days_back, max_runs, sync_mode, since_date, until_date))

    return {
        "task_id": task.id,
        "status": task.status,
        "message": "同步任务已创建，后台执行中...",
    }


@router.post("/release-management/sync/retry/{task_id}")
async def retry_sync(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """根据历史记录重试同步"""
    import asyncio
    token = settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(400, "GitHub Token 未配置")

    old_task = await db.get(ReleaseAnalysisTask, task_id)
    if not old_task:
        raise HTTPException(404, f"同步任务 {task_id} 不存在")

    days_back = old_task.days_back or 30
    max_runs = old_task.max_runs or 100
    workflow_id = old_task.workflow_id

    # 检查是否有 running/pending 任务
    active_stmt = select(ReleaseAnalysisTask).where(
        ReleaseAnalysisTask.workflow_id == workflow_id,
        ReleaseAnalysisTask.status.in_(["running", "pending"]),
    )
    active_result = await db.execute(active_stmt)
    if active_result.scalar_one_or_none():
        raise HTTPException(409, "该工作流已有同步任务正在执行中，请稍后再试")

    task = ReleaseAnalysisTask(
        workflow_id=workflow_id,
        task_type="sync_runs",
        trigger_source="retry",
        status="pending",
        progress=0,
        progress_message="等待执行（重试）...",
        total_items=max_runs,
        days_back=days_back,
        max_runs=max_runs,
        started_at=datetime.now(UTC),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    asyncio.create_task(_run_sync_task(task.id, workflow_id, days_back, max_runs))

    return {
        "task_id": task.id,
        "status": task.status,
        "message": "重试任务已创建",
    }


@router.post("/release-management/sync/cancel/{task_id}")
async def cancel_sync_task(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """取消/终止正在执行的同步任务"""
    task = await db.get(ReleaseAnalysisTask, task_id)
    if not task:
        raise HTTPException(404, f"同步任务 {task_id} 不存在")
    if task.status not in ("running", "pending"):
        raise HTTPException(400, f"任务状态为 {task.status}，无法取消")
    task.status = "cancelled"
    task.progress_message = "已手动终止"
    task.completed_at = datetime.now(UTC)
    await db.commit()
    wf = await db.get(ReleaseWorkflow, task.workflow_id)
    return _task_to_dict(task, wf.workflow_name if wf else None)


@router.get("/release-management/sync/task-by-id/{task_id}")
async def get_sync_task(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """获取单个同步任务状态"""
    task = await db.get(ReleaseAnalysisTask, task_id)
    if not task:
        raise HTTPException(404, f"同步任务 {task_id} 不存在")
    wf = await db.get(ReleaseWorkflow, task.workflow_id)
    return _task_to_dict(task, wf.workflow_name if wf else None)


@router.get("/release-management/sync/archive/{task_id}")
async def archive_sync_task(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """归档：将同步采集的记录回填到构建分析中（返回 overview + runs 供前端展示）"""
    task = await db.get(ReleaseAnalysisTask, task_id)
    if not task or task.status != "completed":
        raise HTTPException(400, "只有已完成的同步任务可以查看")

    wf = await db.get(ReleaseWorkflow, task.workflow_id)
    if not wf:
        raise HTTPException(404, "Workflow 不存在")

    svc = ReleaseManagementService(db, _make_github())
    overview = await svc.get_overview(workflow_ids=[task.workflow_id], days_back=task.days_back or 30)
    runs_data = await svc.get_runs(workflow_ids=[task.workflow_id], page=1, page_size=200)

    total_runs = runs_data.get("total", 0)
    if total_runs == 0:
        raise HTTPException(400, "该工作流暂无构建数据，请先执行一次同步采集")

    return {
        "task_id": task.id,
        "workflow_id": task.workflow_id,
        "workflow_name": wf.workflow_name,
        "overview": overview,
        "runs": runs_data.get("runs", []),
        "total_runs": runs_data.get("total", 0),
    }


@router.get("/release-management/sync/excel/{task_id}")
async def export_sync_excel(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """导出同步任务的采集记录为 Excel"""
    from io import BytesIO

    task = await db.get(ReleaseAnalysisTask, task_id)
    if not task:
        raise HTTPException(404, f"同步任务 {task_id} 不存在")
    if task.status == "pending":
        raise HTTPException(400, "任务还在等待中，无法导出")

    wf = await db.get(ReleaseWorkflow, task.workflow_id)
    wf_name = wf.workflow_name if wf else f"workflow_{task.workflow_id}"

    svc = ReleaseManagementService(db, _make_github())
    overview = await svc.get_overview(workflow_ids=[task.workflow_id], days_back=task.days_back or 30)

    runs_data = await svc.get_runs(workflow_ids=[task.workflow_id], page=1, page_size=500)
    runs = runs_data.get("runs", [])

    groups_stmt = (
        select(ReleaseBuildRun)
        .where(ReleaseBuildRun.workflow_id == task.workflow_id)
        .order_by(ReleaseBuildRun.created_at_remote.desc())
        .limit(100)
    )
    run_records = (await db.execute(groups_stmt)).scalars().all()

    from release_management.analyzer import ReleaseAnalyzer

    analyzer = ReleaseAnalyzer(db)
    all_groups = []
    for run_rec in run_records:
        detail = await analyzer.get_run_detail(run_rec.id)
        if detail:
            all_groups.extend(detail.get("groups", []))

    rg = ReportGenerator()
    excel_bytes = await rg.generate_excel(
        workflow_name=wf_name,
        stats=overview.get("stats", {}),
        runs=runs,
        groups=all_groups,
        evaluation=overview.get("evaluation"),
    )

    return StreamingResponse(
        BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=release_sync_{task_id}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.xlsx"},
    )


@router.post("/release-management/sync/pdf/{task_id}")
async def export_sync_pdf(
    db: DbSession,
    current_user: CurrentUser,
    task_id: int,
):
    """导出同步任务的采集记录为 PDF"""
    from io import BytesIO

    task = await db.get(ReleaseAnalysisTask, task_id)
    if not task:
        raise HTTPException(404, f"同步任务 {task_id} 不存在")
    if task.status == "pending":
        raise HTTPException(400, "任务还在等待中，无法导出")

    wf_name, overview, runs, all_groups = await _load_export_data(db, [task.workflow_id])

    rg = ReportGenerator()
    pdf_bytes, filename = await rg.generate_pdf(**_build_report_kwargs(wf_name, overview, runs, all_groups))

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/release-management/sync/tasks")
async def get_sync_tasks(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(None),
    status: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取同步任务列表"""
    from sqlalchemy import func as sa_func

    query = select(ReleaseAnalysisTask)
    count_query = select(sa_func.count())

    if workflow_id:
        query = query.where(ReleaseAnalysisTask.workflow_id == workflow_id)
        count_query = count_query.where(ReleaseAnalysisTask.workflow_id == workflow_id)
    if status:
        query = query.where(ReleaseAnalysisTask.status == status)
        count_query = count_query.where(ReleaseAnalysisTask.status == status)

    total_result = await db.execute(count_query.select_from(ReleaseAnalysisTask))
    total = total_result.scalar() or 0

    query = query.order_by(ReleaseAnalysisTask.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    tasks = result.scalars().all()

    wf_ids = list(set(t.workflow_id for t in tasks))
    wf_map = {}
    if wf_ids:
        wf_result = await db.execute(select(ReleaseWorkflow).where(ReleaseWorkflow.id.in_(wf_ids)))
        wf_map = {w.id: w.workflow_name for w in wf_result.scalars().all()}

    task_list = [_task_to_dict(t, wf_map.get(t.workflow_id)) for t in tasks]

    return {"tasks": task_list, "total": total}


@router.get("/release-management/sync/summary")
async def get_sync_summary(
    db: DbSession,
    current_user: CurrentUser,
):
    """获取同步统计摘要"""
    from sqlalchemy import func as sa_func

    total = (await db.execute(select(sa_func.count()).select_from(ReleaseAnalysisTask))).scalar() or 0
    completed = (await db.execute(select(sa_func.count()).select_from(ReleaseAnalysisTask).where(ReleaseAnalysisTask.status == "completed"))).scalar() or 0
    failed = (await db.execute(select(sa_func.count()).select_from(ReleaseAnalysisTask).where(ReleaseAnalysisTask.status == "failed"))).scalar() or 0
    running_stmt = select(sa_func.count()).where(ReleaseAnalysisTask.status.in_(["running", "pending"]))
    running = (await db.execute(running_stmt)).scalar() or 0

    last_task_result = await db.execute(
        select(ReleaseAnalysisTask).order_by(ReleaseAnalysisTask.created_at.desc()).limit(1)
    )
    last_task = last_task_result.scalar_one_or_none()

    total_collected_result = await db.execute(
        select(sa_func.coalesce(sa_func.sum(ReleaseAnalysisTask.collected_items), 0)).select_from(ReleaseAnalysisTask)
    )
    total_collected = total_collected_result.scalar() or 0

    return {
        "total_syncs": total,
        "completed_count": completed,
        "failed_count": failed,
        "running_count": running,
        "total_collected": total_collected,
        "last_sync_at": last_task.created_at.isoformat() if last_task else None,
        "last_sync_status": last_task.status if last_task else None,
    }


# ==================== Sync Schedule ====================

@router.get("/release-management/sync/schedules")
async def list_sync_schedules(db: DbSession, current_user: CurrentUser):
    """Get all sync schedules with workflow names"""
    result = await db.execute(select(ReleaseSyncSchedule))
    schedules = result.scalars().all()

    wf_map = {}
    if schedules:
        wf_result = await db.execute(
            select(ReleaseWorkflow).where(ReleaseWorkflow.id.in_([s.workflow_id for s in schedules]))
        )
        wf_map = {w.id: w.workflow_name for w in wf_result.scalars().all()}

    return [dict(
        id=s.id,
        workflow_id=s.workflow_id,
        workflow_name=wf_map.get(s.workflow_id, ''),
        sync_mode=s.sync_mode,
        recent_days=s.recent_days,
        cron_expression=s.cron_expression,
        is_active=s.is_active,
        last_triggered_at=s.last_triggered_at.isoformat() if s.last_triggered_at else None,
        created_at=s.created_at.isoformat() if s.created_at else None,
        updated_at=s.updated_at.isoformat() if s.updated_at else None,
    ) for s in schedules]


@router.post("/release-management/sync/schedules")
async def create_update_schedule(db: DbSession, current_user: CurrentUser, body: dict = Body(...)):
    """Create or update a schedule for a workflow"""
    wf_id = body.get("workflow_id")
    if not wf_id:
        raise HTTPException(status_code=400, detail="workflow_id is required")

    existing = (
        await db.execute(
            select(ReleaseSyncSchedule).where(ReleaseSyncSchedule.workflow_id == wf_id)
        )
    ).scalar_one_or_none()

    if existing:
        existing.sync_mode = body.get("sync_mode", existing.sync_mode)
        existing.recent_days = body.get("recent_days", existing.recent_days)
        existing.cron_expression = body.get("cron_expression", existing.cron_expression)
        existing.is_active = body.get("is_active", existing.is_active)
    else:
        existing = ReleaseSyncSchedule(
            workflow_id=wf_id,
            sync_mode=body.get("sync_mode", "incremental"),
            recent_days=body.get("recent_days", 30),
            cron_expression=body.get("cron_expression", "0 2 * * *"),
            is_active=body.get("is_active", False),
        )
        db.add(existing)

    await db.commit()
    await db.refresh(existing)
    return {"id": existing.id, "workflow_id": existing.workflow_id, "message": "ok"}


@router.delete("/release-management/sync/schedules/{schedule_id}")
async def delete_schedule(db: DbSession, current_user: CurrentUser, schedule_id: int):
    """Delete a schedule"""
    schedule = (
        await db.execute(
            select(ReleaseSyncSchedule).where(ReleaseSyncSchedule.id == schedule_id)
        )
    ).scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(schedule)
    await db.commit()
    return {"message": "deleted"}


# ==================== Overview & Runs ====================

@router.get("/release-management/overview")
async def get_overview(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None, description="逗号分隔的 workflow ID 列表"),
    days_back: int = Query(30, ge=1, le=365),
):
    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()] if workflow_ids else None
    svc = ReleaseManagementService(db, _make_github())
    return await svc.get_overview(workflow_ids=ids, days_back=days_back)


@router.get("/release-management/runs")
async def get_runs(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None),
    status: str = Query(None),
    branch: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()] if workflow_ids else None
    svc = ReleaseManagementService(db, _make_github())
    return await svc.get_runs(
        workflow_ids=ids, status=status, branch=branch,
        page=page, page_size=page_size,
    )


@router.get("/release-management/runs/{run_id}")
async def get_run_detail(
    db: DbSession,
    current_user: CurrentUser,
    run_id: int,
):
    from release_management.analyzer import ReleaseAnalyzer
    analyzer = ReleaseAnalyzer(db)
    return await analyzer.get_run_detail(run_id)


@router.get("/release-management/comparison")
async def get_comparison(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(..., description="逗号分隔的 workflow ID 列表，至少 2 个"),
    days_back: int = Query(30, ge=1, le=365),
):
    """多工作流对比分析"""
    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()]
    if len(ids) < 2:
        raise HTTPException(400, "至少需要 2 个工作流进行对比")

    from release_management.analyzer import ReleaseAnalyzer

    analyzer = ReleaseAnalyzer(db)
    wf_map = {}
    wf_result = await db.execute(select(ReleaseWorkflow).where(ReleaseWorkflow.id.in_(ids)))
    wf_map = {w.id: w.workflow_name for w in wf_result.scalars().all()}

    comparisons = []
    for wf_id in ids:
        overview = await analyzer.get_overview([wf_id], days_back=days_back)
        comparisons.append({
            "workflow_id": wf_id,
            "workflow_name": wf_map.get(wf_id, f"#{wf_id}"),
            "stats": overview["stats"],
        })

    return {"comparisons": comparisons, "days_back": days_back}


# ==================== Trends ====================

@router.get("/release-management/trends/duration")
async def get_duration_trend(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None),
    days_back: int = Query(30, ge=1, le=365),
    aggregate: str = Query("day", pattern="^(day|week)$"),
):
    from datetime import timedelta

    stmt = (
        select(ReleaseBuildRun)
        .where(ReleaseBuildRun.created_at_remote >= datetime.now(UTC) - timedelta(days=days_back))
        .order_by(ReleaseBuildRun.created_at_remote)
    )
    if workflow_ids:
        ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()]
        stmt = stmt.where(ReleaseBuildRun.workflow_id.in_(ids))

    result = await db.execute(stmt)
    runs = result.scalars().all()

    if aggregate == "day":
        key_func = lambda r: r.created_at_remote.strftime("%Y-%m-%d") if r.created_at_remote else ""
    else:
        key_func = lambda r: r.created_at_remote.strftime("%Y-W%V") if r.created_at_remote else ""

    grouped: dict[str, list[float]] = {}
    status_map: dict[str, list[str]] = {}
    for r in runs:
        k = key_func(r)
        if k not in grouped:
            grouped[k] = []
            status_map[k] = []
        if r.exec_duration > 0:
            grouped[k].append(r.exec_duration)
        status_map[k].append(r.status)

    trend = []
    for k in sorted(grouped.keys()):
        durs = grouped[k]
        statuses = status_map[k]
        success_ct = sum(1 for s in statuses if s == "success")
        trend.append({
            "period": k,
            "count": len(durs),
            "success_count": success_ct,
            "success_rate": round(success_ct / max(len(statuses), 1) * 100, 1),
            "avg_duration": round(sum(durs) / len(durs), 2) if durs else 0,
            "min_duration": round(min(durs), 2) if durs else 0,
            "max_duration": round(max(durs), 2) if durs else 0,
        })

    return trend


@router.get("/release-management/trends/phases")
@router.get("/release-management/trends/steps")
@router.get("/release-management/trends/architecture")
async def get_trend_placeholder(
    current_user: CurrentUser,
):
    return {"message": "接口已注册，数据从 /release-management/overview 中获取"}


# ==================== Third Party ====================

@router.get("/release-management/third-party")
async def get_third_party_overview(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None),
):
    tp = ThirdPartyService(db)
    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()] if workflow_ids else None
    return await tp.get_components_overview(ids)


@router.get("/release-management/third-party/components")
async def get_components_list(
    db: DbSession,
    current_user: CurrentUser,
):
    from sqlalchemy import distinct
    result = await db.execute(select(distinct(ReleaseBuildRun.branch)))
    return sorted([r[0] for r in result.all() if r[0]])


@router.get("/release-management/third-party/version-history")
async def get_version_history(
    db: DbSession,
    current_user: CurrentUser,
    component_name: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
):
    tp = ThirdPartyService(db)
    return await tp.get_version_history(component_name, limit=limit)


@router.get("/release-management/third-party/baselines")
async def list_baselines(
    db: DbSession,
    current_user: CurrentUser,
):
    tp = ThirdPartyService(db)
    return await tp.list_baselines()


@router.post("/release-management/third-party/baselines")
async def create_or_update_baseline(
    db: DbSession,
    current_user: CurrentUser,
    data: dict,
):
    tp = ThirdPartyService(db)
    return await tp.create_or_update_baseline(data)


@router.delete("/release-management/third-party/baselines/{baseline_id}")
async def delete_baseline(
    db: DbSession,
    current_user: CurrentUser,
    baseline_id: int,
):
    tp = ThirdPartyService(db)
    return await tp.delete_baseline(baseline_id)


@router.get("/release-management/third-party/compliance")
async def get_compliance(
    db: DbSession,
    current_user: CurrentUser,
    run_id: int = Query(...),
):
    tp = ThirdPartyService(db)
    return await tp.get_compliance_check(run_id)


# ==================== AI Evaluation ====================

@router.post("/release-management/evaluate")
async def trigger_evaluation(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(...),
    evaluation_type: str = Query("trustworthiness", pattern="^(trustworthiness|production_ready|trend|release_readiness)$"),
    period_start: str = Query(None),
    period_end: str = Query(None),
    model_name: str = Query(None),
):
    evaluator = TrustworthinessEvaluator(db)
    task_id, evaluation, task = await evaluator.evaluate(
        workflow_id=workflow_id,
        evaluation_type=evaluation_type,
        period_start=period_start,
        period_end=period_end,
        model_name=model_name,
        generated_by=current_user.username,
    )
    return {
        "task_id": task_id,
        "evaluation_id": evaluation.id,
        "trust_score": evaluation.trust_score,
        "trust_level": evaluation.trust_level,
        "production_ready": evaluation.production_ready,
    }


@router.get("/release-management/evaluations")
async def list_evaluations(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    conditions = []
    if workflow_id:
        conditions.append(ReleaseAIEvaluation.workflow_id == workflow_id)

    count_stmt = select(func.count(ReleaseAIEvaluation.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = select(ReleaseAIEvaluation)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(ReleaseAIEvaluation.generated_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)

    result = await db.execute(stmt)
    items = result.scalars().all()

    return {
        "evaluations": [
            {
                "id": e.id,
                "workflow_id": e.workflow_id,
                "evaluation_type": e.evaluation_type,
                "period_start": str(e.period_start) if e.period_start else None,
                "period_end": str(e.period_end) if e.period_end else None,
                "run_count": e.run_count,
                "trust_score": e.trust_score,
                "trust_level": e.trust_level,
                "production_ready": e.production_ready,
                "llm_model": e.llm_model,
                "generated_by": e.generated_by,
                "generated_at": e.generated_at.isoformat() if e.generated_at else None,
            }
            for e in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/release-management/evaluations/latest")
async def get_latest_evaluation(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(None),
):
    stmt = select(ReleaseAIEvaluation).order_by(ReleaseAIEvaluation.generated_at.desc()).limit(1)
    if workflow_id:
        stmt = stmt.where(ReleaseAIEvaluation.workflow_id == workflow_id)
    result = await db.execute(stmt)
    e = result.scalar_one_or_none()
    if not e:
        return None
    return await _build_evaluation_detail(e)


@router.get("/release-management/evaluations/{evaluation_id}")
async def get_evaluation_detail(
    db: DbSession,
    current_user: CurrentUser,
    evaluation_id: int,
):
    e = await db.get(ReleaseAIEvaluation, evaluation_id)
    if not e:
        raise HTTPException(404, "评估报告不存在")
    return await _build_evaluation_detail(e)


async def _build_evaluation_detail(e) -> dict:
    import json
    return {
        "id": e.id,
        "workflow_id": e.workflow_id,
        "evaluation_type": e.evaluation_type,
        "period_start": str(e.period_start) if e.period_start else None,
        "period_end": str(e.period_end) if e.period_end else None,
        "run_count": e.run_count,
        "trust_score": e.trust_score,
        "trust_level": e.trust_level,
        "production_ready": e.production_ready,
        "llm_model": e.llm_model,
        "executive_summary": e.executive_summary,
        "findings": json.loads(e.findings) if e.findings else [],
        "recommendations": json.loads(e.recommendations) if e.recommendations else [],
        "risk_factors": json.loads(e.risk_factors) if e.risk_factors else [],
        "data_quality_issues": json.loads(e.data_quality_issues) if e.data_quality_issues else [],
        "report_content": e.report_content,
        "generated_by": e.generated_by,
        "generated_at": e.generated_at.isoformat() if e.generated_at else None,
    }


# ==================== Export ====================


async def _load_export_data(
    db: DbSession, workflow_ids: list[int], limit: int = 100
) -> tuple[str, dict, list[dict], list[dict]]:
    """Load shared data for PDF/Excel export: (workflow_name, overview, runs, groups)"""
    from release_management.analyzer import ReleaseAnalyzer

    svc = ReleaseManagementService(db, _make_github())
    overview = await svc.get_overview(workflow_ids=workflow_ids, days_back=30)

    runs_data = await svc.get_runs(workflow_ids=workflow_ids, page=1, page_size=500)
    runs = runs_data.get("runs", [])

    wf_result = await db.execute(
        select(ReleaseWorkflow.workflow_name)
        .where(ReleaseWorkflow.id.in_(workflow_ids))
        .limit(1)
    )
    wf_name = wf_result.scalar_one_or_none() or "构建分析"

    run_records = (await db.execute(
        select(ReleaseBuildRun)
        .where(ReleaseBuildRun.workflow_id.in_(workflow_ids))
        .order_by(ReleaseBuildRun.created_at_remote.desc())
        .limit(limit)
    )).scalars().all()

    analyzer = ReleaseAnalyzer(db)
    all_groups = []
    for rec in run_records:
        detail = await analyzer.get_run_detail(rec.id)
        if detail:
            all_groups.extend(detail.get("groups", []))

    return wf_name, overview, runs, all_groups


def _build_report_kwargs(
    workflow_name: str, overview: dict, runs: list[dict], groups: list[dict]
) -> dict:
    """Build keyword dict for generate_pdf / generate_excel calls."""
    return {
        "workflow_name": workflow_name,
        "stats": overview.get("stats", {}),
        "runs": runs,
        "groups": groups,
        "failed_runs": [r for r in runs if r.get("status") == "failure"],
        "evaluation": overview.get("evaluation"),
        "timeline": overview.get("timeline", []),
        "phase_data": overview.get("phase_data", []),
        "target_data": overview.get("target_data", []),
        "arch_data": overview.get("arch_data", []),
        "step_data": overview.get("step_data", []),
        "run_breakdown": overview.get("run_breakdown", []),
    }


@router.post("/release-management/export/excel")
async def export_excel(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None),
    days_back: int = Query(30, ge=1, le=365),
):
    from io import BytesIO

    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()] if workflow_ids else None
    ids = ids or [r[0] for r in (await db.execute(
        select(ReleaseWorkflow.id).where(ReleaseWorkflow.enabled)
    )).all()]

    wf_name, overview, runs, all_groups = await _load_export_data(db, ids)

    rg = ReportGenerator()
    excel_bytes = await rg.generate_excel(**_build_report_kwargs(wf_name, overview, runs, all_groups))

    return StreamingResponse(
        BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=release_build_analysis_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.xlsx"},
    )


@router.post("/release-management/export/pdf")
async def export_pdf(
    db: DbSession,
    current_user: CurrentUser,
    days_back: int = Query(30, ge=1, le=365),
    workflow_ids: str | None = Query(None),
):
    """导出 PDF 分析报告"""
    from io import BytesIO

    ids = [int(x.strip()) for x in workflow_ids.split(",") if x.strip()] if workflow_ids else None
    ids = ids or [r[0] for r in (await db.execute(
        select(ReleaseWorkflow.id).where(ReleaseWorkflow.enabled)
    )).all()]

    wf_name, overview, runs, all_groups = await _load_export_data(db, ids)

    rg = ReportGenerator()
    pdf_bytes, filename = await rg.generate_pdf(**_build_report_kwargs(wf_name, overview, runs, all_groups))

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
