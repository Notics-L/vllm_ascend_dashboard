"""
贡献者看板 API 路由
"""
import io
import logging
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel


class BuildPlanFilterOptions(BaseModel):
    commit_types: list[str]
    build_types: list[str]

from api.deps import CurrentSuperAdminUser, DbSession
from infrastructure.persistence.models import ContributorSyncRecord
from contracts.schemas.contributor_board import (
    BuildOptimizationStats,
    BuildPlanEvaluateRequest,
    BuildPlanListResponse,
    BuildPlanResponse,
    ClassificationSummary,
    ContributorDetailResponse,
    ContributorDistributionResponse,
    ContributorHeatmapResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingResponse,
    ContributorTimelineResponse,
    ContributorTrendResponse,
    ContributorSyncHistoryResponse,
    ContributorSyncRecordResponse,
    ContributorSyncRequest,
    ContributorSyncResponse,
    ContributorSyncSummary,
    ContributorSyncScheduleConfig,
    ContributorSyncScheduleUpdateRequest,
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
    repo_name: str | None = Query(None),
):
    """获取提交分类统计"""
    service = get_contributor_board_service()
    summary = await service.get_classification_summary_from_db(db, repo_name=repo_name)
    return summary


@router.get("/builds/plans", response_model=BuildPlanListResponse)
async def get_build_plans(
    db: DbSession,
    repo_name: str | None = Query(None),
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
        repo_name=repo_name,
        build_type=build_type,
        commit_type=commit_type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )


@router.get("/builds/filter_options")
async def get_build_filter_options(
    db: DbSession,
    repo_name: str | None = Query(None),
):
    """获取构建计划筛选选项（去重后的 commit_type 和 build_type 列表）"""
    service = get_contributor_board_service()
    return await service.get_build_plan_filter_options(db, repo_name=repo_name)


@router.get("/builds/stats", response_model=BuildOptimizationStats)
async def get_build_optimization_stats(
    db: DbSession,
    repo_name: str | None = Query(None),
):
    """获取构建优化统计"""
    service = get_contributor_board_service()
    return await service.get_build_optimization_stats_from_db(db, repo_name=repo_name)


@router.post("/builds/evaluate", response_model=BuildPlanResponse)
async def evaluate_build(
    request: BuildPlanEvaluateRequest,
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
    repo_name: str | None = Query(None, description="仓库名过滤"),
    days: int = Query(30, ge=1, le=365, description="统计时间窗（天），默认 30 与 PR 流水线一致"),
):
    """获取总体统计数据（与 PR 流水线贡献者 Tab 对齐）"""
    service = get_contributor_board_service()
    return await service.get_overview_stats(db, repo_name=repo_name, days=days)


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
    days: int = Query(30, ge=1, le=365, description="统计时间窗（天），默认 30 与排行榜一致"),
):
    """获取贡献者详情"""
    service = get_contributor_board_service()
    detail = await service.get_contributor_detail(db, login=login, days=days)
    if detail is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Contributor {login} not found")
    return detail


# ---- 四象限数据（管理员可见）----

