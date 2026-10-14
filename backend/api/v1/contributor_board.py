"""
贡献者看板 API 路由
"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel


class BuildPlanFilterOptions(BaseModel):
    commit_types: list[str]
    build_types: list[str]

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from contracts.schemas.contributor_board import (
    BuildOptimizationStats,
    BuildPlanEvaluateRequest,
    BuildPlanListResponse,
    BuildPlanResponse,
    ClassificationSummary,
    ContributorDetailResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingResponse,
    ContributorTrendResponse,
    ContributorSyncRequest,
    ContributorSyncResponse,
    ExtendedTrendResponse,
    ModuleHotspotResponse,
    PolicyResultResponse,
    QuadrantDataResponse,
    RoleStats,
    ContributorBoardSetting,
    ContributorBoardSettingsResponse,
    UpdateQuadrantConfigRequest,
    UpdateScoringWeightsRequest,
    UpdateRoleThresholdsRequest,
)
from contributor_board.service import get_contributor_board_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/commits/classifications")
async def get_classification_stats(
    db: DbSession,
):
    """获取提交分类统计"""
    service = get_contributor_board_service()
    summary = await service.get_classification_summary_from_db(db)
    return summary


@router.get("/builds/plans", response_model=BuildPlanListResponse)
async def get_build_plans(
    db: DbSession,
    build_type: str | None = Query(None),
    commit_type: str | None = Query(None),
    created_after: date | None = Query(None),
    created_before: date | None = Query(None),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取构建计划列表"""
    service = get_contributor_board_service()
    return await service.get_build_plans_from_db(
        db,
        build_type=build_type,
        commit_type=commit_type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/builds/filter_options")
async def get_build_filter_options(db: DbSession):
    """获取构建计划筛选选项（去重后的 commit_type 和 build_type 列表）"""
    service = get_contributor_board_service()
    return await service.get_build_plan_filter_options(db)


@router.get("/builds/stats", response_model=BuildOptimizationStats)
async def get_build_optimization_stats(
    db: DbSession,
):
    """获取构建优化统计"""
    service = get_contributor_board_service()
    return await service.get_build_optimization_stats_from_db(db)


@router.post("/builds/evaluate", response_model=BuildPlanResponse)
async def evaluate_build(
    request: BuildPlanEvaluateRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    """对新 PR 评估构建策略"""
    service = get_contributor_board_service()
    result = service.classify_commit(
        message=request.commit_message or "",
        files_changed=request.files_changed,
    )

    plan = service.evaluate_build_plan(
        pr_number=request.pr_number,
        commit_sha="",
        commit_type=result.commit_type,
        is_chip_specific=result.is_chip_specific,
        chip_target=result.chip_target,
        files_changed=request.files_changed,
    )

    return BuildPlanResponse(
        id=None,
        pr_number=plan.pr_number,
        commit_sha=plan.commit_sha,
        commit_type=plan.commit_type,
        build_type=plan.build_type,
        chips=plan.chips,
        os_list=plan.os_list,
        cache_strategy=plan.cache_strategy,
        estimated_saving_percent=plan.estimated_saving_percent,
        actual_execution=False,
        reasoning=plan.reasoning,
        created_at=None,
    )


@router.get("/stats/overview", response_model=ContributorOverviewStats)
async def get_overview_stats(
    db: DbSession,
):
    """获取总体统计数据"""
    service = get_contributor_board_service()
    return await service.get_overview_stats(db)


# ---- 贡献者列表 ----

@router.get("/contributors", response_model=ContributorListResponse)
async def list_contributors(
    db: DbSession,
    role_type: str | None = Query(None, description="角色过滤"),
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
    search: str | None = Query(None, description="搜索用户名"),
    quadrant: str | None = Query(None, description="象限过滤"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """获取贡献者列表（登录即可访问）"""
    service = get_contributor_board_service()
    filter_obj = ContributorListFilter(
        role_type=role_type,
        period_start=period_start,
        period_end=period_end,
        search=search,
        quadrant=quadrant,
        limit=limit,
        offset=offset,
    )
    return await service.get_contributors(db, filter=filter_obj)


@router.get("/contributors/{login}", response_model=ContributorDetailResponse)
async def get_contributor(
    login: str,
    db: DbSession,
):
    """获取贡献者详情"""
    service = get_contributor_board_service()
    detail = await service.get_contributor_detail(db, login=login)
    if detail is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Contributor {login} not found")
    return detail


# ---- 四象限数据（管理员可见）----

@router.get("/quadrant", response_model=QuadrantDataResponse)
async def get_quadrant_data(
    db: DbSession,
    _admin: CurrentAdminUser,
    repo_name: str | None = Query(None),
    y_axis_type: str | None = Query(None, description="Y轴指标(不传则读取设置)"),
):
    """获取四象限数据（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_quadrant_data(
        db, repo_name=repo_name, y_axis_type=y_axis_type,
    )


# ---- 排行榜（管理员可见）----

@router.get("/ranking", response_model=ContributorRankingResponse)
async def get_ranking(
    db: DbSession,
    _admin: CurrentAdminUser,
    role_type: str = Query("developer", description="角色类型: developer/reviewer/maintainer"),
    repo_name: str | None = Query(None, description="仓库名过滤"),
    sort_by: str | None = Query(None, description="排序字段"),
    sort_order: str = Query("desc", description="排序方向: asc/desc"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(20, ge=1, le=200, description="分页大小"),
):
    """获取排行榜（全量统计，管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_ranking(
        db, role_type=role_type, repo_name=repo_name,
        sort_by=sort_by, sort_order=sort_order, offset=offset, limit=limit,
    )


# ---- 趋势数据 ----

@router.get("/contributors/{login}/trend", response_model=ContributorTrendResponse)
async def get_contributor_trend(
    login: str,
    db: DbSession,
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
):
    """获取贡献者趋势数据"""
    service = get_contributor_board_service()
    return await service.get_contributor_trend(
        db, login=login, period_start=period_start, period_end=period_end
    )


# ---- 手动触发采集（管理员可见）----

@router.post("/sync/contributors", response_model=ContributorSyncResponse)
async def sync_contributors(
    request: ContributorSyncRequest,
    _admin: CurrentAdminUser,
):
    """手动触发贡献者数据采集（管理员可见）"""
    service = get_contributor_board_service()
    return await service.trigger_sync(
        since_date=request.since_date,
        full_resync=request.full_resync,
    )


# ---- 角色统计（管理员可见）----

@router.get("/stats/roles", response_model=RoleStats)
async def get_role_stats(
    db: DbSession,
    _admin: CurrentAdminUser,
    repo_name: str | None = Query(None),
):
    """获取角色维度统计（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_role_stats(db, repo_name=repo_name)


# ---- 模块热点（管理员可见）----

@router.get("/stats/module_hotspot", response_model=ModuleHotspotResponse)
async def get_module_hotspot(
    db: DbSession,
    _admin: CurrentAdminUser,
    repo_name: str | None = Query(None),
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
):
    """获取模块热点统计（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_module_hotspot(db, repo_name=repo_name, period_start=period_start, period_end=period_end)


# ---- 角色排行榜（管理员可见）----

@router.get("/ranking/roles/{role_type}", response_model=ContributorRankingResponse)
async def get_role_ranking(
    role_type: str,
    db: DbSession,
    _admin: CurrentAdminUser,
    repo_name: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
):
    """获取指定角色的排行榜（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_role_ranking(db, role_type=role_type, repo_name=repo_name, limit=limit)


# ---- 策略建议（登录即可访问）----

@router.get("/contributors/{login}/policy", response_model=PolicyResultResponse)
async def get_contributor_policy(
    login: str,
    db: DbSession,
):
    """获取贡献者的策略建议"""
    service = get_contributor_board_service()
    result = await service.get_contributor_policy(db, login=login)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Contributor {login} not found")
    return result


# ---- 仓库列表（管理员可见）----

@router.get("/repos/list")
async def get_repo_list(
    db: DbSession,
):
    """获取可选仓库列表"""
    service = get_contributor_board_service()
    repos = await service.get_repo_list(db)
    return {"repos": repos}


# ---- 扩展趋势数据（登录即可访问）----

@router.get("/contributors/{login}/trend/extended", response_model=ExtendedTrendResponse)
async def get_extended_trend(
    login: str,
    db: DbSession,
    period_start: date | None = Query(None),
    period_end: date | None = Query(None),
):
    """获取贡献者扩展趋势数据（提交量/Review/分数）"""
    service = get_contributor_board_service()
    return await service.get_extended_trend(
        db, login=login, period_start=period_start, period_end=period_end
    )


# ---- 设置管理（管理员可见）----

@router.get("/settings", response_model=ContributorBoardSettingsResponse)
async def get_settings(db: DbSession, _admin: CurrentAdminUser):
    """获取贡献者分析设置"""
    service = get_contributor_board_service()
    return await service.get_settings(db)


@router.put("/settings/quadrant", response_model=ContributorBoardSetting)
async def update_quadrant_config(db: DbSession, _admin: CurrentAdminUser, request: UpdateQuadrantConfigRequest):
    """更新四象限Y轴配置"""
    service = get_contributor_board_service()
    return await service.update_quadrant_config(db, request)


@router.put("/settings/scoring_weights", response_model=ContributorBoardSetting)
async def update_scoring_weights(db: DbSession, _admin: CurrentAdminUser, request: UpdateScoringWeightsRequest):
    """更新分数计算权重"""
    service = get_contributor_board_service()
    return await service.update_scoring_weights(db, request)


@router.put("/settings/role_thresholds", response_model=ContributorBoardSetting)
async def update_role_thresholds(db: DbSession, _admin: CurrentAdminUser, request: UpdateRoleThresholdsRequest):
    """更新角色阈值"""
    service = get_contributor_board_service()
    return await service.update_role_thresholds(db, request)
