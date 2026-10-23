"""
发布件管理 API 路由
"""
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
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
    ReleaseWorkflow,
)
from release_management.ai_evaluator import TrustworthinessEvaluator
from release_management.report_generator import ReportGenerator
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
    return GitHubClient(
        token=settings.GITHUB_TOKEN or "",
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
    svc = ReleaseManagementService(db, GitHubClient(
        token=settings.GITHUB_TOKEN,
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

@router.post("/release-management/sync")
async def trigger_sync(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(...),
    days_back: int = Query(30, ge=1, le=365),
    max_runs: int = Query(100, ge=1, le=500),
):
    svc = ReleaseManagementService(db, _make_github())
    return await svc.trigger_sync(workflow_id, days_back=days_back, max_runs=max_runs)


@router.get("/release-management/sync/status")
async def get_sync_status(
    db: DbSession,
    current_user: CurrentUser,
    workflow_id: int = Query(...),
    limit: int = Query(5, ge=1, le=20),
):
    stmt = (
        select(ReleaseAnalysisTask)
        .where(ReleaseAnalysisTask.workflow_id == workflow_id)
        .order_by(ReleaseAnalysisTask.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    return [
        {
            "id": t.id,
            "task_type": t.task_type,
            "status": t.status,
            "progress": t.progress,
            "total_items": t.total_items,
            "processed_items": t.processed_items,
            "error_message": t.error_message,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


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


# ==================== Trends ====================

@router.get("/release-management/trends/duration")
async def get_duration_trend(
    db: DbSession,
    current_user: CurrentUser,
    workflow_ids: str = Query(None),
    days_back: int = Query(30, ge=1, le=365),
    aggregate: str = Query("day", regex="^(day|week)$"),
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
    evaluation_type: str = Query("trustworthiness", regex="^(trustworthiness|production_ready|trend)$"),
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

    svc = ReleaseManagementService(db, _make_github())
    overview = await svc.get_overview(workflow_ids=ids, days_back=days_back)

    runs_data = await svc.get_runs(workflow_ids=ids, page=1, page_size=500)
    runs = runs_data.get("runs", [])

    wf_name = "构建分析"
    wf_result = await db.execute(
        select(ReleaseWorkflow.workflow_name).where(
            ReleaseWorkflow.id.in_(ids)
        ).limit(1)
    )
    wf_row = wf_result.scalar_one_or_none()
    if wf_row:
        wf_name = wf_row

    groups_stmt = (
        select(ReleaseBuildRun)
        .where(ReleaseBuildRun.workflow_id.in_(ids))
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

    svc = ReleaseManagementService(db, _make_github())
    overview = await svc.get_overview(workflow_ids=ids, days_back=days_back)

    runs_data = await svc.get_runs(workflow_ids=ids, page=1, page_size=500)
    runs = runs_data.get("runs", [])

    wf_name = "vLLM Ascend 发布件构建"
    if ids:
        wf_result = await db.execute(
            select(ReleaseWorkflow.last_remote_name)
            .where(ReleaseWorkflow.id == ids[0])
        )
        wf_row = wf_result.scalar_one_or_none()
        if wf_row:
            wf_name = wf_row

    groups_stmt = (
        select(ReleaseBuildRun)
        .where(ReleaseBuildRun.workflow_id.in_(ids))
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
    pdf_bytes, filename = await rg.generate_pdf(
        workflow_name=wf_name,
        stats=overview.get("stats", {}),
        runs=runs,
        groups=all_groups,
        evaluation=overview.get("evaluation"),
    )

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
