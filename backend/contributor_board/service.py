"""
贡献者看板 Service 层
"""
import asyncio
import logging
from datetime import date
from typing import Any

from contracts.schemas.contributor_board import (
    BuildOptimizationStats,
    BuildPlanListResponse,
    BuildPlanResponse,
    ClassificationSummary,
    ContributorBoardSetting,
    ContributorBoardSettingsResponse,
    ContributorDetailResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingItem,
    ContributorRankingResponse,
    ContributorResponse,
    ContributorSyncHistoryResponse,
    ContributorSyncSummary,
    ContributorSyncRecordResponse,
    ContributorSyncRequest,
    ContributorSyncResponse,
    ContributorSyncScheduleConfig,
    ContributorSyncScheduleUpdateRequest,
    ContributorTrendPoint,
    ContributorTrendResponse,
    ExtendedTrendResponse,
    ModuleHotspotResponse,
    PolicyItemResponse,
    PolicyResultResponse,
    QuadrantConfig,
    QuadrantDataResponse,
    RoleStats,
    RoleThresholdsConfig,
    ScoringWeightsConfig,
    UpdateQuadrantConfigRequest,
    UpdateRoleThresholdsRequest,
    UpdateScoringWeightsRequest,
)
from contributor_board import policy_engine
from contributor_board import role_analytics
from contributor_board.build_optimizer import BuildPlan, evaluate_build_plan
from contributor_board.classifier import ClassificationResult, classify_commit
from contributor_board.quadrant_engine import (
    QuadrantPoint,
    calculate_quadrants,
    determine_quadrant,
)
from infrastructure.db.base import SessionLocal as AsyncSessionLocal
from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorBoardSettings,
    ContributorMetrics,
    ContributorSyncRecord,
    ErrorRecord,
    ImageBuildPlan,
    QuadrantSnapshot,
)
from sqlalchemy import Integer, func
from sqlalchemy import and_, or_, select, true

logger = logging.getLogger(__name__)