@router.get("/quadrant", response_model=QuadrantDataResponse)
async def get_quadrant_data(
    db: DbSession,
    _access: CurrentSuperAdminUser,
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
    _access: CurrentSuperAdminUser,
    type: str | None = Query(None, description="贡献者类型: author/reviewer/all（与 PR 流水线一致）"),
    role_type: str | None = Query(None, description="[deprecated] 旧参数，等价于 type"),
    repo_name: str | None = Query(None, description="仓库名过滤"),
    sort_by: str | None = Query(None, description="排序字段: pr_count/review_count/merged_count/lines_added/lines_removed/score/..."),
    sort_order: str = Query("desc", description="排序方向: asc/desc"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(20, ge=1, le=200, description="分页大小"),
    quadrant: str | None = Query(None, description="四象限过滤: INCENTIVE/IMPROVE/ENCOURAGE/REPLACE"),
    y_axis_type: str | None = Query(None, description="四象限Y轴类型: error_score/review_count/merged_pr_count/code_additions"),
    days: int = Query(30, ge=1, le=365, description="统计时间窗（天），默认 30 天与 PR 流水线一致"),
):
    """获取排行榜（完全复用 PR 流水线贡献者数据，管理员可见）"""
    service = get_contributor_board_service()
    # type 优先，role_type 作为 deprecated 兼容
    effective_type = type or role_type or "author"
    return await service.get_ranking(
        db, role_type=effective_type, repo_name=repo_name,
        sort_by=sort_by, sort_order=sort_order, offset=offset, limit=limit,
        quadrant=quadrant, y_axis_type=y_axis_type, days=days,
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
async def trigger_sync(
    request: ContributorSyncRequest,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """手动触发贡献者数据采集（管理员可见）

    异步执行：立即返回 record_id，同步任务在后台执行
    """
    import logging
    from fastapi import HTTPException

    logger = logging.getLogger(__name__)
    service = get_contributor_board_service()
    try:
        logger.info(
            "Triggering sync: repo=%s, full_resync=%s, since=%s, until=%s",
            request.repo_name, request.full_resync, request.since_date, request.until_date
        )
        return await service.trigger_sync(
            db=db,
            since_date=request.since_date,
            until_date=request.until_date,
            full_resync=request.full_resync,
            repo_name=request.repo_name,
        )
    except ValueError as e:
        # 业务异常：任务已在运行
        logger.warning("Sync trigger failed (business error): %s", e)
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        # 其他异常：记录详细日志并重新抛出
        logger.error(
            "Sync trigger failed (unexpected error): %s",
            e, exc_info=True
        )
        raise


@router.get("/sync/status/{record_id}", response_model=ContributorSyncRecordResponse)
async def get_sync_status(
    record_id: int,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """获取同步任务状态（管理员可见）"""
    service = get_contributor_board_service()
    record = await service.get_sync_record(db, record_id)
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Sync record {record_id} not found")
    return record


@router.get("/sync/history", response_model=ContributorSyncHistoryResponse)
async def get_sync_history(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取同步历史记录（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_sync_history(db, limit=limit, offset=offset, repo_name=repo_name, status=status)


@router.get("/sync/summary", response_model=ContributorSyncSummary)
async def get_sync_summary(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
):
    """获取同步统计摘要（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_sync_summary(db, repo_name=repo_name)


@router.post("/sync/status/{record_id}/cancel")
async def cancel_sync(
    record_id: int,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """取消/终止正在运行的同步任务（管理员可见）"""
    from datetime import UTC, datetime
    from infrastructure.tasks.background_task import BackgroundTaskTracker
    from sqlalchemy import update as sa_update, text

    sync_record = await db.get(ContributorSyncRecord, record_id)
    if not sync_record:
        from infrastructure.core.exceptions import NotFoundError
        raise NotFoundError(f"Sync record {record_id} not found")
    if sync_record.status not in ("running", "pending", "cancelled"):
        from infrastructure.core.exceptions import ValidationError
        raise ValidationError(f"Sync record {record_id} status={sync_record.status}, cannot cancel")

    # 更新 contributor_sync_records
    await db.execute(
        sa_update(ContributorSyncRecord)
        .where(ContributorSyncRecord.id == record_id)
        .values(status="cancelled", progress_message="任务被用户取消", completed_at=datetime.now(UTC))
    )
    await db.commit()

    # 通过 task_params 中的 sync_record_id 查找并取消 background task
    try:
        bg_result = await db.execute(
            text(f"""
                SELECT id FROM {BackgroundTaskTracker.TABLE_NAME}
                WHERE task_type = 'contributor_sync'
                  AND status IN ('running', 'pending')
                  AND JSON_EXTRACT(task_params, '$.sync_record_id') = :sync_id
            """),
            {"sync_id": record_id},
        )
        bg_row = bg_result.fetchone()
        if bg_row:
            bg_task_id = bg_row[0]
            await BackgroundTaskTracker.cancel(bg_task_id)
            logger.info("Cancelled background task %d for sync record %d", bg_task_id, record_id)
    except Exception as e:
        logger.warning("Failed to cancel background task for sync %d: %s", record_id, e)

    return {"success": True, "message": "任务已取消"}


# ---- 角色统计（管理员可见）----

@router.get("/stats/roles", response_model=RoleStats)
async def get_role_stats(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    days: int = Query(30, ge=1, le=365, description="统计时间窗（天），默认 30 与 PR 流水线一致"),
):
    """获取角色维度统计（管理员可见）"""
    service = get_contributor_board_service()
    return await service.get_role_stats(db, repo_name=repo_name, days=days)


# ---- 模块热点（管理员可见）----

@router.get("/stats/module_hotspot", response_model=ModuleHotspotResponse)
async def get_module_hotspot(
    db: DbSession,
    _access: CurrentSuperAdminUser,
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
    _access: CurrentSuperAdminUser,
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
async def get_settings(db: DbSession):
    """获取贡献者分析设置"""
    service = get_contributor_board_service()
    return await service.get_settings(db)


@router.put("/settings/quadrant", response_model=ContributorBoardSetting)
async def update_quadrant_config(db: DbSession, request: UpdateQuadrantConfigRequest):
    """更新四象限Y轴配置"""
    service = get_contributor_board_service()
    return await service.update_quadrant_config(db, request)


@router.put("/settings/scoring_weights", response_model=ContributorBoardSetting)
async def update_scoring_weights(db: DbSession, request: UpdateScoringWeightsRequest):
    """更新分数计算权重"""
    service = get_contributor_board_service()
    return await service.update_scoring_weights(db, request)


@router.put("/settings/role_thresholds", response_model=ContributorBoardSetting)
async def update_role_thresholds(db: DbSession, request: UpdateRoleThresholdsRequest):
    """更新角色阈值"""
    service = get_contributor_board_service()
    return await service.update_role_thresholds(db, request)


@router.post("/settings/recalculate_scores")
async def recalculate_scores(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None, description="指定仓库，不传则重算所有仓库"),
):
    """权重变更后，读取最新权重配置并批量重算所有贡献者分数（管理员可见）"""
    service = get_contributor_board_service()
    return await service.recalculate_scores(db, repo_name=repo_name)


# ---- 定时同步设置 ----

@router.get("/settings/sync-schedule", response_model=ContributorSyncScheduleConfig)
async def get_sync_schedule(db: DbSession):
    """获取定时同步设置"""
    service = get_contributor_board_service()
    return await service.get_sync_schedule(db)


@router.put("/settings/sync-schedule", response_model=ContributorSyncScheduleConfig)
async def update_sync_schedule(db: DbSession, request: ContributorSyncScheduleUpdateRequest):
    """更新定时同步设置
    
    保存后会通知 Scheduler 进程重载定时任务配置。
    """
    from infrastructure.tasks.scheduler_config import persist_scheduler_runtime_config
    import json
    import uuid

    service = get_contributor_board_service()
    result = await service.update_sync_schedule(db, request)

    # 触发 Scheduler 重载：通过 control_outbox 发消息
    try:
        from sqlalchemy import text as sa_text

        async with db.begin_nested():
            version_result = await db.execute(
                sa_text(
                    """
                    SELECT aggregate_version FROM control_outbox
                    WHERE aggregate_type = 'scheduler'
                      AND aggregate_id = 'runtime-config'
                      AND event_type = 'scheduler.config.reload'
                    ORDER BY aggregate_version DESC LIMIT 1
                    """
                )
            )
            current_version = version_result.scalar_one_or_none() or 0

            await db.execute(
                sa_text(
                    """
                    INSERT INTO control_outbox
                        (event_id, aggregate_type, aggregate_id, event_type, aggregate_version, payload)
                    VALUES
                        (:event_id, 'scheduler', 'runtime-config', 'scheduler.config.reload', :version, :payload)
                    """
                ),
                {
                    "event_id": str(uuid.uuid4()),
                    "version": int(current_version) + 1,
                    "payload": json.dumps({"changed": ["contributor_sync_schedule"]}),
                },
            )
        logger.info("Queued contributor sync schedule reload for scheduler")
    except Exception as e:
        logger.warning("Failed to queue scheduler reload: %s", e)

    return result


# ---- 贡献者详情页增强（登录即可访问）----

@router.get("/contributors/{login}/heatmap", response_model=ContributorHeatmapResponse)
async def get_contributor_heatmap(
    login: str,
    db: DbSession,
    repo_name: str | None = Query(None),
    years: int = Query(1, ge=1, le=5, description="回溯年数"),
):
    """获取贡献者提交热力图数据"""
    service = get_contributor_board_service()
    data = await service.get_contributor_heatmap_data(
        db, login=login, repo_name=repo_name, years=years
    )
    from contracts.schemas.contributor_board import HeatmapDataPoint
    return ContributorHeatmapResponse(
        login=data["login"],
        heatmap_data=[HeatmapDataPoint(**p) for p in data["heatmap_data"]],
    )


@router.get("/contributors/{login}/distribution", response_model=ContributorDistributionResponse)
async def get_contributor_distribution(
    login: str,
    db: DbSession,
    repo_name: str | None = Query(None),
):
    """获取贡献者模块+类型分布数据"""
    service = get_contributor_board_service()
    data = await service.get_contributor_module_distribution(
        db, login=login, repo_name=repo_name
    )
    from contracts.schemas.contributor_board import (
        ModuleDistributionItem,
        TypeDistributionItem,
    )
    return ContributorDistributionResponse(
        login=data["login"],
        modules=[ModuleDistributionItem(**m) for m in data["modules"]],
        type_distribution=[TypeDistributionItem(**t) for t in data["type_distribution"]],
    )


@router.get("/contributors/{login}/timeline", response_model=ContributorTimelineResponse)
async def get_contributor_timeline(
    login: str,
    db: DbSession,
    limit: int = Query(20, ge=1, le=100),
):
    """获取贡献者提交时间线"""
    service = get_contributor_board_service()
    data = await service.get_contributor_timeline(db, login=login, limit=limit)
    from contracts.schemas.contributor_board import TimelineCommitItem
    return ContributorTimelineResponse(
        login=data["login"],
        commits=[TimelineCommitItem(**c) for c in data["commits"]],
    )


# =====================================================================
# 评分权重版本管理 (C-3 ~ C-7)
# =====================================================================

from contributor_board.scoring_version_service import ScoringVersionService
from contributor_board.scoring_version_schemas import (
    AIReportDetailResponse,
    AIReportGenerateRequest,
    AIReportResponse,
    CalculateAutoWeightsResponse,
    ScoreSnapshotResponse,
    ScoringVersionCreateRequest,
    ScoringVersionResponse,
    ScoreUpdateAuditResponse,
    VersionCompareResponse,
)


# ---- C-3: 权重版本 CRUD + 应用 + 对比 ----

@router.get("/settings/scoring/versions", response_model=dict)
async def get_scoring_versions(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    status: str | None = Query(None, description="版本状态: draft/active/archived"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """获取权重版本历史列表"""
    svc = ScoringVersionService()
    return await svc.list_versions(db, repo_name=repo_name, status=status, skip=skip, limit=limit)


@router.get("/settings/scoring/versions/active", response_model=ScoringVersionResponse | None)
async def get_active_scoring_version(
    db: DbSession,
    repo_name: str | None = Query(None),
):
    """获取当前生效的权重版本"""
    svc = ScoringVersionService()
    return await svc.get_active_version(db, repo_name=repo_name)


@router.get("/settings/scoring/versions/{version_id}", response_model=ScoringVersionResponse | None)
async def get_scoring_version(
    version_id: int,
    db: DbSession,
):
    """获取权重版本详情"""
    svc = ScoringVersionService()
    return await svc.get_version(db, version_id)


@router.post("/settings/scoring/versions", response_model=ScoringVersionResponse)
async def create_scoring_version(
    request: ScoringVersionCreateRequest,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """创建新的权重版本"""
    svc = ScoringVersionService()
    return await svc.create_version(db, request, created_by=_access.username)


@router.post("/settings/scoring/versions/{version_id}/apply", response_model=ScoringVersionResponse)
async def apply_scoring_version(
    version_id: int,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """应用指定版本为生效版本（自动归档当前版本 + 重算 + 生成快照）"""
    svc = ScoringVersionService()
    return await svc.apply_version(db, version_id, created_by=_access.username)


@router.post("/settings/scoring/versions/{version_id}/rollback", response_model=ScoringVersionResponse)
async def rollback_scoring_version(
    version_id: int,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """回滚到指定版本"""
    svc = ScoringVersionService()
    return await svc.rollback_to_version(db, version_id, created_by=_access.username)


@router.post("/settings/scoring/versions/compare", response_model=VersionCompareResponse)
async def compare_scoring_versions(
    db: DbSession,
    version_id_a: int = Query(..., description="版本A ID"),
    version_id_b: int = Query(..., description="版本B ID"),
):
    """对比两个权重版本"""
    svc = ScoringVersionService()
    return await svc.compare_versions(db, version_id_a, version_id_b)


# ---- C-4: 自动评估 + AI评估 ----

@router.post("/settings/scoring/auto-evaluate", response_model=CalculateAutoWeightsResponse)
async def auto_evaluate_weights(
    db: DbSession,
    repo_name: str | None = Query(None),
):
    """执行自动权重评估（熵权法），返回结果但不自动创建版本"""
    from contributor_board.auto_weight_engine import compute_weights_from_db

    weights, confidence, data_summary = await compute_weights_from_db(db, repo_name)

    return CalculateAutoWeightsResponse(
        weights=weights,
        confidence=confidence,
        data_summary=data_summary,
    )


@router.post("/settings/scoring/ai-evaluate", response_model=CalculateAutoWeightsResponse)
async def ai_evaluate_weights(
    db: DbSession,
    repo_name: str | None = Query(None),
    model: str | None = Query(None, description="指定 LLM 模型"),
):
    """执行 AI 权重评估"""
    from contributor_board.auto_weight_engine import compute_weights_from_db
    from contributor_board.ai_weight_engine import analyze_weights_with_ai

    _, _, data_summary = await compute_weights_from_db(db, repo_name)
    ai_weights, ai_analysis, ai_model = await analyze_weights_with_ai(db, data_summary, model)

    return CalculateAutoWeightsResponse(
        weights=ai_weights,
        confidence=0.7,
        data_summary={**data_summary, "ai_analysis": ai_analysis, "ai_model": ai_model},
    )


# ---- C-5: 分数快照 CRUD + 对比 + 导出 ----

@router.get("/settings/scoring/snapshots", response_model=dict)
async def get_score_snapshots(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """获取分数快照列表"""
    svc = ScoringVersionService()
    return await svc.list_snapshots(db, repo_name=repo_name, skip=skip, limit=limit)


@router.get("/settings/scoring/snapshots/{snapshot_id}", response_model=ScoreSnapshotResponse | None)
async def get_score_snapshot(
    snapshot_id: int,
    db: DbSession,
):
    """获取分数快照详情"""
    svc = ScoringVersionService()
    return await svc.get_snapshot(db, snapshot_id)


@router.post("/settings/scoring/snapshots/generate", response_model=ScoreSnapshotResponse | None)
async def generate_score_snapshot(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    version_id: int = Query(..., description="权重版本 ID"),
):
    """手动生成分数快照（基于指定版本）"""
    svc = ScoringVersionService()
    return await svc.generate_snapshot(db, version_id, created_by=_access.username)


@router.get("/settings/scoring/snapshots/export/pdf")
async def export_snapshots_pdf(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    version_id: int = Query(..., description="权重版本 ID"),
):
    """导出分数快照为 PDF — 全量数据，包含所有贡献者"""
    from contributor_board.scoring_version_service import ScoringVersionService
    from contributor_board.report_exporter import export_pdf
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse

    svc = ScoringVersionService()
    version = await svc.get_version(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    snapshot = await svc.generate_snapshot(db, version_id, created_by=_access.username)
    if not snapshot:
        raise HTTPException(status_code=500, detail="Failed to generate snapshot")

    version_info = {
        "version": version.version,
        "method": version.method,
    }

    pdf_bytes, filename = await export_pdf(db, snapshot.scores, version_info, statistics=snapshot.statistics)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---- C-6: 审计日志查询 ----

@router.get("/settings/scoring/audit-log", response_model=dict)
async def get_audit_logs(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """获取评分更新审计日志"""
    svc = ScoringVersionService()
    return await svc.list_audit_logs(db, repo_name=repo_name, skip=skip, limit=limit)


# ---- C-7: AI 报告生成 + 查询 ----

def _get_ai_report_service():
    from contributor_board.ai_report_service import AIReportService
    return AIReportService()


@router.get("/settings/reports/ai", response_model=dict)
async def get_ai_reports(
    db: DbSession,
    _access: CurrentSuperAdminUser,
    repo_name: str | None = Query(None),
    report_type: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """获取 AI 综合报告列表"""
    svc = _get_ai_report_service()
    return await svc.list_reports(db, repo_name=repo_name, report_type=report_type, skip=skip, limit=limit)


@router.get("/settings/reports/ai/{report_id}", response_model=AIReportDetailResponse)
async def get_ai_report_detail(
    report_id: int,
    db: DbSession,
):
    """获取 AI 综合报告详情"""
    svc = _get_ai_report_service()
    return await svc.get_report(db, report_id)


@router.post("/settings/reports/ai/generate", response_model=AIReportResponse)
async def generate_ai_report(
    request: AIReportGenerateRequest,
    db: DbSession,
    _access: CurrentSuperAdminUser,
):
    """生成 AI 综合分析报告"""
    svc = _get_ai_report_service()
    return await svc.generate_report(db, request, created_by=_access.username)
