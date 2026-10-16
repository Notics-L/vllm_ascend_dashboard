"""评分权重版本管理 API"""
from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import CurrentSuperAdminUser, DbSession
from contracts.schemas.contributor_board import ScoringWeightsConfig
from contributor_board.auto_weight_engine import compute_weights
from contributor_board.scoring_version_schemas import (
    CalculateAutoWeightsResponse,
    ScoreSnapshotResponse,
    ScoreUpdateAuditResponse,
    ScoringVersionCreateRequest,
    ScoringVersionResponse,
    VersionCompareResponse,
)
from contributor_board.scoring_version_service import ScoringVersionService

router = APIRouter(tags=["ContributorBoard/Scoring"])


def _get_service() -> ScoringVersionService:
    return ScoringVersionService()


def _get_user(access: CurrentSuperAdminUser) -> str:
    return access.username


@router.get("/versions/active", response_model=ScoringVersionResponse | None)
async def get_active_version(db: DbSession, repo_name: str | None = Query(None)):
    return await _get_service().get_active_version(db, repo_name)


@router.get("/versions", response_model=Any)
async def list_versions(
    db: DbSession,
    repo_name: str | None = Query(None),
    status: str | None = Query(None, description="draft/active/archived"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return await _get_service().list_versions(db, repo_name, status, skip, limit)


@router.get("/versions/{version_id}", response_model=ScoringVersionResponse)
async def get_version(db: DbSession, version_id: int):
    v = await _get_service().get_version(db, version_id)
    if not v:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Version not found")
    return v


@router.get("/versions/{version_id}/compare/{other_id}", response_model=VersionCompareResponse)
async def compare_versions(db: DbSession, version_id: int, other_id: int):
    return await _get_service().compare_versions(db, version_id, other_id)


@router.post("/versions/create", response_model=ScoringVersionResponse)
async def create_version(
    db: DbSession,
    request: ScoringVersionCreateRequest,
    _access: CurrentSuperAdminUser,
    user: str = Depends(_get_user),
):
    return await _get_service().create_version(db, request, created_by=user)


@router.post("/versions/{version_id}/apply", response_model=ScoringVersionResponse)
async def apply_version(
    db: DbSession,
    version_id: int,
    _access: CurrentSuperAdminUser,
    user: str = Depends(_get_user),
):
    return await _get_service().apply_version(db, version_id, created_by=user)


@router.post("/versions/{version_id}/rollback", response_model=ScoringVersionResponse)
async def rollback_to_version(
    db: DbSession,
    version_id: int,
    _access: CurrentSuperAdminUser,
    user: str = Depends(_get_user),
):
    return await _get_service().rollback_to_version(db, version_id, created_by=user)


@router.post("/weights/calculate-auto", response_model=CalculateAutoWeightsResponse)
async def calculate_auto_weights(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    user: str = Depends(_get_user),
):
    """触发自动算权 — 创建新版本并自动生效"""
    from sqlalchemy import select as sa_select
    from infrastructure.persistence.models import ContributorMetrics as CM

    result = await db.execute(sa_select(CM))
    metrics_list = result.scalars().all()

    if not metrics_list:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No contributor metrics data available")

    metrics_data = []
    for m in metrics_list:
        metrics_data.append({
            "merged_pr_count": m.merged_pr_count or 0,
            "code_additions": m.code_additions or 0,
            "review_count": m.review_count or 0,
            "approval_count": m.approval_count or 0,
            "core_module_count": m.core_module_count or 0,
            "files_changed_count": getattr(m, 'files_changed_count', None) or m.commit_count or 0,
            "commit_count": m.commit_count or 0,
        })

    weights, confidence, data_summary = compute_weights(metrics_data)

    svc = _get_service()
    from contributor_board.scoring_version_schemas import ScoringVersionCreateRequest
    request = ScoringVersionCreateRequest(
        method="auto",
        weights=weights,
        data_summary=data_summary,
        confidence_score=confidence,
        description=f"熵权法自动算权，置信度: {confidence:.4f}",
    )
    version = await svc.create_version(db, request, created_by=user)
    # 自动 apply 新版本（归档旧 active 版本）
    version = await svc.apply_version(db, version.id, created_by=user)
    # 自动生成分数快照
    await svc.generate_snapshot(db, version.id, created_by=user)

    return CalculateAutoWeightsResponse(
        weights=weights,
        confidence=confidence,
        data_summary=data_summary,
        version_id=version.id,
    )


@router.post("/weights/ai-analyze", response_model=ScoringVersionResponse)
async def ai_analyze_weights(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    user: str = Depends(_get_user),
    model: str | None = Query(None, description="指定 LLM 模型名"),
):
    """AI 辅助权重分析 — 调用 LLM 给出权重建议并创建新版本"""
    from sqlalchemy import select as sa_select
    from infrastructure.persistence.models import ContributorMetrics as CM
    from contributor_board.auto_weight_engine import compute_weights
    from contributor_board.ai_weight_engine import analyze_weights_with_ai

    result = await db.execute(sa_select(CM))
    metrics_list = result.scalars().all()

    if not metrics_list:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="No contributor metrics data available")

    metrics_data = []
    for m in metrics_list:
        metrics_data.append({
            "merged_pr_count": m.merged_pr_count or 0,
            "code_additions": m.code_additions or 0,
            "review_count": m.review_count or 0,
            "approval_count": m.approval_count or 0,
            "core_module_count": m.core_module_count or 0,
            "files_changed_count": getattr(m, 'files_changed_count', None) or m.commit_count or 0,
            "commit_count": m.commit_count or 0,
        })

    _, _, data_summary = compute_weights(metrics_data)

    ai_weights, analysis, used_model = await analyze_weights_with_ai(db, data_summary, model)

    svc = _get_service()
    from contributor_board.scoring_version_schemas import ScoringVersionCreateRequest
    request = ScoringVersionCreateRequest(
        method="ai",
        weights=ai_weights,
        data_summary=data_summary,
        ai_prompt="AI 权重分析",
        ai_response=analysis,
        ai_model=used_model,
        description=f"AI 辅助评权 (模型: {used_model})",
    )
    version = await svc.create_version(db, request, created_by=user)
    version = await svc.apply_version(db, version.id, created_by=user)

    return ScoringVersionResponse.model_validate(version)


@router.get("/export/excel")
async def export_excel(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    format_type: str = Query("excel", description="export format: excel"),
    snapshot_id: int | None = Query(None, description="export specific snapshot, else latest"),
):
    from fastapi.responses import StreamingResponse
    from contributor_board.report_exporter import export_excel as do_export_excel

    svc = _get_service()
    if snapshot_id:
        snap = await svc.get_snapshot(db, snapshot_id)
    else:
        result = await svc.list_snapshots(db, None, 0, 1)
        snap = result["snapshots"][0] if result["snapshots"] else None

    if not snap:
        raise HTTPException(status_code=404, detail="No score snapshots found")

    active = await svc.get_active_version(db)
    data, filename = await do_export_excel(db, snap.scores, snap.version_id)

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/pdf")
async def export_pdf(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    snapshot_id: int | None = Query(None, description="export specific snapshot, else latest"),
):
    from fastapi.responses import StreamingResponse
    from contributor_board.report_exporter import export_pdf as do_export_pdf

    svc = _get_service()
    if snapshot_id:
        snap = await svc.get_snapshot(db, snapshot_id)
    else:
        result = await svc.list_snapshots(db, None, 0, 1)
        snap = result["snapshots"][0] if result["snapshots"] else None

    if not snap:
        raise HTTPException(status_code=404, detail="No score snapshots found")

    active = await svc.get_active_version(db)
    data, filename = await do_export_pdf(
        db, snap.scores,
        version_info=active.model_dump() if active else {},
        statistics=snap.statistics,
    )

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/snapshots", response_model=Any)
async def list_snapshots(
    db: DbSession,
    repo_name: str | None = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
):
    return await _get_service().list_snapshots(db, repo_name, skip, limit)


@router.get("/snapshots/{snapshot_id}", response_model=ScoreSnapshotResponse)
async def get_snapshot(db: DbSession, snapshot_id: int):
    s = await _get_service().get_snapshot(db, snapshot_id)
    if not s:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return s


@router.get("/audit", response_model=Any)
async def list_audit_logs(
    db: DbSession,
    repo_name: str | None = Query(None),
    skip: int = Query(0),
    limit: int = Query(100),
):
    return await _get_service().list_audit_logs(db, repo_name, skip, limit)


@router.post("/report/ai-analyze")
async def ai_analyze_report(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
):
    """AI 综合分析报表 — 基于多周期快照生成决策报告"""
    from contributor_board.ai_report_engine import (
        build_report_context,
        generate_ai_report,
    )

    context = await build_report_context(db, repo_name=repo_name)
    snapshot_data = context.get("snapshots", [])

    if not snapshot_data:
        raise HTTPException(status_code=400, detail="暂无快照数据，请先执行分数重算")

    report, model_name = await generate_ai_report(db, snapshot_data, context)

    return {
        **report,
        "ai_model": model_name,
        "periods_count": len(context.get("periods", [])),
        "total_contributors": context.get("summary", {}).get("total_contributors", 0),
        "generated_at": datetime.now(UTC).isoformat(),
    }