class ContributorBoardService:
    """贡献者看板服务"""

    def __init__(self):
        pass

    # ---- 提交分类 ----

    @staticmethod
    def classify_commit(
            message: str,
        files_changed: list[str] | None = None,
    ) -> ClassificationResult:
        return classify_commit(message, files_changed)

    @staticmethod
    def bulk_classify(
            commits: list[dict[str, Any]]
    ) -> list[ClassificationResult]:
        return [
            classify_commit(c.get("message", ""), c.get("files_changed"))
            for c in commits
        ]

    # ---- 构建计划 ----

    @staticmethod
    def evaluate_build_plan(
        pr_number: int,
        commit_sha: str,
        commit_type: str,
        is_chip_specific: bool = False,
        chip_target: str | None = None,
        files_changed: list[str] | None = None,
    ) -> BuildPlan:
        return evaluate_build_plan(
            commit_sha=commit_sha,
            commit_type=commit_type,
            is_chip_specific=is_chip_specific,
            chip_target=chip_target,
            files_changed=files_changed,
            pr_number=pr_number,
        )

    # ---- 数据库统计查询 ----
    @staticmethod
    async def get_classification_summary_from_db(
        db,
        repo_name: str | None = None,
    ) -> ClassificationSummary:
        base_where = []
        if repo_name:
            base_where.append(CommitClassification.repo_name == repo_name)

        total_result = await db.execute(
            select(func.count(CommitClassification.id))
            .where(and_(*base_where) if base_where else true())
        )
        total = int(total_result.scalar() or 0)

        type_dist_result = await db.execute(
            select(
                CommitClassification.commit_type,
                func.count(CommitClassification.id),
            ).where(and_(*base_where) if base_where else true())
            .group_by(CommitClassification.commit_type)
        )
        type_tuples = type_dist_result.all()
        type_distribution: dict[str, int] = {k: int(v) for k, v in type_tuples}

        type_percentage: dict[str, float] = {}
        if total > 0:
            for k, v in type_distribution.items():
                type_percentage[k] = round(v / total * 100, 2)

        chip_result = await db.execute(
            select(func.sum(CommitClassification.is_chip_specific.cast(Integer)))
            .where(and_(*base_where) if base_where else true())
        )
        chip_specific_count = int(chip_result.scalar() or 0)

        module_hotspot: dict[str, int] = {}
        hotspot_stmt = (
            select(CommitClassification.modules_touched)
            .where(and_(CommitClassification.modules_touched.isnot(None), *base_where) if base_where else CommitClassification.modules_touched.isnot(None))
        )
        hotspot_result = await db.execute(hotspot_stmt)
        for row in hotspot_result.scalars():
            if isinstance(row, list):
                for mod in row:
                    module_hotspot[mod] = module_hotspot.get(mod, 0) + 1

        return ClassificationSummary(
            total_commits=total,
            type_distribution=type_distribution,
            type_percentage=type_percentage,
            chip_specific_count=chip_specific_count,
            module_hotspot=module_hotspot,
        )

    @staticmethod
    async def get_build_optimization_stats_from_db(
        db,
        repo_name: str | None = None,
    ) -> BuildOptimizationStats:
        base_where: list = []
        if repo_name:
            base_where.append(ImageBuildPlan.repo_name == repo_name)

        total_result = await db.execute(
            select(func.count(ImageBuildPlan.id)).where(and_(*base_where) if base_where else true())
        )
        total_evals = int(total_result.scalar() or 0)

        skipped_result = await db.execute(
            select(func.sum(ImageBuildPlan.build_type == "skip").cast(Integer)).where(and_(*base_where) if base_where else true())
        )
        skipped = int(skipped_result.scalar() or 0)

        reduced_result = await db.execute(
            select(func.sum(
                ImageBuildPlan.build_type.in_(["minimal_build", "nightly_only", "smart_check"])
            ).cast(Integer)).where(and_(*base_where) if base_where else true())
        )
        reduced = int(reduced_result.scalar() or 0)

        full_result = await db.execute(
            select(func.sum(ImageBuildPlan.build_type == "full_build").cast(Integer)).where(and_(*base_where) if base_where else true())
        )
        full_builds = int(full_result.scalar() or 0)

        avg_saving_result = await db.execute(
            select(func.avg(ImageBuildPlan.estimated_saving_percent)).where(and_(*base_where) if base_where else true())
        )
        avg_saving = round(float(avg_saving_result.scalar() or 0), 2)

        by_type_result = await db.execute(
            select(
                ImageBuildPlan.build_type,
                func.count(ImageBuildPlan.id),
            ).where(and_(*base_where) if base_where else true()).group_by(ImageBuildPlan.build_type)
        )
        by_type: dict[str, int] = {k: int(v) for k, v in by_type_result.all()}

        return BuildOptimizationStats(
            total_evaluations=total_evals,
            skipped_builds=skipped,
            reduced_builds=reduced,
            full_builds=full_builds,
            avg_saving_percent=avg_saving,
            by_type=by_type,
        )


    @staticmethod
    async def get_build_plans_from_db(
        db,
        build_type: str | None = None,
        commit_type: str | None = None,
        created_after: date | None = None,
        created_before: date | None = None,
        limit: int = 20,
        offset: int = 0,
        repo_name: str | None = None,
    ) -> BuildPlanListResponse:
        stmt = select(ImageBuildPlan)
        count_stmt = select(func.count(ImageBuildPlan.id))

        if repo_name:
            stmt = stmt.where(ImageBuildPlan.repo_name == repo_name)
            count_stmt = count_stmt.where(ImageBuildPlan.repo_name == repo_name)
        if build_type:
            stmt = stmt.where(ImageBuildPlan.build_type == build_type)
            count_stmt = count_stmt.where(ImageBuildPlan.build_type == build_type)
        if commit_type:
            stmt = stmt.where(ImageBuildPlan.commit_type == commit_type)
            count_stmt = count_stmt.where(ImageBuildPlan.commit_type == commit_type)
        if created_after:
            stmt = stmt.where(ImageBuildPlan.created_at >= created_after)
            count_stmt = count_stmt.where(ImageBuildPlan.created_at >= created_after)
        if created_before:
            from datetime import datetime as _dt, time
            before_end = _dt.combine(created_before, time(23, 59, 59, 999999))
            stmt = stmt.where(ImageBuildPlan.created_at <= before_end)
            count_stmt = count_stmt.where(ImageBuildPlan.created_at <= before_end)

        total = int((await db.execute(count_stmt)).scalar() or 0)

        stmt = stmt.order_by(ImageBuildPlan.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        plans = result.scalars().all()

        return BuildPlanListResponse(
            plans=[
                BuildPlanResponse(
                    id=p.id,
                    pr_number=p.pr_number,
                    commit_sha=p.commit_sha,
                    commit_type=p.commit_type or "",
                    build_type=p.build_type,
                    chips=p.chips or [],
                    os_list=p.os_list or [],
                    cache_strategy=p.cache_strategy,
                    estimated_saving_percent=p.estimated_saving_percent or 0,
                    actual_execution=p.actual_execution or False,
                    reasoning=p.reasoning,
                    files_changed=p.files_changed or [],
                    created_at=p.created_at,
                )
                for p in plans
            ],
            total=total,
        )

    @staticmethod
    async def get_build_plan_filter_options(
        db,
        repo_name: str | None = None,
    ) -> dict:
        base_where = [
            ImageBuildPlan.commit_type.isnot(None),
            ImageBuildPlan.commit_type != ""
        ]
        if repo_name:
            base_where.append(ImageBuildPlan.repo_name == repo_name)

        commit_types_result = await db.execute(
            select(ImageBuildPlan.commit_type)
            .where(and_(*base_where))
            .distinct()
            .order_by(ImageBuildPlan.commit_type)
        )
        commit_types = [row[0] for row in commit_types_result.all()]

        build_where = []
        if repo_name:
            build_where.append(ImageBuildPlan.repo_name == repo_name)

        build_types_result = await db.execute(
            select(ImageBuildPlan.build_type)
            .where(and_(*build_where) if build_where else true())
            .distinct()
            .order_by(ImageBuildPlan.build_type)
        )
        build_types = [row[0] for row in build_types_result.all()]

        return {"commit_types": commit_types, "build_types": build_types}

    @staticmethod
    async def get_overview_stats(db) -> ContributorOverviewStats:
        contributors_result = await db.execute(
            select(func.count(Contributor.id))
        )
        total_contributors = int(contributors_result.scalar() or 0)

        community_result = await db.execute(
            select(func.sum(Contributor.is_community.cast(Integer)))
        )
        community_contributors = int(community_result.scalar() or 0)

        quadrant_result = await db.execute(
            select(
                ContributorMetrics.quadrant,
                func.count(ContributorMetrics.id),
            ).group_by(ContributorMetrics.quadrant)
        )
        quadrant_distribution: dict[str, int] = {k: int(v) for k, v in quadrant_result.all()}

        top_result = await db.execute(
            select(
                Contributor.login,
                Contributor.avatar_url,
                ContributorMetrics.contribution_score,
                ContributorMetrics.merge_count,
                ContributorMetrics.review_count,
                ContributorMetrics.approval_count,
                ContributorMetrics.quadrant,
            )
            .outerjoin(ContributorMetrics, Contributor.id == ContributorMetrics.contributor_id)
            .order_by(ContributorMetrics.contribution_score.desc())
            .limit(10)
        )
        top_rows = top_result.all()
        top_contributors: list[ContributorRankingItem] = [
            ContributorRankingItem(
                login=r[0],
                avatar_url=r[1],
                score=float(r[2] or 0),
                rank=i + 1,
                merged_pr_count=int(r[3] or 0),
                review_count=int(r[4] or 0),
                approval_count=int(r[5] or 0),
                contribution_score=float(r[2] or 0),
                quadrant=r[6] or None,
            )
            for i, r in enumerate(top_rows)
        ]

        last_sync_result = await db.execute(
            select(func.max(CommitClassification.created_at))
        )
        last_synced = last_sync_result.scalar()

        classification_summary = await ContributorBoardService.get_classification_summary_from_db(db)
        build_stats = await ContributorBoardService.get_build_optimization_stats_from_db(db)

        return ContributorOverviewStats(
            total_contributors=total_contributors,
            community_contributors=community_contributors,
            total_commits_classified=classification_summary.total_commits,
            classification_distribution=classification_summary.type_distribution,
            quadrant_distribution=quadrant_distribution,
            build_optimization_stats=build_stats,
            top_contributors=top_contributors,
            last_synced_at=last_synced,
        )

    # ---- 贡献者列表查询 ----

    @staticmethod
    async def get_contributors(
        db,
        filter: ContributorListFilter | None = None,
    ) -> ContributorListResponse:
        if filter is None:
            filter = ContributorListFilter()

        base_stmt = select(Contributor)
        count_stmt = select(func.count(Contributor.id))

        if filter.role_type:
            base_stmt = base_stmt.where(
                Contributor.role_types.contains([filter.role_type])
            )
            count_stmt = count_stmt.where(
                Contributor.role_types.contains([filter.role_type])
            )

        if filter.search:
            search_pattern = f"%{filter.search}%"
            base_stmt = base_stmt.where(
                or_(
                    Contributor.login.ilike(search_pattern),
                    Contributor.name.ilike(search_pattern),
                )
            )
            count_stmt = count_stmt.where(
                or_(
                    Contributor.login.ilike(search_pattern),
                    Contributor.name.ilike(search_pattern),
                )
            )

        total = int((await db.execute(count_stmt)).scalar() or 0)

        base_stmt = base_stmt.order_by(Contributor.last_contribution_at.desc()).limit(filter.limit).offset(filter.offset)
        result = await db.execute(base_stmt)
        contributors = result.scalars().all()

        return ContributorListResponse(
            contributors=[
                ContributorResponse.model_validate(c)
                for c in contributors
            ],
            total=total,
        )

    @staticmethod
    async def get_contributor_detail(db, login: str) -> ContributorDetailResponse | None:
        stmt = (
            select(Contributor, ContributorMetrics)
            .outerjoin(
                ContributorMetrics,
                and_(
                    Contributor.id == ContributorMetrics.contributor_id,
                    ContributorMetrics.period_end == ContributorMetrics.period_end,
                ),
            )
            .where(Contributor.login == login)
            .order_by(ContributorMetrics.updated_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.one_or_none()

        if not row:
            return None

        contributor, metrics = row
        return ContributorDetailResponse(
            id=contributor.id,
            github_id=contributor.github_id,
            login=contributor.login,
            avatar_url=contributor.avatar_url,
            email=contributor.email,
            name=contributor.name,
            is_community=contributor.is_community,
            role_types=contributor.role_types,
            company=contributor.company,
            employee_id=contributor.employee_id,
            first_contribution_at=contributor.first_contribution_at,
            last_contribution_at=contributor.last_contribution_at,
            created_at=contributor.created_at,
            updated_at=contributor.updated_at,
            merged_pr_count=int(metrics.merged_pr_count or 0) if metrics else 0,
            pending_pr_count=int(metrics.pending_pr_count or 0) if metrics else 0,
            code_additions=int(metrics.code_additions or 0) if metrics else 0,
            code_deletions=int(metrics.code_deletions or 0) if metrics else 0,
            review_count=int(metrics.review_count or 0) if metrics else 0,
            approval_count=int(metrics.approval_count or 0) if metrics else 0,
            contribution_score=float(metrics.contribution_score or 0) if metrics else 0,
            error_score=float(metrics.error_score or 0) if metrics else 0,
            quadrant=metrics.quadrant if metrics else None,
        )

    # ---- 四象限数据 ----
    @staticmethod
    async def get_quadrant_data(
        db,
        repo_name: str | None = None,
        y_axis_type: str | None = None,
    ) -> QuadrantDataResponse:
        # 从设置中读取 Y 轴类型，API 传入优先级高于设置
        if not y_axis_type:
            y_axis_type = await ContributorBoardService._get_quadrant_y_axis(db)

        conditions = []
        if repo_name:
            conditions.append(ContributorMetrics.repo_name == repo_name)

        # 按 contributor 聚合（取最新周期数据或总和），避免同一人出现多次
        agg_stmt = (
            select(
                Contributor.login,
                Contributor.avatar_url,
                func.coalesce(func.sum(ContributorMetrics.contribution_score), 0).label("contribution_score"),
                func.coalesce(func.sum(ContributorMetrics.error_score), 0).label("error_score"),
                func.coalesce(func.sum(ContributorMetrics.review_count), 0).label("review_count"),
                func.coalesce(func.sum(ContributorMetrics.merged_pr_count), 0).label("merged_pr_count"),
                func.coalesce(func.sum(ContributorMetrics.code_additions), 0).label("code_additions"),
                func.coalesce(func.max(ContributorMetrics.quadrant), "").label("role_label"),
            )
            .join(ContributorMetrics, Contributor.id == ContributorMetrics.contributor_id)
        )

        if conditions:
            agg_stmt = agg_stmt.where(and_(*conditions))

        agg_stmt = agg_stmt.group_by(Contributor.login, Contributor.avatar_url)
        result = await db.execute(agg_stmt)
        rows = result.all()

        contributors_metrics: list[dict[str, Any]] = []
        for r in rows:
            contributors_metrics.append({
                "login": r[0],
                "avatar_url": r[1],
                "contribution_score": float(r[2]),
                "error_score": float(r[3]),
                "review_count": int(r[4]),
                "merged_pr_count": int(r[5]),
                "code_additions": int(r[6]),
                "role_label": r[7],
                "is_community": False,
            })

        # 去重：同一 login 取 contribution_score 最高的
        deduped: dict[str, dict[str, Any]] = {}
        for m in contributors_metrics:
            login = m["login"]
            if login not in deduped or m["contribution_score"] > deduped[login]["contribution_score"]:
                deduped[login] = m
        contributors_metrics = list(deduped.values())

        # 只保留有贡献的记录
        contributors_metrics = [m for m in contributors_metrics if m["contribution_score"] > 0]

        y_axis_map = {
            "error_score": "error_score",
            "review_count": "review_count",
            "merged_pr_count": "merged_pr_count",
            "code_additions": "code_additions",
        }
        y_key = y_axis_map.get(y_axis_type, "error_score")

        points, quad_x, quad_y = calculate_quadrants(contributors_metrics, y_axis_type=y_key)

        quadrant_counts: dict[str, int] = {}
        for p in points:
            quadrant_counts[p.quadrant] = quadrant_counts.get(p.quadrant, 0) + 1

        # 构建 points 响应，保留原始字段名兼容前端
        resp_points = []
        for p in points:
            m = deduped.get(p.login, {})
            resp_points.append({
                "login": p.login,
                "avatar_url": p.avatar_url,
                "contribution_score": p.x_value,
                "error_score": p.y_value if y_key == "error_score" else m.get("error_score", 0),
                "y_axis_type": y_key,
                "y_value": p.y_value,
                "quadrant": p.quadrant,
                "merged_pr_count": p.merged_pr_count,
                "is_community": p.is_community,
                "role_label": m.get("role_label", ""),
            })

        return QuadrantDataResponse(
            points=resp_points,
            quad_x=quad_x,
            quad_y=quad_y,
            y_axis_type=y_key,
            repo_name=repo_name,
            period_start=None,
            period_end=None,
            total_count=len(resp_points),
            quadrant_counts=quadrant_counts,
        )

    # ---- 排行榜 ----

    @staticmethod
    async def _build_aggregated_metrics(
        db, repo_name: str | None = None
    ) -> list[dict[str, Any]]:
        """聚合所有贡献者指标（无时间过滤，全量统计）"""
        stmt = (
            select(
                Contributor.login,
                Contributor.avatar_url,
                Contributor.employee_id,
                func.sum(ContributorMetrics.contribution_score).label('contribution_score'),
                func.sum(ContributorMetrics.merged_pr_count).label('merged_pr_count'),
                func.sum(ContributorMetrics.pending_pr_count).label('pending_pr_count'),
                func.sum(ContributorMetrics.code_additions).label('code_additions'),
                func.sum(ContributorMetrics.code_deletions).label('code_deletions'),
                func.sum(ContributorMetrics.review_count).label('review_count'),
                func.sum(ContributorMetrics.approval_count).label('approval_count'),
                func.sum(ContributorMetrics.error_score).label('error_score'),
                func.sum(ContributorMetrics.commit_count).label('commit_count'),
                func.max(ContributorMetrics.quadrant).label('role_label'),
            )
            .join(ContributorMetrics, Contributor.id == ContributorMetrics.contributor_id)
        )
        if repo_name:
            stmt = stmt.where(ContributorMetrics.repo_name == repo_name)
        stmt = stmt.group_by(Contributor.login, Contributor.avatar_url, Contributor.employee_id)

        result = await db.execute(stmt)
        rows = result.all()

        return [
            {
                "login": r[0], "avatar_url": r[1], "employee_id": r[2],
                "contribution_score": float(r[3] or 0),
                "merged_pr_count": int(r[4] or 0),
                "pending_pr_count": int(r[5] or 0),
                "code_additions": int(r[6] or 0),
                "code_deletions": int(r[7] or 0),
                "review_count": int(r[8] or 0),
                "approval_count": int(r[9] or 0),
                "error_score": float(r[10] or 0),
                "commit_count": int(r[11] or 0),
                "role_label": r[12],
                "is_community": False,
            }
            for r in rows
        ]

    @staticmethod
    async def _get_quadrant_y_axis(db) -> str:
        """从设置中读取当前四象限 Y 轴类型"""
        result = await db.execute(
            select(ContributorBoardSettings.setting_value).where(
                ContributorBoardSettings.setting_key == "quadrant_y_axis"
            )
        )
        row = result.scalar_one_or_none()
        if row and isinstance(row, dict):
            return row.get("y_axis_type", "review_count")
        return "review_count"

    @staticmethod
    async def _compute_quadrants_for_metrics(
        metrics: list[dict[str, Any]], db
    ) -> dict[str, str]:
        """基于设置中的 Y 轴类型计算动态四象限，返回 login → quadrant 映射"""
        y_axis_type = await ContributorBoardService._get_quadrant_y_axis(db)
        axis_map = {
            "error_score": "error_score",
            "review_count": "review_count",
            "merged_pr_count": "merged_pr_count",
            "code_additions": "code_additions",
        }
        y_key = axis_map.get(y_axis_type, "review_count")
        points, _, _ = calculate_quadrants(metrics, y_axis_type=y_key)
        return {p.login: p.quadrant for p in points}

    @staticmethod
    async def get_ranking(
        db,
        role_type: str = "developer",
        repo_name: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
        quadrant: str | None = None,
        y_axis_type: str | None = None,
    ) -> ContributorRankingResponse:
        agg_columns: dict[str, Any] = {
            "score": "contribution_score",
            "merged_pr_count": "merged_pr_count",
            "pending_pr_count": "pending_pr_count",
            "commit_count": "commit_count",
            "code_additions": "code_additions",
            "code_deletions": "code_deletions",
            "review_count": "review_count",
            "approval_count": "approval_count",
            "error_score": "error_score",
            "login": "login",
        }

        # 获取全量聚合数据
        all_metrics = await ContributorBoardService._build_aggregated_metrics(db, repo_name=repo_name)

        # 计算动态四象限（配置驱动，支持外部指定 Y 轴类型）
        if y_axis_type:
            axis_map = {
                "error_score": "error_score",
                "review_count": "review_count",
                "merged_pr_count": "merged_pr_count",
                "code_additions": "code_additions",
            }
            y_key = axis_map.get(y_axis_type, "error_score")
            from contributor_board.quadrant_engine import calculate_quadrants
            points, _, _ = calculate_quadrants(all_metrics, y_axis_type=y_key)
            quadrant_map = {p.login: p.quadrant for p in points}
        else:
            quadrant_map = await ContributorBoardService._compute_quadrants_for_metrics(all_metrics, db)

        # 四象限过滤
        if quadrant:
            all_metrics = [
                m for m in all_metrics
                if quadrant_map.get(m["login"]) == quadrant
            ]

        # 按角色类型排序
        if role_type == "reviewer":
            default_sort = "review_count"
        elif role_type == "maintainer":
            default_sort = "merged_pr_count"
        else:
            default_sort = "score"

        sort_key = (sort_by or default_sort)
        sort_field = agg_columns.get(sort_key, default_sort)
        reverse = sort_order == "desc"

        sorted_metrics = sorted(all_metrics, key=lambda m: m.get(sort_field, 0), reverse=reverse)

        # 应用分页
        paginated = sorted_metrics[offset:offset + limit]
        total = len(sorted_metrics)

        rankings: list[ContributorRankingItem] = [
            ContributorRankingItem(
                login=m["login"],
                avatar_url=m["avatar_url"],
                employee_id=m["employee_id"],
                score=m["contribution_score"],
                rank=i + 1 + offset,
                merged_pr_count=m["merged_pr_count"],
                pending_pr_count=m["pending_pr_count"],
                code_additions=m["code_additions"],
                code_deletions=m["code_deletions"],
                review_count=m["review_count"],
                approval_count=m["approval_count"],
                contribution_score=m["contribution_score"],
                error_score=m["error_score"],
                commit_count=m.get("commit_count", 0),
                dynamic_quadrant=quadrant_map.get(m["login"]),
                role_label=m["role_label"],
            )
            for i, m in enumerate(paginated)
        ]

        return ContributorRankingResponse(
            role_type=role_type,
            rankings=rankings,
            total=total,
            repo_name=repo_name,
        )

    # ---- 设置管理 ----

    @staticmethod
    async def _get_scoring_weights(db) -> ScoringWeightsConfig:
        """内部方法：读取当前评分权重配置"""
        result = await db.execute(
            select(ContributorBoardSettings).where(
                ContributorBoardSettings.setting_key == "scoring_weights"
            )
        )
        existing = result.scalar_one_or_none()
        if existing and existing.setting_value:
            return ScoringWeightsConfig(**existing.setting_value)
        return ScoringWeightsConfig()

    @staticmethod
    async def get_settings(db) -> ContributorBoardSettingsResponse:
        result = await db.execute(
            select(ContributorBoardSettings).where(
                ContributorBoardSettings.setting_key.in_([
                    "quadrant_y_axis", "scoring_weights", "role_thresholds"
                ])
            )
        )
        settings = result.scalars().all()
        setting_map = {s.setting_key: s.setting_value for s in settings}

        y_axis = setting_map.get("quadrant_y_axis", {"y_axis_type": "review_count"})
        weights = setting_map.get("scoring_weights", {
            "merged_pr_weight": 10.0, "code_line_weight": 0.5,
            "review_weight": 3.0, "approval_weight": 5.0, "core_module_weight": 8.0
        })
        thresholds = setting_map.get("role_thresholds", {
            "star_threshold": 30, "active_threshold": 15, "max_error_score": 2
        })

        return ContributorBoardSettingsResponse(
            quadrant_config=QuadrantConfig(**y_axis),
            scoring_weights=ScoringWeightsConfig(**weights),
            role_thresholds=RoleThresholdsConfig(**thresholds),
        )

    @staticmethod
    async def update_setting(
        db, key: str, value: dict, description: str | None = None
    ) -> ContributorBoardSetting:
        result = await db.execute(
            select(ContributorBoardSettings).where(ContributorBoardSettings.setting_key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.setting_value = value
            if description is not None:
                existing.description = description
        else:
            existing = ContributorBoardSettings(
                setting_key=key, setting_value=value, description=description
            )
            db.add(existing)
        await db.flush()
        await db.refresh(existing)
        return ContributorBoardSetting(
            setting_key=existing.setting_key,
            setting_value=existing.setting_value,
            description=existing.description,
        )

    @staticmethod
    async def update_quadrant_config(
        db, request: UpdateQuadrantConfigRequest
    ) -> ContributorBoardSetting:
        return await ContributorBoardService.update_setting(
            db, "quadrant_y_axis", {"y_axis_type": request.y_axis_type},
            "四象限Y轴指标类型"
        )

    @staticmethod
    async def update_scoring_weights(
        db, request: UpdateScoringWeightsRequest
    ) -> ContributorBoardSetting:
        return await ContributorBoardService.update_setting(
            db, "scoring_weights", request.model_dump(), "贡献分数计算权重"
        )

    @staticmethod
    async def recalculate_scores(
        db, repo_name: str | None = None
    ) -> dict[str, Any]:
        """权重变更后，读取最新权重配置并批量重算所有贡献者分数。"""
        from contributor_board.scoring_engine import recalculate_all_scores, calculate_contribution_score

        # 读取当前权重配置
        weights = await ContributorBoardService._get_scoring_weights(db)
        weights_dict: dict[str, float] = {
            "merged_pr_weight": float(weights.merged_pr_weight),
            "code_line_weight": float(weights.code_line_weight),
            "review_weight": float(weights.review_weight),
            "approval_weight": float(weights.approval_weight),
            "core_module_weight": float(weights.core_module_weight),
            "files_changed_weight": float(weights.files_changed_weight),
            "bugfix_weight": float(weights.bugfix_weight),
            "feature_weight": float(weights.feature_weight),
            "active_duration_weight": float(weights.active_duration_weight),
        }

        count = await recalculate_all_scores(db, weights_dict, repo_name)
        return {"recalculated_count": count}

    @staticmethod
    async def update_role_thresholds(
        db, request: UpdateRoleThresholdsRequest
    ) -> ContributorBoardSetting:
        return await ContributorBoardService.update_setting(
            db, "role_thresholds", request.model_dump(), "静态角色标签阈值"
        )

    @staticmethod
    async def get_sync_schedule(
        db,
    ) -> ContributorSyncScheduleConfig:
        """获取定时同步设置"""
        result = await db.execute(
            select(ContributorBoardSettings).where(
                ContributorBoardSettings.setting_key == "sync_schedule"
            )
        )
        existing = result.scalar_one_or_none()
        if existing and existing.setting_value:
            return ContributorSyncScheduleConfig(**existing.setting_value)
        return ContributorSyncScheduleConfig()

    @staticmethod
    async def update_sync_schedule(
        db, request: ContributorSyncScheduleUpdateRequest
    ) -> ContributorSyncScheduleConfig:
        """更新定时同步设置"""
        current = await ContributorBoardService.get_sync_schedule(db)
        updates = request.model_dump(exclude_none=True)
        merged = {**current.model_dump(), **updates}

        await ContributorBoardService.update_setting(
            db, "sync_schedule", merged, "定时同步配置"
        )

        # 写入 ProjectDashboardConfig 供 Scheduler 读取
        try:
            from infrastructure.persistence.models import ProjectDashboardConfig

            cfg_result = await db.execute(
                select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == "contributor_sync_schedule"
                )
            )
            cfg_row = cfg_result.scalar_one_or_none()
            if cfg_row:
                cfg_row.config_value = merged
            else:
                db.add(ProjectDashboardConfig(
                    config_key="contributor_sync_schedule",
                    config_value=merged,
                    description="Contributor sync schedule configuration",
                ))
        except Exception as e:
            logger.warning(f"Failed to write sync schedule to ProjectDashboardConfig: {e}")

        return ContributorSyncScheduleConfig(**merged)

    # ---- 趋势数据 ----

    @staticmethod
    async def _fetch_contributor_metrics(
        db,
        login: str,
        period_start: date | None = None,
        period_end: date | None = None,
    ):
        """查询贡献者 metrics，同周期只取最新一条（防重复插入导致的多行）。"""
        # 子查询：每个 (contributor_login, period_start, period_end) 取最大 id
        sub = (
            select(ContributorMetrics.id)
            .where(ContributorMetrics.contributor_login == login)
            .group_by(
                ContributorMetrics.contributor_login,
                ContributorMetrics.period_start,
                ContributorMetrics.period_end,
            )
            .having(ContributorMetrics.id == func.max(ContributorMetrics.id))
            .subquery()
        )

        stmt = (
            select(ContributorMetrics)
            .where(ContributorMetrics.contributor_login == login)
            .where(ContributorMetrics.id.in_(select(sub.c.id)))
            .order_by(ContributorMetrics.period_start)
        )

        if period_start:
            stmt = stmt.where(ContributorMetrics.period_start >= period_start)
        if period_end:
            stmt = stmt.where(ContributorMetrics.period_end <= period_end)

        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_contributor_trend(
        self,
        db,
        login: str,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> ContributorTrendResponse:
        metrics = await self._fetch_contributor_metrics(
            db, login, period_start, period_end
        )

        points: list[ContributorTrendPoint] = [
            ContributorTrendPoint(
                date=m.period_start,
                merged_pr_count=int(m.merged_pr_count or 0),
                code_additions=int(m.code_additions or 0),
                code_deletions=int(m.code_deletions or 0),
                review_count=int(m.review_count or 0),
                contribution_score=float(m.contribution_score or 0),
                error_score=float(m.error_score or 0),
            )
            for m in metrics
        ]

        return ContributorTrendResponse(
            login=login,
            points=points,
            period_start=period_start,
            period_end=period_end,
        )

    # ---- 角色统计 ----

    @staticmethod
    async def get_role_stats(db, repo_name: str | None = None) -> RoleStats:
        return await role_analytics.get_role_stats(db, repo_name=repo_name)

    @staticmethod
    async def get_module_hotspot(db, repo_name: str | None = None, period_start: date | None = None, period_end: date | None = None) -> ModuleHotspotResponse:
        return await role_analytics.get_module_hotspot(db, repo_name=repo_name, period_start=period_start, period_end=period_end)

    @staticmethod
    async def get_role_ranking(
        db,
        role_type: str = "developer",
        repo_name: str | None = None,
        limit: int = 10,
    ) -> ContributorRankingResponse:
        return await role_analytics.get_role_ranking(db, role_type=role_type, repo_name=repo_name, limit=limit)

    # ---- 仓库列表 ----

    @staticmethod
    async def get_repo_list(db) -> list[str]:
        """获取可选仓库列表。

        优先从已有数据中获取 repo_name，首次部署时 fallback
        到配置中的仓库列表（GITHUB_OWNER/GITHUB_REPO 或
        CONTRIBUTOR_REPOS），确保用户能看到可选仓库。
        """
        result = await db.execute(
            select(ContributorMetrics.repo_name)
            .where(ContributorMetrics.repo_name.isnot(None))
            .distinct()
            .order_by(ContributorMetrics.repo_name)
        )
        repos = sorted({r[0] for r in result.all()})

        # 首次部署时 contributor_metrics 表为空，fallback 到配置中的仓库
        if not repos:
            from infrastructure.core.config import settings

            owner = (settings.GITHUB_OWNER or "").strip()
            repo = (settings.GITHUB_REPO or "").strip()
            if owner and repo:
                repos = [f"{owner}/{repo}"]

            # 同时解析 CONTRIBUTOR_REPOS 环境变量
            repos_cfg = (settings.CONTRIBUTOR_REPOS or "").strip()
            if repos_cfg:
                for entry in repos_cfg.split(","):
                    entry = entry.strip()
                    if "/" in entry and entry not in repos:
                        repos.append(entry)

        return repos

    # ---- 策略引擎 ----

    @staticmethod
    async def get_contributor_policy(
        db,
        login: str,
    ) -> PolicyResultResponse | None:
        result = await policy_engine.evaluate_policy_from_db(db, login)
        if result is None:
            return None

        return PolicyResultResponse(
            login=result.login,
            quadrant=result.quadrant,
            contribution_score=result.contribution_score,
            error_score=result.error_score,
            policies=[
                PolicyItemResponse(
                    action=p.action,
                    severity=p.severity,
                    execution=p.execution,
                    description=p.description,
                    reason=p.reason,
                    impact_level=p.impact_level,
                )
                for p in result.policies
            ],
            has_critical=result.has_critical,
            has_warning=result.has_warning,
            evaluated_at=result.evaluated_at,
            auto_count=len(result.auto_policies),
            suggestion_count=len(result.suggestion_policies),
            alert_count=len(result.alert_policies),
        )

    # ---- 扩展趋势数据 ----

    @staticmethod
    async def get_extended_trend(
        db,
        login: str,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> ExtendedTrendResponse:
        from sqlalchemy import select, func as sa_func, case as sa_case

        from contributor_board.policy_engine import get_trend_data
        from contracts.schemas.contributor_board import (
            CommitTrendPoint,
            ReviewTrendPoint,
            ScoreTrendPoint,
        )
        from infrastructure.persistence.models import CommitClassification

        # ContributorReview model may not exist in all deployments
        try:
            from infrastructure.persistence.models import ContributorReview  # noqa: F401
            has_review_model = True
        except ImportError:
            ContributorReview = None  # type: ignore[assignment,misc]
            has_review_model = False

        metrics = await ContributorBoardService._fetch_contributor_metrics(
            db, login, period_start, period_end
        )

        # Monthly score trend from ContributorMetrics (unchanged)
        trend = get_trend_data(login, list(metrics))

        # Day-level commit trend from CommitClassification
        commit_stmt = (
            select(
                sa_func.date(CommitClassification.created_at).label("day"),
                sa_func.count(CommitClassification.id).label("commit_count"),
                sa_func.count(
                    sa_case(
                        (CommitClassification.commit_type == "Feature", 1)
                    )
                ).label("feature_count"),
                sa_func.count(
                    sa_case(
                        (CommitClassification.commit_type == "BugFix", 1)
                    )
                ).label("bugfix_count"),
            )
            .where(CommitClassification.contributor_login == login)
            .group_by(sa_func.date(CommitClassification.created_at))
            .order_by(sa_func.date(CommitClassification.created_at))
        )
        if period_start:
            commit_stmt = commit_stmt.where(CommitClassification.created_at >= period_start)
        if period_end:
            commit_stmt = commit_stmt.where(CommitClassification.created_at <= period_end)

        commit_result = await db.execute(commit_stmt)
        commit_rows = commit_result.all()

        commit_trend: list[CommitTrendPoint] = [
            CommitTrendPoint(
                date=str(r[0]),
                merged_pr_count=r[1],
                code_additions=r[2],
                code_deletions=r[3],
            )
            for r in commit_rows
        ]

        # Day-level review trend - fall back to monthly data if model doesn't exist
        review_trend_list: list[dict] = []
        if has_review_model:
            try:
                review_stmt = (
                    select(
                        sa_func.date(ContributorReview.reviewed_at).label("day"),
                        sa_func.count(ContributorReview.id).label("review_count"),
                        sa_func.count(
                            sa_case(
                                (ContributorReview.review_state == "APPROVED", 1)
                            )
                        ).label("approval_count"),
                    )
                    .where(ContributorReview.contributor_login == login)
                    .group_by(sa_func.date(ContributorReview.reviewed_at))
                    .order_by(sa_func.date(ContributorReview.reviewed_at))
                )
                if period_start:
                    review_stmt = review_stmt.where(ContributorReview.reviewed_at >= period_start)
                if period_end:
                    review_stmt = review_stmt.where(ContributorReview.reviewed_at <= period_end)

                review_result = await db.execute(review_stmt)
                review_rows = review_result.all()
                review_trend_list = [
                    {
                        "date": str(r[0]),
                        "review_count": int(r[1]),
                        "approval_count": int(r[2]),
                    }
                    for r in review_rows
                ]
            except Exception:
                review_trend_list = trend.get("review_trend", [])
        else:
            review_trend_list = trend.get("review_trend", [])

        return ExtendedTrendResponse(
            login=login,
            commit_trend=commit_trend,
            review_trend=[ReviewTrendPoint(**p) for p in review_trend_list],
            score_trend=[
                ScoreTrendPoint(**p) for p in trend.get("score_trend", [])
            ],
        )

    # ---- 同步接口 ----

    @staticmethod
    async def trigger_sync(
        db,
        since_date: date | None = None,
        until_date: date | None = None,
        full_resync: bool = False,
        repo_name: str | None = None,
        trigger_source: str = "manual",
    ) -> ContributorSyncResponse:
        """
        触发异步同步任务（非阻塞）

        Args:
            trigger_source: 触发来源 ('manual', 'scheduled_full', 'scheduled_incremental')

        1. 检查是否有正在运行的同类任务（防重复）
        2. 创建任务记录（pending 状态）
        3. 立即返回 record_id，后台 asyncio task 执行同步
        """
        from datetime import UTC, datetime, time
        from infrastructure.tasks.background_task import BackgroundTaskTracker, TaskStatus

        try:
            # 确保任务追踪表存在
            await BackgroundTaskTracker.ensure_table(db)

            # 检查是否有正在运行的同类任务（防重复提交）
            # 运行超过 6 小时的任务视为超时，自动清理
            active = await BackgroundTaskTracker.get_active_task(db, "contributor_sync", repo_name)
            if active:
                from datetime import timedelta as td
                created = active.get("created_at")
                if created and (datetime.now(UTC) - created) > td(hours=6):
                    logger.warning("Found stale running task id=%s, auto-clearing", active["id"])
                    await BackgroundTaskTracker.update_status(
                        active["id"], TaskStatus.FAILED, "Task timeout (auto-cleared)"
                    )
                    active = None
                else:
                    raise ValueError(
                        f"同步任务已在运行中 (task_id={active['id']})，请稍后再试"
                    )

            if full_resync:
                since_dt = None
            elif since_date is None:
                since_dt = datetime(2024, 1, 1, tzinfo=UTC)
            else:
                since_dt = datetime.combine(since_date, time.min, tzinfo=UTC)

            until_dt: datetime | None = None
            if until_date:
                until_dt = datetime.combine(until_date, time(23, 59, 59), tzinfo=UTC)

            # 同步类型判断
            if full_resync:
                sync_type = "full"
            elif since_date and until_date:
                sync_type = "range"
            elif since_date:
                sync_type = "incremental"
            else:
                sync_type = "full"

            # 创建 ContributorSyncRecord (pending 状态)
            record = ContributorSyncRecord(
                repo_name=repo_name,
                sync_type=sync_type,
                status=TaskStatus.PENDING,
                since_date=since_date,
                until_date=until_date,
                trigger_source=trigger_source,
                progress_percent=0,
                progress_message="任务已创建，等待执行",
            )
            db.add(record)
            await db.flush()
            record_id = record.id

            logger.info(
                "Created sync record %d for repo %s (type=%s)",
                record_id, repo_name, sync_type
            )

            # 同时创建 BackgroundTaskTracker 记录
            task_params = {
                "sync_record_id": record_id,
                "repo_name": repo_name,
                "since_date": since_date.isoformat() if since_date else None,
                "until_date": until_date.isoformat() if until_date else None,
            }

            task_id = await BackgroundTaskTracker.create_and_run(
                task_type="contributor_sync",
                task_func=ContributorBoardService._run_contributor_sync,
                task_params=task_params,
                repo_name=repo_name,
                db=db,
            )

            logger.info(
                "Scheduled background task %d for sync record %d",
                task_id, record_id
            )

            return ContributorSyncResponse(
                record_id=record_id,
            )

        except ValueError:
            # 重新抛出业务异常（如任务已在运行）
            raise
        except Exception as e:
            logger.error(
                "Failed to trigger sync for repo %s: %s",
                repo_name, e, exc_info=True
            )
            # 回滚数据库事务
            try:
                await db.rollback()
            except Exception:
                pass
            raise

    @staticmethod
    async def _run_contributor_sync(
        task_params: dict[str, Any],
        tracker: Any,  # BackgroundTaskTracker instance
    ) -> dict[str, Any]:
        """后台执行的同步任务函数 — 由 BackgroundTaskTracker.create_and_run 调用"""
        from datetime import UTC, datetime
        from collector.contributor_sync import sync_contributors
        from infrastructure.db.base import SessionLocal as AsyncSessionLocal
        from sqlalchemy import update as sa_update

        logger.info("_run_contributor_sync started, params: %s", task_params)

        sync_record_id = task_params["sync_record_id"]
        repo_name = task_params.get("repo_name")
        since_date_str = task_params.get("since_date")
        until_date_str = task_params.get("until_date")

        since_dt: datetime | None = None
        if since_date_str:
            since_dt = datetime.fromisoformat(since_date_str).replace(hour=0, minute=0, second=0, tzinfo=UTC)
        else:
            since_dt = datetime(2024, 1, 1, tzinfo=UTC)

        until_dt: datetime | None = None
        if until_date_str:
            until_dt = datetime.fromisoformat(until_date_str).replace(hour=23, minute=59, second=59, tzinfo=UTC)

        # 更新 ContributorSyncRecord 为 running
        async with AsyncSessionLocal() as db:
            await db.execute(
                sa_update(ContributorSyncRecord)
                .where(ContributorSyncRecord.id == sync_record_id)
                .values(
                    status="running",
                    progress_percent=0,
                    progress_message="正在初始化同步...",
                    started_at=func.now(),
                )
            )
            await db.commit()

        async def progress_callback(pct: int, msg: str):
            await tracker.update_progress(pct, msg)
            # 检查任务是否被取消
            if await tracker.is_cancelled():
                raise Exception("任务被用户取消")
            # 同时更新 ContributorSyncRecord
            try:
                async with AsyncSessionLocal() as update_db:
                    await update_db.execute(
                        sa_update(ContributorSyncRecord)
                        .where(ContributorSyncRecord.id == sync_record_id)
                        .values(progress_percent=pct, progress_message=msg)
                    )
                    await update_db.commit()
            except Exception as progress_err:
                logger.debug(
                    "Failed to update sync progress for record %d: %s",
                    sync_record_id, progress_err,
                )

        try:
            logger.info("Starting sync_contributors for record %d", sync_record_id)
            result = await sync_contributors(
                since_date=since_dt,
                until_date=until_dt,
                repo_name=repo_name,
                progress_callback=progress_callback,
            )

            # 最终状态
            logger.info("sync_contributors returned: contributors=%d commits=%d build_plans=%d errors=%d duration=%.1f", result.contributors_synced, result.commits_classified, result.build_plans_created, len(result.errors), result.duration_seconds)
            errors_json = result.errors if isinstance(result.errors, list) else []
            has_errors = len(errors_json) > 0
            has_results = (result.contributors_synced > 0 or result.commits_classified > 0)

            if has_errors and has_results:
                final_status = "partial"
                error_summary = f"{len(errors_json)} errors (first: {errors_json[0][:120]}...)" if errors_json else "unknown error"
                await tracker.partial(
                    error_message=error_summary,
                    result_metadata={
                        "contributors_synced": result.contributors_synced,
                        "commits_classified": result.commits_classified,
                        "build_plans_created": result.build_plans_created,
                    },
                )
            elif has_errors:
                final_status = "failed"
                error_summary = f"{len(errors_json)} errors (first: {errors_json[0][:120]}...)" if errors_json else "unknown error"
                await tracker.fail(error_summary)
            else:
                final_status = "completed"
                await tracker.complete(result_metadata={
                    "contributors_synced": result.contributors_synced,
                    "commits_classified": result.commits_classified,
                    "build_plans_created": result.build_plans_created,
                })

            # 更新 ContributorSyncRecord 最终结果
            logger.info("Updating ContributorSyncRecord %d final status: %s", sync_record_id, final_status)
            final_msg = f"同步 {final_status}: {result.contributors_synced} 贡献者, {result.commits_classified} 提交"
            async with AsyncSessionLocal() as update_db:
                await update_db.execute(
                    sa_update(ContributorSyncRecord)
                    .where(ContributorSyncRecord.id == sync_record_id)
                    .values(
                        status=final_status,
                        contributors_synced=result.contributors_synced,
                        commits_classified=result.commits_classified,
                        total_prs_scanned=result.total_prs_scanned,
                        build_plans_created=result.build_plans_created,
                        errors=errors_json,
                        duration_seconds=result.duration_seconds,
                        synced_at=result.synced_at or datetime.now(UTC),
                        progress_percent=100,
                        progress_message=final_msg,
                        completed_at=func.now(),
                    )
                )
                await update_db.commit()

            return {
                "contributors_synced": result.contributors_synced,
                "commits_classified": result.commits_classified,
                "build_plans_created": result.build_plans_created,
            }

        except Exception as e:
            logger.error("Contributor sync task failed: %s", e, exc_info=True)
            await tracker.fail(str(e))

            # 更新 ContributorSyncRecord 为失败
            async with AsyncSessionLocal() as update_db:
                await update_db.execute(
                    sa_update(ContributorSyncRecord)
                    .where(ContributorSyncRecord.id == sync_record_id)
                    .values(
                        status="failed",
                        errors=[str(e)],
                        progress_percent=0,
                        progress_message=f"同步失败: {e}",
                        completed_at=func.now(),
                    )
                )
                await update_db.commit()

            raise

    @staticmethod
    async def get_sync_summary(db, repo_name: str | None = None) -> ContributorSyncSummary:
        """获取同步统计摘要"""
        filters = []
        if repo_name:
            filters.append(ContributorSyncRecord.repo_name == repo_name)

        total = select(func.count(ContributorSyncRecord.id)).where(*filters) if filters else select(func.count(ContributorSyncRecord.id))
        completed = select(func.count(ContributorSyncRecord.id)).where(*([ContributorSyncRecord.status == "completed"] + filters))
        failed = select(func.count(ContributorSyncRecord.id)).where(*([ContributorSyncRecord.status == "failed"] + filters))
        running = select(func.count(ContributorSyncRecord.id)).where(*([ContributorSyncRecord.status == "running"] + filters))

        total_sum = select(func.coalesce(func.sum(ContributorSyncRecord.contributors_synced), 0)).where(*filters) if filters else select(func.coalesce(func.sum(ContributorSyncRecord.contributors_synced), 0))
        commits_sum = select(func.coalesce(func.sum(ContributorSyncRecord.commits_classified), 0)).where(*filters) if filters else select(func.coalesce(func.sum(ContributorSyncRecord.commits_classified), 0))
        build_sum = select(func.coalesce(func.sum(ContributorSyncRecord.build_plans_created), 0)).where(*filters) if filters else select(func.coalesce(func.sum(ContributorSyncRecord.build_plans_created), 0))
        prs_sum = select(func.coalesce(func.sum(ContributorSyncRecord.total_prs_scanned), 0)).where(*filters) if filters else select(func.coalesce(func.sum(ContributorSyncRecord.total_prs_scanned), 0))

        last_record = select(ContributorSyncRecord).order_by(ContributorSyncRecord.created_at.desc()).limit(1)
        if repo_name:
            last_record = last_record.where(ContributorSyncRecord.repo_name == repo_name)

        results = await asyncio.gather(
            db.execute(total),
            db.execute(completed),
            db.execute(failed),
            db.execute(running),
            db.execute(total_sum),
            db.execute(commits_sum),
            db.execute(build_sum),
            db.execute(prs_sum),
            db.execute(last_record),
        )

        last_rec = results[8].scalar_one_or_none()
        return ContributorSyncSummary(
            total_syncs=int(results[0].scalar() or 0),
            completed_count=int(results[1].scalar() or 0),
            failed_count=int(results[2].scalar() or 0),
            running_count=int(results[3].scalar() or 0),
            total_contributors=int(results[4].scalar() or 0),
            total_commits=int(results[5].scalar() or 0),
            total_build_plans=int(results[6].scalar() or 0),
            total_prs_scanned=int(results[7].scalar() or 0),
            last_sync_at=last_rec.synced_at if last_rec else None,
            last_sync_status=last_rec.status if last_rec else None,
        )

    @staticmethod
    async def get_sync_record(db, record_id: int) -> ContributorSyncRecordResponse | None:
        stmt = select(ContributorSyncRecord).where(ContributorSyncRecord.id == record_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return ContributorSyncRecordResponse.model_validate(record)

    @staticmethod
    async def get_sync_history(
        db,
        limit: int = 20,
        offset: int = 0,
        repo_name: str | None = None,
        status: str | None = None,
    ) -> ContributorSyncHistoryResponse:
        stmt = select(ContributorSyncRecord)
        count_stmt = select(func.count(ContributorSyncRecord.id))

        if repo_name:
            stmt = stmt.where(ContributorSyncRecord.repo_name == repo_name)
            count_stmt = count_stmt.where(ContributorSyncRecord.repo_name == repo_name)
        if status:
            stmt = stmt.where(ContributorSyncRecord.status == status)
            count_stmt = count_stmt.where(ContributorSyncRecord.status == status)

        total = int((await db.execute(count_stmt)).scalar() or 0)

        stmt = stmt.order_by(ContributorSyncRecord.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        records = result.scalars().all()

        return ContributorSyncHistoryResponse(
            records=[ContributorSyncRecordResponse.model_validate(r) for r in records],
            total=total,
        )


    # ---- 贡献者详情页增强：热力图 ----

    @staticmethod
    async def get_contributor_heatmap_data(
        db,
        login: str,
        repo_name: str | None = None,
        years: int = 1,
    ) -> dict:
        """按日聚合贡献者的 commit 数量，返回热力图数据。

        Returns:
            { login, heatmap_data: [ {date, commit_count, weekDay}, ... ] }
        """
        from datetime import timedelta, UTC, datetime

        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=years * 365)

        conditions = [
            CommitClassification.contributor_login == login,
            CommitClassification.created_at >= start_date,
            CommitClassification.created_at <= end_date,
        ]
        if repo_name:
            conditions.append(CommitClassification.repo_name == repo_name)

        date_col = func.date(CommitClassification.created_at)

        stmt = (
            select(
                date_col.label("d"),
                func.count(CommitClassification.id).label("cnt"),
            )
            .where(and_(*conditions))
            .group_by(date_col)
            .order_by(date_col.asc())
        )

        result = await db.execute(stmt)
        rows = result.all()

        heatmap_data: list[dict] = []
        for r in rows:
            d = r[0]
            cnt = int(r[1] or 0)
            if isinstance(d, datetime):
                date_str = d.strftime("%Y-%m-%d")
                weekday = d.weekday()
            elif isinstance(d, str):
                date_str = d
                weekday = datetime.strptime(d, "%Y-%m-%d").weekday()
            else:
                date_str = str(d)
                weekday = 0
            heatmap_data.append({
                "date": date_str,
                "commit_count": cnt,
                "weekDay": weekday,
            })

        return {"login": login, "heatmap_data": heatmap_data}

    # ---- 贡献者详情页增强：模块+类型分布 ----

    @staticmethod
    async def get_contributor_module_distribution(
        db,
        login: str,
        repo_name: str | None = None,
    ) -> dict:
        """按模块和类型聚合贡献者的提交分布。

        Returns:
            { login, modules, type_distribution }
        """
        conditions_cc = [CommitClassification.contributor_login == login]
        if repo_name:
            conditions_cc.append(CommitClassification.repo_name == repo_name)

        # 模块分布
        cc_stmt = (
            select(
                CommitClassification.commit_sha,
                CommitClassification.modules_touched,
            )
            .where(and_(*conditions_cc))
        )
        cc_result = await db.execute(cc_stmt)
        cc_rows = cc_result.all()

        module_commit_count: dict[str, int] = {}
        for r in cc_rows:
            modules = r[1] or []
            if isinstance(modules, list):
                for mod in modules:
                    mod_name = str(mod).strip()
                    if mod_name:
                        module_commit_count[mod_name] = module_commit_count.get(mod_name, 0) + 1

        # 从 ContributorMetrics 获取 code 统计
        cm_stmt = (
            select(
                ContributorMetrics.contributor_login,
                func.coalesce(func.sum(ContributorMetrics.code_additions), 0).label("total_additions"),
                func.coalesce(func.sum(ContributorMetrics.code_deletions), 0).label("total_deletions"),
            )
            .where(ContributorMetrics.contributor_login == login)
        )
        if repo_name:
            cm_stmt = cm_stmt.where(ContributorMetrics.repo_name == repo_name)

        cm_result = await db.execute(cm_stmt)
        cm_row = cm_result.one_or_none()

        total_additions = int(cm_row[1] or 0) if cm_row else 0
        total_deletions = int(cm_row[2] or 0) if cm_row else 0

        total_module_commits = sum(module_commit_count.values())
        modules: list[dict] = []
        for mod_name, cnt in sorted(module_commit_count.items(), key=lambda x: -x[1]):
            ratio = cnt / total_module_commits if total_module_commits > 0 else 0
            modules.append({
                "module": mod_name,
                "commit_count": cnt,
                "code_additions": int(total_additions * ratio),
                "code_deletions": int(total_deletions * ratio),
            })

        # 类型分布
        type_stmt = (
            select(
                CommitClassification.commit_type,
                func.count(CommitClassification.id).label("cnt"),
            )
            .where(and_(*conditions_cc))
            .group_by(CommitClassification.commit_type)
            .order_by(func.count(CommitClassification.id).desc())
        )
        type_result = await db.execute(type_stmt)
        type_rows = type_result.all()

        type_distribution: list[dict] = [
            {"commit_type": r[0], "count": int(r[1] or 0)}
            for r in type_rows
        ]

        return {
            "login": login,
            "modules": modules,
            "type_distribution": type_distribution,
        }

    # ---- 贡献者详情页增强：提交时间线 ----

    @staticmethod
    async def get_contributor_timeline(
        db,
        login: str,
        limit: int = 20,
    ) -> dict:
        """获取贡献者最近的提交记录，按时间倒序排列。

        Returns:
            { login, commits: [...] }
        """
        conditions = [CommitClassification.contributor_login == login]

        stmt = (
            select(
                CommitClassification.commit_sha,
                CommitClassification.commit_message,
                CommitClassification.commit_type,
                CommitClassification.modules_touched,
                CommitClassification.created_at,
                CommitClassification.files_changed_count,
            )
            .where(and_(*conditions))
            .order_by(CommitClassification.created_at.desc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        commits: list[dict] = []
        for r in rows:
            modules = r[3] or []
            primary_module = str(modules[0]) if isinstance(modules, list) and len(modules) > 0 else ""
            commits.append({
                "commit_hash": r[0][:8] if r[0] else "",
                "message": (r[1] or "")[:120],
                "classification": r[2] or "",
                "module": primary_module,
                "committed_at": r[4],
                "files_changed": int(r[5] or 0),
                "code_additions": 0,
                "code_deletions": 0,
            })

        return {"login": login, "commits": commits}


_service_instance: ContributorBoardService | None = None


def get_contributor_board_service() -> ContributorBoardService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ContributorBoardService()
    return _service_instance
