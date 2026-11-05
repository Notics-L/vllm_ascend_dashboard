"""
贡献者看板服务层（重构版）。

数据来源统一为 contributor_profiles 宽表（每贡献者一行）。
核心能力：
    - 总体概述 (get_overview_stats)
    - 综合排行榜 (get_ranking)  author / reviewer / all
    - 四象限 (get_quadrant_data)  X=total_score, Y=review_ratio
    - 角色统计 (get_role_stats) 含 reviewer 定位
    - 贡献者详情 / 趋势 (get_contributor_detail / get_contributor_trend)
    - 构建优化 (build plans) —— 保留
    - 同步管理 (trigger_sync / 历史 / 摘要) / 设置
    - 评分重算 (recalculate_scores)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from contributor_board.classifier import classify_commit
from contributor_board.quadrant_engine import calculate_quadrants
from contributor_board.scoring_engine import (
    assign_tier,
    calculate_score,
    get_default_weights,
    recalculate_all_scores,
)
from contracts.schemas.contributor_board import (
    BuildOptimizationStats,
    BuildPlanListResponse,
    BuildPlanResponse,
    ContributorDetailResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingItem,
    ContributorRankingResponse,
    ContributorResponse,
    ContributorSyncHistoryResponse,
    ContributorSyncRecordResponse,
    ContributorSyncResponse,
    ContributorSyncSummary,
    ContributorTrendPoint,
    ContributorTrendResponse,
    ContributorBoardSettingsResponse,
    QuadrantPoint,
    QuadrantDataResponse,
    RoleStats,
    ScoringWeightsConfig,
    ContributorBoardSetting,
    ContributorSyncScheduleConfig,
    UpdateQuadrantConfigRequest,
    UpdateScoringWeightsRequest,
    UpdateRoleThresholdsRequest,
    RoleThresholdsConfig,
)
from infrastructure.persistence.models import (
    Contributor,
    ContributorBoardSettings,
    ContributorProfile,
    ContributorSyncRecord,
    DailyCommit,
    DailyPR,
    ImageBuildPlan,
)
from sqlalchemy import and_, delete, func, or_, select

logger = logging.getLogger(__name__)

_TIER_ORDER = {"INCENTIVE": 0, "ENCOURAGE": 1, "IMPROVE": 2, "REPLACE": 3}
# 兼容旧四象限名（前端/旧参数可能传 star/active/observer/at_risk）
_LEGACY_TIER_MAP = {"star": "INCENTIVE", "active": "ENCOURAGE", "observer": "ENCOURAGE", "at_risk": "REPLACE"}


def _normalize_tier(value: str | None) -> str | None:
    if not value:
        return None
    value = value.upper()
    v = _LEGACY_TIER_MAP.get(value.lower()) or value
    return v if v in _TIER_ORDER else None


class ContributorBoardService:
    """贡献者看板服务（重构版）"""

    # ══════════════════════════ 概述 ══════════════════════════

    async def get_overview_stats(
        self, db, repo_name: str | None = None, days: int = 30
    ) -> ContributorOverviewStats:
        base = select(ContributorProfile)
        if repo_name:
            base = base.where(ContributorProfile.repo_name == repo_name)
        rows = (await db.execute(base)).scalars().all()

        total = len(rows)
        community = sum(1 for r in rows if r.role != "developer")
        tier_dist: dict[str, int] = {"INCENTIVE": 0, "ENCOURAGE": 0, "IMPROVE": 0, "REPLACE": 0}
        for r in rows:
            if r.tier in tier_dist:
                tier_dist[r.tier] += 1

        # Top 5 贡献者（按综合分）
        top_rows = sorted(rows, key=lambda r: r.total_score or 0, reverse=True)[:5]
        top_contributors = [self._to_ranking_item(r, i + 1) for i, r in enumerate(top_rows)]

        build_stats = await self._build_optimization_stats(db, repo_name)
        last_sync = await self._last_sync_at(db, repo_name)

        return ContributorOverviewStats(
            total_contributors=total,
            community_contributors=community,
            total_commits_classified=sum(r.commit_count or 0 for r in rows),
            classification_distribution=self._type_distribution(rows),
            quadrant_distribution=tier_dist,
            build_optimization_stats=build_stats,
            top_contributors=top_contributors,
            last_synced_at=last_sync,
        )

    # ══════════════════════════ 贡献者列表（兼容旧）══════════════════════════════

    async def get_contributors(
        self, db, filter: ContributorListFilter
    ) -> ContributorListResponse:
        base = select(Contributor)
        tier = _normalize_tier(filter.quadrant)
        # 若按象限/角色过滤，需要 join profile
        if tier or filter.role_type:
            joined = (
                select(Contributor)
                .join(ContributorProfile, ContributorProfile.contributor_id == Contributor.id)
            )
            if tier:
                joined = joined.where(ContributorProfile.tier == tier)
            if filter.role_type:
                joined = joined.where(Contributor.role_types.contains([filter.role_type]))
            if filter.repo_name:
                joined = joined.where(ContributorProfile.repo_name == filter.repo_name)
            base = joined
        if filter.search:
            pat = f"%{filter.search}%"
            base = base.where(and_(Contributor.login.ilike(pat), Contributor.name.ilike(pat)))
        if filter.repo_name and not (tier or filter.role_type):
            base = (
                select(Contributor)
                .join(ContributorProfile, ContributorProfile.contributor_id == Contributor.id)
                .where(ContributorProfile.repo_name == filter.repo_name)
            )

        total = (
            await db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            (await db.execute(base.order_by(Contributor.last_contribution_at.desc())
                              .limit(filter.limit).offset(filter.offset)))
            .scalars().all()
        )
        return ContributorListResponse(
            contributors=[ContributorResponse.model_validate(r) for r in rows],
            total=int(total),
        )

    # ══════════════════════════ 综合排行榜 ══════════════════════════

    async def get_ranking(
        self,
        db,
        role_type: str = "author",
        repo_name: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
        quadrant: str | None = None,
        y_axis_type: str | None = None,
        days: int = 30,
    ) -> ContributorRankingResponse:
        base = select(ContributorProfile)
        if role_type == "author":
            # 贡献者榜单：排除"纯 reviewer"（role='reviewer' 且无任何作者 PR）。
            # 多身份贡献者（role 被标成 reviewer 但 pr_merged/pr_open/pr_closed>0，
            # 如 wangxiyuan）需同时出现在贡献者榜单，否则开发者侧搜不到。
            base = base.where(
                or_(
                    ContributorProfile.role != "reviewer",
                    ContributorProfile.pr_merged > 0,
                    ContributorProfile.pr_open > 0,
                    ContributorProfile.pr_closed > 0,
                )
            )
        elif role_type == "reviewer":
            base = base.where(ContributorProfile.role == "reviewer")
        # role_type == "all"：不加角色过滤
        if repo_name:
            base = base.where(ContributorProfile.repo_name == repo_name)
        tier = _normalize_tier(quadrant)
        if tier:
            base = base.where(ContributorProfile.tier == tier)

        # 时间窗过滤：宽表只存全量累计（first_seen..last_seen），无日度明细。
        # 按 days 窗口过滤"窗口内有活动"的贡献者：last_seen >= today - days。
        # 同时按 max(days, active_days_90)/90 把 7 维指标缩放到窗口内估计值，
        # 再用 scoring_engine 重算 score/tier，让 days 选项真正影响榜单。
        days = max(1, int(days))
        window_start = date.today() - timedelta(days=days)
        base = base.where(ContributorProfile.last_seen >= window_start)

        rows = (await db.execute(base)).scalars().all()

        # 按窗口缩放指标 + 重算 score
        weights = self._load_weights_setting(
            (await db.execute(
                select(ContributorBoardSettings).where(
                    ContributorBoardSettings.setting_key == "scoring_weights"
                )
            )).scalars().first()
        )
        scale = min(1.0, days / 90.0)  # active_days_90 是 90 天窗口值；days>=90 不缩放
        for r in rows:
            active = r.active_days_90 or 0
            if active > 0:
                # 窗口内活跃天数估计：min(active * scale, days)
                # 当 days>=90 时 scale=1.0，active 不变
                r.active_days_90 = max(1, int(active * scale)) if scale < 1.0 else active
            # span_days 缩放到窗口内（last_seen - first_seen 跨度，窗口内最大为 days）
            r.span_days = min(r.span_days or 0, days)
            # 重算 score + tier
            metrics = {
                "commit_count": r.commit_count or 0,
                "code_files": r.code_files or 0,
                "doc_files": r.doc_files or 0,
                "pr_merged": r.pr_merged or 0,
                "pr_open": r.pr_open or 0,
                "pr_closed": r.pr_closed or 0,
                "active_days_90": r.active_days_90 or 0,
                "span_days": r.span_days or 0,
            }
            r.total_score = calculate_score(metrics, weights)
            r.tier = assign_tier(r.total_score, r.active_days_90 or 0)

        total = len(rows)

        # 内存排序（用重算后的 score）
        sort_key = self._sort_key(sort_by)
        rows.sort(key=sort_key, reverse=(sort_order != "asc"))

        # join 身份信息（avatar/employee/company/email）
        logins = [r.login for r in rows]
        ident_map = await self._contributor_identity_map(db, logins)

        items = []
        for i, r in enumerate(rows[offset:offset + limit]):
            item = self._to_ranking_item(r, offset + i + 1)
            ident = ident_map.get(r.login) or {}
            item.avatar_url = ident.get("avatar_url")
            item.employee_id = ident.get("employee_id")
            item.company = ident.get("company")
            item.primary_email = ident.get("email")
            item.contributor_type = "reviewer" if r.role == "reviewer" else "author"
            items.append(item)

        return ContributorRankingResponse(
            role_type=role_type,
            rankings=items,
            total=total,
            repo_name=repo_name,
        )

    async def get_module_hotspot(
        self, db, repo_name: str | None = None,
        period_start: date | None = None, period_end: date | None = None,
    ) -> dict:
        from contracts.schemas.contributor_board import ModuleHotspotResponse
        return ModuleHotspotResponse(
            modules=[], total_changes=0, repo_name=repo_name,
            period_start=period_start, period_end=period_end,
        )

    async def get_role_ranking(
        self, db, role_type: str, repo_name: str | None = None, limit: int = 10
    ) -> ContributorRankingResponse:
        return await self.get_ranking(
            db, role_type=role_type, repo_name=repo_name,
            sort_by="score", limit=limit,
        )

    # ══════════════════════════ 策略建议 ══════════════════════════

    async def get_contributor_policy(
        self, db, login: str
    ) -> dict | None:
        from contributor_board.policy_engine import evaluate_policy_from_db
        return await evaluate_policy_from_db(db, login=login)

    # ══════════════════════════ 四象限 ══════════════════════════

    async def get_quadrant_data(
        self, db, repo_name: str | None = None, y_axis_type: str | None = None
    ) -> QuadrantDataResponse:
        base = select(ContributorProfile, Contributor)
        stmt = (
            select(ContributorProfile)
        )
        if repo_name:
            stmt = stmt.where(ContributorProfile.repo_name == repo_name)
        rows = (await db.execute(stmt)).scalars().all()

        ident_map = await self._contributor_identity_map(db, [r.login for r in rows])
        metrics_list = []
        for r in rows:
            ident = ident_map.get(r.login) or {}
            metrics_list.append({
                "login": r.login,
                "avatar_url": ident.get("avatar_url"),
                "total_score": r.total_score or 0,
                "review_ratio": r.review_ratio or 0,
                "merged_pr_count": r.pr_merged or 0,
                "is_community": ident.get("is_community", True),
            })

        points, quad_x, quad_y = calculate_quadrants(metrics_list)
        ident_full = ident_map
        out_points = []
        quad_counts: dict[str, int] = {"INCENTIVE": 0, "IMPROVE": 0, "ENCOURAGE": 0, "REPLACE": 0}
        for p in points:
            ident = ident_full.get(p.login) or {}
            out_points.append(QuadrantPoint(
                login=p.login,
                avatar_url=p.avatar_url,
                contribution_score=p.x_value,
                error_score=0,
                y_value=p.y_value,
                y_axis_type="review_ratio",
                quadrant=p.quadrant,
                role_label=p.quadrant,
                merged_pr_count=p.merged_pr_count,
                is_community=p.is_community,
                company=ident.get("company"),
                primary_email=ident.get("email"),
            ))
            if p.quadrant in quad_counts:
                quad_counts[p.quadrant] += 1

        return QuadrantDataResponse(
            points=out_points,
            quad_x=quad_x,
            quad_y=quad_y,
            y_axis_type="review_ratio",
            repo_name=repo_name,
            total_count=len(out_points),
            quadrant_counts=quad_counts,
        )

    # ══════════════════════════ 角色统计 ══════════════════════════

    async def get_role_stats(self, db, repo_name: str | None = None, days: int = 30) -> RoleStats:
        stmt = select(ContributorProfile)
        if repo_name:
            stmt = stmt.where(ContributorProfile.repo_name == repo_name)
        rows = (await db.execute(stmt)).scalars().all()

        developers = [r for r in rows if r.role == "developer"]
        reviewers = [r for r in rows if r.role == "reviewer"]
        mixed = [r for r in rows if r.role == "contributor"]

        def _avg(lst: list[ContributorProfile]) -> float:
            return round(sum(r.total_score or 0 for r in lst) / len(lst), 2) if lst else 0.0

        return RoleStats(
            developer_count=len(developers),
            reviewer_count=len(reviewers),
            total_pr=sum(r.pr_merged or 0 for r in rows),
            total_code_lines=sum(r.commit_count or 0 for r in rows),
            total_reviews=sum(r.review_count or 0 for r in rows),
            total_approvals=sum(r.approval_count or 0 for r in rows),
        )

    # ══════════════════════════ 贡献者详情 ══════════════════════════

    async def get_contributor_detail(
        self, db, login: str, days: int = 30
    ) -> ContributorDetailResponse | None:
        contributor = (
            await db.execute(select(Contributor).where(Contributor.login == login))
        ).scalar_one_or_none()
        if contributor is None:
            return None
        profile = (
            await db.execute(select(ContributorProfile).where(ContributorProfile.login == login))
        ).scalars().first()
        base = ContributorResponse.model_validate(contributor)
        if profile is None:
            return ContributorDetailResponse(**base.model_dump())
        return ContributorDetailResponse(
            **base.model_dump(),
            merged_pr_count=profile.pr_merged or 0,
            pending_pr_count=profile.pr_open or 0,
            code_additions=profile.code_files or 0,
            code_deletions=profile.doc_files or 0,
            review_count=profile.review_count or 0,
            approval_count=profile.approval_count or 0,
            contribution_score=profile.total_score or 0,
            error_score=0,
            quadrant=profile.tier,
        )

    async def get_contributor_trend(
        self, db, login: str, period_start: date | None = None, period_end: date | None = None
    ) -> ContributorTrendResponse:
        # 宽表无月度明细，趋势返回基于最近活跃窗口的近似（单点汇总）
        profile = (
            await db.execute(select(ContributorProfile).where(ContributorProfile.login == login))
        ).scalars().first()
        points: list[ContributorTrendPoint] = []
        if profile and profile.last_seen:
            points = [ContributorTrendPoint(
                date=profile.last_seen,
                merged_pr_count=profile.pr_merged or 0,
                code_additions=profile.code_files or 0,
                code_deletions=profile.doc_files or 0,
                review_count=profile.review_count or 0,
                contribution_score=profile.total_score or 0,
            )]
        return ContributorTrendResponse(
            login=login, points=points,
            period_start=period_start, period_end=period_end,
        )

    # ══════════════════════════ 构建优化（保留）══════════════════════════════

    async def get_build_plans_from_db(
        self, db, repo_name: str | None = None, build_type: str | None = None,
        commit_type: str | None = None, created_after: date | None = None,
        created_before: date | None = None, limit: int = 20, offset: int = 0,
    ) -> BuildPlanListResponse:
        stmt = select(ImageBuildPlan)
        if repo_name:
            stmt = stmt.where(ImageBuildPlan.repo_name == repo_name)
        if build_type:
            stmt = stmt.where(ImageBuildPlan.build_type == build_type)
        if commit_type:
            stmt = stmt.where(ImageBuildPlan.commit_type == commit_type)
        if created_after:
            stmt = stmt.where(ImageBuildPlan.created_at >= created_after)
        if created_before:
            stmt = stmt.where(ImageBuildPlan.created_at <= created_before)
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (
            await db.execute(stmt.order_by(ImageBuildPlan.created_at.desc())
                             .limit(limit).offset(offset))
        ).scalars().all()
        return BuildPlanListResponse(
            plans=[BuildPlanResponse.model_validate(r) for r in rows], total=int(total)
        )

    async def get_build_optimization_stats_from_db(
        self, db, repo_name: str | None = None
    ) -> BuildOptimizationStats:
        return await self._build_optimization_stats(db, repo_name)

    async def get_classification_summary_from_db(
        self, db, repo_name: str | None = None
    ) -> dict:
        from contracts.schemas.contributor_board import ClassificationSummary
        stmt = select(ImageBuildPlan.commit_type, func.count().label("cnt"))
        if repo_name:
            stmt = stmt.where(ImageBuildPlan.repo_name == repo_name)
        rows = (await db.execute(stmt.group_by(ImageBuildPlan.commit_type))).all()
        total = sum(r[1] for r in rows)
        type_dist = {r[0]: int(r[1]) for r in rows if r[0]}
        type_pct = {k: round(v * 100.0 / total, 2) for k, v in type_dist.items()} if total else {}
        return ClassificationSummary(
            total_commits=total,
            type_distribution=type_dist,
            type_percentage=type_pct,
            chip_specific_count=0,
            module_hotspot={},
        )

    async def get_build_plan_filter_options(
        self, db, repo_name: str | None = None
    ) -> dict[str, list[str]]:
        stmt = select(ImageBuildPlan.commit_type, ImageBuildPlan.build_type)
        if repo_name:
            stmt = stmt.where(ImageBuildPlan.repo_name == repo_name)
        rows = (await db.execute(stmt)).all()
        commit_types = sorted({r[0] for r in rows if r[0]})
        build_types = sorted({r[1] for r in rows if r[1]})
        return {"commit_types": commit_types, "build_types": build_types}

    async def _build_optimization_stats(self, db, repo_name: str | None) -> BuildOptimizationStats:
        stmt = select(ImageBuildPlan.build_type, func.count(), func.avg(ImageBuildPlan.estimated_saving_percent))
        if repo_name:
            stmt = stmt.where(ImageBuildPlan.repo_name == repo_name)
        rows = (await db.execute(stmt.group_by(ImageBuildPlan.build_type))).all()
        by_type = {r[0]: int(r[1]) for r in rows if r[0]}
        total = sum(by_type.values())
        skipped = by_type.get("skip", 0)
        reduced = sum(v for k, v in by_type.items() if k in ("minimal_build", "nightly_only", "smart_check", "conservative"))
        full = sum(v for k, v in by_type.items() if k == "full_build")
        avg_saving = 0.0
        for r in rows:
            if r[2] is not None:
                avg_saving += (float(r[2]) or 0) * int(r[1])
        avg_saving = round(avg_saving / total, 2) if total else 0.0
        return BuildOptimizationStats(
            total_evaluations=total, skipped_builds=skipped, reduced_builds=reduced,
            full_builds=full, avg_saving_percent=avg_saving, by_type=by_type,
        )

    # ══════════════════════════ 同步管理 ══════════════════════════

    async def clear_sync_data(self, db) -> dict[str, int]:
        """一键清空贡献者模块的采集数据，回到"干净"状态便于重新全量同步。

        覆盖范围（排行榜 / 四象限 / 贡献者详情页图表 / 热力图 / 时间线 的数据源）：
            - contributor_profiles  宽表（排行榜 + 四象限）
            - contributors          贡献者身份表
            - daily_prs             日度 PR 明细（详情页趋势等）
            - daily_commits         日度 commit 明细（详情页热力图 / 模块分布 / 时间线）
            - contributor_sync_records 同步历史
        并把所有 running/pending 的任务置为 cancelled，避免残留"运行中"状态。

        刻意保留：评估设置(contributor_board_settings)、评分权重版本 / 快照 / 审计
        （它们是配置与历史版本，不影响采集数据，重跑时会按当前权重自动重算分数）。

        单事务原子执行：全部清成功才 commit，任一失败则整体回滚。
        """
        now = datetime.now(UTC)
        counts: dict[str, int] = {}

        # 1. 宽表（排行榜 + 四象限数据源）
        r = await db.execute(delete(ContributorProfile))
        counts["contributor_profiles"] = int(r.rowcount or 0)
        # 2. 日度明细（详情页图表 / 热力图 / 时间线数据源）
        r = await db.execute(delete(DailyPR))
        counts["daily_prs"] = int(r.rowcount or 0)
        r = await db.execute(delete(DailyCommit))
        counts["daily_commits"] = int(r.rowcount or 0)
        # 3. 贡献者身份表
        r = await db.execute(delete(Contributor))
        counts["contributors"] = int(r.rowcount or 0)
        # 4. 运行中/待处理任务置为 cancelled（保留历史记录供审计）
        res = (
            await db.execute(
                select(ContributorSyncRecord).where(
                    ContributorSyncRecord.status.in_(["running", "pending"])
                )
            )
        ).scalars().all()
        for rec in res:
            rec.status = "cancelled"
            rec.completed_at = now
            rec.progress_message = (rec.progress_message or "") + " | 已由'清除并重置'终止"
        counts["sync_records_cancelled"] = len(res)
        # 5. 同步历史
        r = await db.execute(delete(ContributorSyncRecord))
        counts["sync_records"] = int(r.rowcount or 0)

        await db.commit()
        logger.warning(
            "Contributor board collector data cleared: %s", counts
        )
        return counts

    async def trigger_sync(
        self, db, since_date: date | None = None, until_date: date | None = None,
        full_resync: bool = False, repo_name: str | None = None,
    ) -> ContributorSyncResponse:
        # 已有 running 任务则拒绝
        running = (
            await db.execute(
                select(func.count(ContributorSyncRecord.id)).where(
                    ContributorSyncRecord.status.in_(["running", "pending"])
                )
            )
        ).scalar_one()
        if int(running) > 0:
            raise ValueError("已有同步任务正在运行，请等待完成或取消后再试")

        now = datetime.now(UTC)
        record = ContributorSyncRecord(
            repo_name=repo_name,
            sync_type="full" if full_resync else "incremental",
            status="running",
            since_date=since_date,
            until_date=until_date,
            progress_percent=0,
            progress_message="任务已创建",
            trigger_source="api",
            started_at=now,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id

        logger.info("Started contributor sync task record_id=%s repo=%s", record_id, repo_name)
        asyncio.create_task(self._run_sync_task(record_id, since_date, until_date, full_resync, repo_name))

        return ContributorSyncResponse(
            contributors_synced=0,
            commits_classified=0,
            build_plans_created=0,
            errors=[],
            duration_seconds=0,
            synced_at=now,
            record_id=record_id,
        )

    async def _run_sync_task(
        self, record_id: int, since_date: date | None, until_date: date | None,
        full_resync: bool, repo_name: str | None,
    ) -> None:
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal

        since_dt = None
        until_dt = None
        if since_date:
            since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=UTC)
        if until_date:
            until_dt = datetime(until_date.year, until_date.month, until_date.day, tzinfo=UTC)

        try:
            from collector.contributor_sync import sync_contributors

            async def _progress(pct: int, msg: str):
                async with AsyncSessionLocal() as db:
                    rec = await db.get(ContributorSyncRecord, record_id)
                    if rec:
                        rec.progress_percent = pct
                        rec.progress_message = msg
                        await db.commit()

            result = await sync_contributors(
                since_date=since_dt, until_date=until_dt,
                repo_name=repo_name, progress_callback=_progress,
            )
            async with AsyncSessionLocal() as db:
                rec = await db.get(ContributorSyncRecord, record_id)
                if rec:
                    rec.status = "completed" if not result.errors else "failed"
                    rec.contributors_synced = result.contributors_synced
                    rec.commits_classified = result.commits_classified
                    rec.build_plans_created = result.build_plans_created
                    rec.total_prs_scanned = result.total_prs_scanned
                    rec.errors = result.errors or []
                    rec.duration_seconds = result.duration_seconds
                    rec.synced_at = result.synced_at
                    rec.completed_at = datetime.now(UTC)
                    rec.progress_percent = 100
                    await db.commit()
            logger.info("Contributor sync task %s finished", record_id)
        except Exception as e:
            logger.error("Contributor sync task %s failed: %s", record_id, e, exc_info=True)
            async with AsyncSessionLocal() as db:
                rec = await db.get(ContributorSyncRecord, record_id)
                if rec:
                    rec.status = "failed"
                    rec.errors = [f"Sync task failed: {e}"]
                    rec.completed_at = datetime.now(UTC)
                    await db.commit()

    async def get_sync_record(self, db, record_id: int) -> ContributorSyncRecordResponse | None:
        rec = await db.get(ContributorSyncRecord, record_id)
        if rec is None:
            return None
        return ContributorSyncRecordResponse.model_validate(rec)

    async def get_sync_history(
        self, db, limit: int = 20, offset: int = 0,
        repo_name: str | None = None, status: str | None = None,
    ) -> ContributorSyncHistoryResponse:
        stmt = select(ContributorSyncRecord)
        if repo_name:
            stmt = stmt.where(ContributorSyncRecord.repo_name == repo_name)
        if status:
            stmt = stmt.where(ContributorSyncRecord.status == status)
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (
            await db.execute(stmt.order_by(ContributorSyncRecord.created_at.desc())
                             .limit(limit).offset(offset))
        ).scalars().all()
        return ContributorSyncHistoryResponse(
            records=[ContributorSyncRecordResponse.model_validate(r) for r in rows],
            total=int(total),
        )

    async def get_sync_summary(self, db, repo_name: str | None = None) -> ContributorSyncSummary:
        base = select(ContributorSyncRecord)
        if repo_name:
            base = base.where(ContributorSyncRecord.repo_name == repo_name)
        rows = (await db.execute(base)).scalars().all()
        return ContributorSyncSummary(
            total_syncs=len(rows),
            completed_count=sum(1 for r in rows if r.status == "completed"),
            failed_count=sum(1 for r in rows if r.status == "failed"),
            running_count=sum(1 for r in rows if r.status in ("running", "pending")),
            total_contributors=sum(r.contributors_synced or 0 for r in rows),
            total_commits=sum(r.commits_classified or 0 for r in rows),
        )

    # ══════════════════════════ 设置 ══════════════════════════

    async def get_settings(self, db) -> ContributorBoardSettingsResponse:
        rows = {r.setting_key: r for r in (await db.execute(select(ContributorBoardSettings))).scalars().all()}

        weights = self._load_weights_setting(rows.get("scoring_weights"))
        quadrant_value = self._load_setting(rows.get("quadrant_config"), {"y_axis_type": "review_ratio"})
        thresholds_value = self._load_setting(rows.get("role_thresholds"), {"star_threshold": 30, "active_threshold": 15, "max_error_score": 2})

        from contracts.schemas.contributor_board import QuadrantConfig
        scoring = ScoringWeightsConfig(
            merged_pr_weight=weights.get("pr_merged_rate", 15.0),
            code_line_weight=weights.get("code_files", 15.0),
            review_weight=weights.get("commit_count", 25.0),
            approval_weight=0.0,
            core_module_weight=weights.get("doc_files", 10.0),
            files_changed_weight=weights.get("pr_total", 10.0),
            bugfix_weight=0.0,
            feature_weight=0.0,
            active_duration_weight=weights.get("span_days", 10.0),
        )
        return ContributorBoardSettingsResponse(
            quadrant_config=QuadrantConfig(
                y_axis_type=quadrant_value.get("y_axis_type", "review_ratio")
                if isinstance(quadrant_value, dict) else "review_ratio",
            ),
            scoring_weights=scoring,
            role_thresholds=RoleThresholdsConfig(
                star_threshold=thresholds_value.get("star_threshold", 30.0)
                if isinstance(thresholds_value, dict) else 30.0,
                active_threshold=thresholds_value.get("active_threshold", 15.0)
                if isinstance(thresholds_value, dict) else 15.0,
                max_error_score=thresholds_value.get("max_error_score", 2.0)
                if isinstance(thresholds_value, dict) else 2.0,
            ),
        )

    async def update_quadrant_config(self, db, request: UpdateQuadrantConfigRequest) -> ContributorBoardSetting:
        await self._upsert_setting(db, "quadrant_config", {"y_axis_type": request.y_axis_type},
                                   "四象限 Y 轴配置")
        return ContributorBoardSetting(
            setting_key="quadrant_config", setting_value={"y_axis_type": request.y_axis_type}
        )

    async def update_scoring_weights(self, db, request: UpdateScoringWeightsRequest) -> ContributorBoardSetting:
        # 直接保存 7 个维度（与 scoring_engine.DEFAULT_WEIGHTS 键对齐，合计应≈100）
        value = {
            "commit_count": float(request.commit_count),
            "code_files": float(request.code_files),
            "doc_files": float(request.doc_files),
            "pr_merged_rate": float(request.pr_merged_rate),
            "pr_total": float(request.pr_total),
            "active_days_90": float(request.active_days_90),
            "span_days": float(request.span_days),
        }
        # 合计偏差 > 10% 时打 warning（不阻断，前端滑杆可能微调）
        s = sum(value.values())
        if s > 1e-9 and abs(s - 100.0) / 100.0 > 0.1:
            logger.warning(
                "scoring_weights sum=%.2f 偏离基准 100 超 10%%，仍按原值保存", s
            )
        await self._upsert_setting(db, "scoring_weights", value, "贡献者 7 维权重（合计100）")
        await db.commit()
        return ContributorBoardSetting(setting_key="scoring_weights", setting_value=value)

    async def update_role_thresholds(self, db, request: UpdateRoleThresholdsRequest) -> ContributorBoardSetting:
        value = {
            "star_threshold": request.star_threshold,
            "active_threshold": request.active_threshold,
            "max_error_score": request.max_error_score,
        }
        await self._upsert_setting(db, "role_thresholds", value, "角色阈值")
        await db.commit()
        return ContributorBoardSetting(setting_key="role_thresholds", setting_value=value)

    async def recalculate_scores(self, db, repo_name: str | None = None) -> dict:
        weights = self._load_weights_setting(
            (await db.execute(
                select(ContributorBoardSettings).where(ContributorBoardSettings.setting_key == "scoring_weights")
            )).scalars().first()
        )
        count = await recalculate_all_scores(db, weights=weights, repo_name=repo_name)
        return {"recalculated": count}

    async def get_repo_list(self, db) -> list[str]:
        """获取仓库列表：合并宽表已有数据 + 同步配置中的仓库，确保空库时也能展示。"""
        repos: set[str] = set()

        rows = (await db.execute(
            select(ContributorProfile.repo_name).where(ContributorProfile.repo_name.isnot(None)).distinct()
        )).scalars().all()
        repos.update(r for r in rows if r)

        for owner, repo in self._configured_repos():
            repo_key = f"{owner}/{repo}"
            if repo_key not in repos:
                repos.add(repo_key)

        return sorted(repos)

    @staticmethod
    def _configured_repos() -> list[tuple[str, str]]:
        """从环境配置读取已配置的仓库列表。"""
        from infrastructure.core.config import settings

        repos_cfg = getattr(settings, "CONTRIBUTOR_REPOS", "").strip()
        if repos_cfg:
            parsed: list[tuple[str, str]] = []
            for entry in repos_cfg.split(","):
                entry = entry.strip()
                if "/" not in entry:
                    continue
                parts = entry.split("/", 1)
                parsed.append((parts[0].strip(), parts[1].strip()))
            if parsed:
                return parsed
        return [(settings.GITHUB_OWNER, settings.GITHUB_REPO)]

    # ══════════════════════════ 同步定时设置 ══════════════════════════

    async def get_sync_schedule(self, db) -> ContributorSyncScheduleConfig:
        setting = (
            await db.execute(
                select(ContributorBoardSettings).where(ContributorBoardSettings.setting_key == "sync_schedule")
            )
        ).scalars().first()
        value = self._load_setting(setting, ContributorSyncScheduleConfig())
        return ContributorSyncScheduleConfig(
            full_cron=value.get("full_cron", "0 2 * * *"),
            incremental_interval_minutes=value.get("incremental_interval_minutes", 360),
            enabled=value.get("enabled", True),
        )

    async def update_sync_schedule(self, db, request) -> ContributorSyncScheduleConfig:
        current = await self.get_sync_schedule(db)
        value = {
            "full_cron": request.full_cron or current.full_cron,
            "incremental_interval_minutes": request.incremental_interval_minutes or current.incremental_interval_minutes,
            "enabled": request.enabled if request.enabled is not None else current.enabled,
        }
        await self._upsert_setting(db, "sync_schedule", value, "同步定时配置")
        await db.commit()
        return ContributorSyncScheduleConfig(**value)

    # ══════════════════════════ 扩展趋势（贡献者详情页图表）══════════════════════════════

    async def get_extended_trend(
        self, db, login: str,
        period_start: date | None = None, period_end: date | None = None,
    ) -> dict:
        """贡献者详情页趋势：直接从 daily_commits / daily_prs 读真实日度数据。

        - commit_trend: 每日 commit 数 / 代码增删行数（按 DailyCommit 聚合）
        - review_trend: 空结构（当前不采集 review 日度明细，前端图会显示"无数据"）
        - score_trend:  窗口内累计贡献分数（基于到当天为止的真实聚合，按 scoring_engine 重算）

        无日度数据时（贡献者未跑过新采集器），返回空结构，前端显示空图。
        窗口：默认最近 90 天；可被 period_start / period_end 覆盖。
        """
        from contracts.schemas.contributor_board import (
            CommitTrendPoint, ReviewTrendPoint, ScoreTrendPoint, ExtendedTrendResponse,
        )

        today = date.today()
        # 默认窗口：最近 90 天
        ws = period_start or (today - timedelta(days=90))
        we = period_end or today
        if ws > we:
            ws, we = we, ws
        # 限制窗口最多 365 天
        if (we - ws).days > 365:
            ws = we - timedelta(days=365)

        # 拉取该贡献者在窗口内的所有 commit
        stmt = select(DailyCommit).where(
            DailyCommit.author == login,
            DailyCommit.data_date >= ws,
            DailyCommit.data_date <= we,
        )
        commits = (await db.execute(stmt)).scalars().all()
        # 按 data_date 分组
        by_day: dict[date, list[DailyCommit]] = {}
        for c in commits:
            d = c.data_date or (c.committed_at.date() if c.committed_at else None)
            if d is None:
                continue
            by_day.setdefault(d, []).append(c)

        weights = self._load_weights_setting(
            (await db.execute(
                select(ContributorBoardSettings).where(
                    ContributorBoardSettings.setting_key == "scoring_weights"
                )
            )).scalars().first()
        )

        # 生成连续日期序列
        all_days = [(ws + timedelta(days=i)) for i in range((we - ws).days + 1)]

        commit_points: list[CommitTrendPoint] = []
        review_points: list[ReviewTrendPoint] = []
        score_points: list[ScoreTrendPoint] = []

        cum_pr = 0
        cum_add = 0
        cum_del = 0
        cum_commit = 0
        cum_days_active = 0
        span_start: date | None = None

        for d in all_days:
            day_commits = by_day.get(d, [])
            today_commits = len(day_commits)
            today_add = sum(c.additions or 0 for c in day_commits)
            today_del = sum(c.deletions or 0 for c in day_commits)

            # merged PR 数：该贡献者当天创建的 PR（按 DailyPR.created_at 日期）
            pr_stmt = select(func.count(DailyPR.id)).where(
                DailyPR.author == login,
                DailyPR.data_date == d,
            )
            today_pr = int((await db.execute(pr_stmt)).scalar_one() or 0)

            commit_points.append(CommitTrendPoint(
                date=d.isoformat(),
                merged_pr_count=today_pr,
                code_additions=today_add,
                code_deletions=today_del,
            ))
            # review_trend 暂不采集日度 → 全 0
            review_points.append(ReviewTrendPoint(
                date=d.isoformat(),
                review_count=0,
                approval_count=0,
            ))

            # 累计
            cum_pr += today_pr
            cum_add += today_add
            cum_del += today_del
            cum_commit += today_commits
            if today_pr > 0 or today_commits > 0:
                cum_days_active += 1
                if span_start is None:
                    span_start = d
            span_days = (d - span_start).days + 1 if span_start else 1

            metrics = {
                "commit_count": cum_commit,
                "code_files": cum_add,
                "doc_files": cum_del,
                "pr_merged": cum_pr,
                "pr_open": 0,
                "pr_closed": 0,
                "active_days_90": cum_days_active,
                "span_days": span_days,
                "review_count": 0,
                "approval_count": 0,
            }
            score_points.append(ScoreTrendPoint(
                date=d.isoformat(),
                contribution_score=calculate_score(metrics, weights),
                error_score=0,
                review_count=0,
                merged_pr_count=today_pr,
                code_additions=today_add,
            ))

        return ExtendedTrendResponse(
            login=login,
            commit_trend=commit_points,
            review_trend=review_points,
            score_trend=score_points,
        )

    # ══════════════════════════ 贡献者详情页增强（宽表无日度数据）══════════════════════════════

    async def get_contributor_heatmap_data(
        self, db, login: str, repo_name: str | None = None, years: int = 1,
    ) -> dict:
        """贡献者热力图：从 DailyCommit 按 data_date 聚合每日提交数。

        返回最近 {years} 年的格子（date + commit_count + weekDay 0=Mon..6=Sun）。
        weekDay 按 ISO weekday（周一=0，周日=6）与 GitHub 风格保持一致。
        """
        from contracts.schemas.contributor_board import HeatmapDataPoint, ContributorHeatmapResponse

        today = date.today()
        ws = today - timedelta(days=365 * max(1, years))
        we = today

        stmt = (
            select(
                DailyCommit.data_date,
                func.count(DailyCommit.id),
            )
            .where(DailyCommit.author == login)
            .where(DailyCommit.data_date >= ws)
            .where(DailyCommit.data_date <= we)
            .group_by(DailyCommit.data_date)
        )
        rows = (await db.execute(stmt)).all()
        count_by_day: dict[date, int] = {r[0]: int(r[1]) for r in rows if r[0] is not None}

        points: list[HeatmapDataPoint] = []
        for i in range((we - ws).days + 1):
            d = ws + timedelta(days=i)
            n = count_by_day.get(d, 0)
            points.append(HeatmapDataPoint(
                date=d.isoformat(),
                commit_count=n,
                weekDay=d.weekday(),
            ))
        return ContributorHeatmapResponse(login=login, heatmap_data=points)

    async def get_contributor_module_distribution(
        self, db, login: str, repo_name: str | None = None,
    ) -> dict:
        """模块分布 + commit 类型分布：从 DailyCommit.files_changed 推断模块，
        从 commit message 用 classify_commit 推断类型。
        """
        from contracts.schemas.contributor_board import (
            ModuleDistributionItem, TypeDistributionItem, ContributorDistributionResponse,
        )

        stmt = select(DailyCommit).where(DailyCommit.author == login)
        commits = (await db.execute(stmt)).scalars().all()

        module_stat: dict[str, dict[str, int]] = {}
        type_count: dict[str, int] = {}

        for c in commits:
            # commit 类型
            cmsg = c.message or c.full_message or ""
            cfiles = c.files_changed or []
            ccls = classify_commit(cmsg, cfiles)
            tname = ccls.commit_type.value
            type_count[tname] = type_count.get(tname, 0) + 1

            # 模块：取 files_changed 的顶层目录（root 兜底）
            mods: set[str] = set()
            for f in cfiles:
                if not f:
                    continue
                parts = f.split("/")
                mods.add(parts[0] if len(parts) > 1 else "root")
            if not mods:
                mods.add("unknown")
            # 该 commit 的增删行均摊到每个模块（近似）
            per_mod_add = (c.additions or 0) // len(mods) if mods else 0
            per_mod_del = (c.deletions or 0) // len(mods) if mods else 0
            for m in mods:
                s = module_stat.setdefault(m, {"count": 0, "add": 0, "del": 0})
                s["count"] += 1
                s["add"] += per_mod_add
                s["del"] += per_mod_del

        modules: list[ModuleDistributionItem] = [
            ModuleDistributionItem(
                module=m,
                commit_count=v["count"],
                code_additions=v["add"],
                code_deletions=v["del"],
            )
            for m, v in sorted(module_stat.items(), key=lambda kv: -kv[1]["count"])[:15]
        ]
        types: list[TypeDistributionItem] = [
            TypeDistributionItem(commit_type=t, count=n)
            for t, n in sorted(type_count.items(), key=lambda kv: -kv[1])
        ]
        return ContributorDistributionResponse(login=login, modules=modules, type_distribution=types)

    async def get_contributor_timeline(
        self, db, login: str, limit: int = 20,
    ) -> dict:
        """提交时间线：最近 limit 条 commit，按 committed_at desc。"""
        from contracts.schemas.contributor_board import TimelineCommitItem, ContributorTimelineResponse

        stmt = (
            select(DailyCommit)
            .where(DailyCommit.author == login)
            .order_by(DailyCommit.committed_at.desc())
            .limit(limit)
        )
        commits = (await db.execute(stmt)).scalars().all()

        items: list[TimelineCommitItem] = []
        for c in commits:
            cmsg = c.message or c.full_message or ""
            cfiles = c.files_changed or []
            ccls = classify_commit(cmsg, cfiles)
            # 模块：文件首段
            mod = "root"
            if cfiles:
                parts = cfiles[0].split("/")
                mod = parts[0] if len(parts) > 1 else "root"
            items.append(TimelineCommitItem(
                commit_hash=c.sha or c.short_sha or "",
                message=cmsg[:200],
                classification=ccls.commit_type.value,
                module=mod,
                committed_at=c.committed_at,
                files_changed=len(cfiles),
                code_additions=c.additions or 0,
                code_deletions=c.deletions or 0,
            ))
        return ContributorTimelineResponse(login=login, commits=items)

    # ══════════════════════════ 内部工具 ══════════════════════════

    def _to_ranking_item(self, r: ContributorProfile, rank: int) -> ContributorRankingItem:
        pr_total = (r.pr_merged or 0) + (r.pr_open or 0) + (r.pr_closed or 0)
        merge_rate = round((r.pr_merged or 0) / pr_total, 4) if pr_total else 0.0
        return ContributorRankingItem(
            login=r.login,
            score=r.total_score or 0,
            rank=rank,
            merged_pr_count=r.pr_merged or 0,
            pending_pr_count=r.pr_open or 0,
            code_additions=r.code_files or 0,
            code_deletions=r.doc_files or 0,
            review_count=r.review_count or 0,
            approval_count=r.approval_count or 0,
            contribution_score=r.total_score or 0,
            error_score=0,
            commit_count=r.commit_count or 0,
            dynamic_quadrant=r.tier,
            role_label=r.tier,
            is_bot=False,
        )

    def _sort_key(self, sort_by: str | None):
        def _key(r: ContributorProfile):
            if sort_by == "pr_count":
                return (r.pr_merged or 0) + (r.pr_open or 0) + (r.pr_closed or 0)
            if sort_by == "review_count":
                return r.review_count or 0
            if sort_by == "merged_count":
                return r.pr_merged or 0
            if sort_by in ("lines_added", "code_files"):
                return r.code_files or 0
            if sort_by in ("lines_removed", "doc_files"):
                return r.doc_files or 0
            if sort_by == "commit_count":
                return r.commit_count or 0
            if sort_by == "active_days":
                return r.active_days_90 or 0
            return r.total_score or 0  # score 默认
        return _key

    @staticmethod
    def _type_distribution(rows: list[ContributorProfile]) -> dict[str, int]:
        # 旧字段 classification_distribution：宽表无 commit 类型明细，给出 0 结构
        return {}

    @staticmethod
    async def _last_sync_at(db, repo_name: str | None) -> datetime | None:
        stmt = select(ContributorSyncRecord.synced_at).where(
            ContributorSyncRecord.status == "completed",
            ContributorSyncRecord.synced_at.isnot(None),
        )
        if repo_name:
            stmt = stmt.where(ContributorSyncRecord.repo_name == repo_name)
        stmt = stmt.order_by(ContributorSyncRecord.synced_at.desc()).limit(1)
        return (await db.execute(stmt)).scalars().first()

    async def _contributor_identity_map(
        self, db, logins: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not logins:
            return {}
        rows = (
            await db.execute(select(Contributor).where(Contributor.login.in_(logins)))
        ).scalars().all()
        return {
            c.login: {
                "avatar_url": c.avatar_url,
                "employee_id": c.employee_id,
                "company": c.company,
                "email": c.email,
                "is_community": c.is_community,
            }
            for c in rows
        }

    @staticmethod
    def _load_weights_setting(setting_row) -> dict[str, float]:
        weights = get_default_weights()
        if setting_row is not None:
            try:
                import json
                raw = setting_row.setting_value
                if isinstance(raw, str):
                    raw = json.loads(raw)
                if isinstance(raw, dict):
                    for k in weights:
                        if k in raw:
                            weights[k] = float(raw[k])
            except Exception as e:
                logger.warning("Failed to load scoring weights: %s", e)
        return weights

    @staticmethod
    def _load_setting(setting_row, default_value: dict[str, Any]) -> dict[str, Any]:
        """从设置行加载 dict，失败则返回默认。"""
        if setting_row is not None:
            try:
                raw = setting_row.setting_value
                if isinstance(raw, str):
                    raw = json.loads(raw)
                if isinstance(raw, dict):
                    return raw
            except Exception as e:
                logger.warning("Failed to load setting: %s", e)
        return dict(default_value)

    @staticmethod
    async def _upsert_setting(db, key: str, value: dict, description: str) -> None:
        setting = (
            await db.execute(
                select(ContributorBoardSettings).where(ContributorBoardSettings.setting_key == key)
            )
        ).scalars().first()
        if setting:
            setting.setting_value = value
            setting.description = description
        else:
            db.add(ContributorBoardSettings(setting_key=key, setting_value=value, description=description))
        await db.flush()


# ══════════════════════════ 单例 ══════════════════════════

_service: ContributorBoardService | None = None


def get_contributor_board_service() -> ContributorBoardService:
    global _service
    if _service is None:
        _service = ContributorBoardService()
    return _service
