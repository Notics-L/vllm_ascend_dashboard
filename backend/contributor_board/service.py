"""
贡献者看板 Service 层
"""
import logging
from datetime import date
from typing import Any

from sqlalchemy import Integer, and_, asc, desc, func, or_, select

from contributor_board.build_optimizer import BuildPlan, evaluate_build_plan
from contributor_board.classifier import ClassificationResult, classify_commit
from contributor_board.quadrant_engine import (
    QuadrantPoint,
    calculate_quadrants,
)
from contributor_board import role_analytics
from contributor_board import policy_engine
from contracts.schemas.contributor_board import (
    BuildOptimizationStats,
    BuildPlanListResponse,
    BuildPlanResponse,
    ClassificationSummary,
    ContributorDetailResponse,
    ContributorListFilter,
    ContributorListResponse,
    ContributorOverviewStats,
    ContributorRankingItem,
    ContributorRankingResponse,
    ContributorResponse,
    ContributorTrendPoint,
    ContributorTrendResponse,
    ContributorSyncRequest,
    ContributorSyncResponse,
    ExtendedTrendResponse,
    ModuleHotspotResponse,
    PolicyItemResponse,
    PolicyResultResponse,
    QuadrantDataResponse,
    RoleStats,
)
from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorMetrics,
    ErrorRecord,
    ImageBuildPlan,
    QuadrantSnapshot,
)

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
    async def get_classification_summary_from_db(db) -> ClassificationSummary:
        total_result = await db.execute(
            select(func.count(CommitClassification.id))
        )
        total = int(total_result.scalar() or 0)

        type_dist_result = await db.execute(
            select(
                CommitClassification.commit_type,
                func.count(CommitClassification.id),
            ).group_by(CommitClassification.commit_type)
        )
        type_tuples = type_dist_result.all()
        type_distribution: dict[str, int] = {k: int(v) for k, v in type_tuples}

        type_percentage: dict[str, float] = {}
        if total > 0:
            for k, v in type_distribution.items():
                type_percentage[k] = round(v / total * 100, 2)

        chip_result = await db.execute(
            select(func.sum(CommitClassification.is_chip_specific.cast(Integer)))
        )
        chip_specific_count = int(chip_result.scalar() or 0)

        module_hotspot: dict[str, int] = {}
        hotspot_result = await db.execute(
            select(CommitClassification.modules_touched)
            .where(CommitClassification.modules_touched.isnot(None))
        )
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
    async def get_build_optimization_stats_from_db(db) -> BuildOptimizationStats:
        total_result = await db.execute(
            select(func.count(ImageBuildPlan.id))
        )
        total_evals = int(total_result.scalar() or 0)

        skipped_result = await db.execute(
            select(func.sum(ImageBuildPlan.build_type == "skip").cast(Integer))
        )
        skipped = int(skipped_result.scalar() or 0)

        reduced_result = await db.execute(
            select(func.sum(
                ImageBuildPlan.build_type.in_(["minimal_build", "nightly_only", "smart_check"])
            ).cast(Integer))
        )
        reduced = int(reduced_result.scalar() or 0)

        full_result = await db.execute(
            select(func.sum(ImageBuildPlan.build_type == "full_build").cast(Integer))
        )
        full_builds = int(full_result.scalar() or 0)

        avg_saving_result = await db.execute(
            select(func.avg(ImageBuildPlan.estimated_saving_percent))
        )
        avg_saving = round(float(avg_saving_result.scalar() or 0), 2)

        by_type_result = await db.execute(
            select(
                ImageBuildPlan.build_type,
                func.count(ImageBuildPlan.id),
            ).group_by(ImageBuildPlan.build_type)
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
    ) -> BuildPlanListResponse:
        stmt = select(ImageBuildPlan)
        count_stmt = select(func.count(ImageBuildPlan.id))

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
                    reasoning=None,
                    created_at=p.created_at,
                )
                for p in plans
            ],
            total=total,
        )

    @staticmethod
    async def get_build_plan_filter_options(db) -> dict:
        commit_types_result = await db.execute(
            select(ImageBuildPlan.commit_type)
            .where(ImageBuildPlan.commit_type.isnot(None), ImageBuildPlan.commit_type != "")
            .distinct()
            .order_by(ImageBuildPlan.commit_type)
        )
        commit_types = [row[0] for row in commit_types_result.all()]

        build_types_result = await db.execute(
            select(ImageBuildPlan.build_type)
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
    async def get_quadrant_data(db, repo_name: str | None = None, period_start: date | None = None, period_end: date | None = None) -> QuadrantDataResponse:
        metrics_stmt = select(ContributorMetrics)

        conditions = []
        if repo_name:
            conditions.append(ContributorMetrics.repo_name == repo_name)
        if period_start and period_end:
            conditions.append(
                and_(
                    ContributorMetrics.period_start >= period_start,
                    ContributorMetrics.period_end <= period_end,
                )
            )

        if conditions:
            metrics_stmt = metrics_stmt.where(and_(*conditions))

        metrics_stmt = metrics_stmt.order_by(desc(ContributorMetrics.updated_at))
        result = await db.execute(metrics_stmt)
        metrics_list = result.scalars().all()

        contributors_metrics: list[dict[str, Any]] = []
        for m in metrics_list:
            contributors_metrics.append({
                "login": m.contributor_login,
                "avatar_url": None,
                "contribution_score": float(m.contribution_score or 0),
                "error_score": float(m.error_score or 0),
                "merged_pr_count": int(m.merged_pr_count or 0),
                "is_community": False,
            })

        points, quad_x, quad_y = calculate_quadrants(contributors_metrics)

        quadrant_counts: dict[str, int] = {}
        for p in points:
            quadrant_counts[p.quadrant] = quadrant_counts.get(p.quadrant, 0) + 1

        return QuadrantDataResponse(
            points=[
                {
                    "login": p.login,
                    "avatar_url": p.avatar_url,
                    "contribution_score": p.contribution_score,
                    "error_score": p.error_score,
                    "quadrant": p.quadrant,
                    "merged_pr_count": p.merged_pr_count,
                    "is_community": p.is_community,
                }
                for p in points
            ],
            quad_x=quad_x,
            quad_y=quad_y,
            repo_name=repo_name,
            period_start=period_start,
            period_end=period_end,
            total_count=len(points),
            quadrant_counts=quadrant_counts,
        )

    # ---- 排行榜 ----
    @staticmethod
    async def get_ranking(
        db,
        role_type: str = "developer",
        repo_name: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        sort_by: str | None = None,
        sort_order: str = "desc",
        limit: int = 10,
    ) -> ContributorRankingResponse:
        score_column = ContributorMetrics.contribution_score
        if role_type == "reviewer":
            score_column = ContributorMetrics.review_count
        elif role_type == "maintainer":
            score_column = ContributorMetrics.merge_count

        # Aggregated sort columns
        agg_columns: dict[str, Any] = {
            "score": func.sum(ContributorMetrics.contribution_score),
            "merged_pr_count": func.sum(ContributorMetrics.merged_pr_count),
            "pending_pr_count": func.sum(ContributorMetrics.pending_pr_count),
            "code_additions": func.sum(ContributorMetrics.code_additions),
            "code_deletions": func.sum(ContributorMetrics.code_deletions),
            "review_count": func.sum(ContributorMetrics.review_count),
        }

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
                func.max(ContributorMetrics.quadrant).label('quadrant'),
            )
            .join(ContributorMetrics, Contributor.id == ContributorMetrics.contributor_id)
        )

        conditions = []
        if repo_name:
            conditions.append(ContributorMetrics.repo_name == repo_name)
        if period_start:
            conditions.append(ContributorMetrics.period_start >= period_start)
        if period_end:
            conditions.append(ContributorMetrics.period_end <= period_end)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.group_by(
            Contributor.login,
            Contributor.avatar_url,
            Contributor.employee_id,
        )

        order_col: Any
        if sort_by and sort_by in agg_columns:
            order_col = agg_columns[sort_by]
        elif role_type == "reviewer":
            order_col = agg_columns["review_count"]
        elif role_type == "maintainer":
            order_col = agg_columns["merged_pr_count"]
        else:
            order_col = agg_columns["score"]

        order_func = asc if sort_order == "asc" else desc
        stmt = stmt.order_by(order_func(order_col)).limit(limit)

        result = await db.execute(stmt)
        rows = result.all()

        rankings: list[ContributorRankingItem] = [
            ContributorRankingItem(
                login=r[0],
                avatar_url=r[1],
                employee_id=r[2],
                score=float(r[3] or 0),
                rank=i + 1,
                merged_pr_count=int(r[4] or 0),
                pending_pr_count=int(r[5] or 0),
                code_additions=int(r[6] or 0),
                code_deletions=int(r[7] or 0),
                review_count=int(r[8] or 0),
                approval_count=int(r[9] or 0),
                contribution_score=float(r[3] or 0),
                quadrant=r[10],
            )
            for i, r in enumerate(rows)
        ]

        return ContributorRankingResponse(
            role_type=role_type,
            rankings=rankings,
            repo_name=repo_name,
            period_start=period_start,
            period_end=period_end,
        )

    # ---- 趋势数据 ----

    @staticmethod
    async def _fetch_contributor_metrics(
        db,
        login: str,
        period_start: date | None = None,
        period_end: date | None = None,
    ):
        stmt = (
            select(ContributorMetrics)
            .where(ContributorMetrics.contributor_login == login)
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
        result = await db.execute(
            select(ContributorMetrics.repo_name)
            .where(ContributorMetrics.repo_name.isnot(None))
            .distinct()
            .order_by(ContributorMetrics.repo_name)
        )
        repos = [r[0] for r in result.all()]
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
                    description=p.description,
                    reason=p.reason,
                )
                for p in result.policies
            ],
            has_critical=result.has_critical,
            has_warning=result.has_warning,
            evaluated_at=result.evaluated_at,
        )

    # ---- 扩展趋势数据 ----

    @staticmethod
    async def get_extended_trend(
        db,
        login: str,
        period_start: date | None = None,
        period_end: date | None = None,
    ) -> ExtendedTrendResponse:
        metrics = await ContributorBoardService._fetch_contributor_metrics(
            db, login, period_start, period_end
        )

        from contributor_board.policy_engine import get_trend_data
        trend = get_trend_data(login, list(metrics))

        from contracts.schemas.contributor_board import (
            CommitTrendPoint,
            ReviewTrendPoint,
            ScoreTrendPoint,
        )

        return ExtendedTrendResponse(
            login=login,
            commit_trend=[
                CommitTrendPoint(**p) for p in trend.get("commit_trend", [])
            ],
            review_trend=[
                ReviewTrendPoint(**p) for p in trend.get("review_trend", [])
            ],
            score_trend=[
                ScoreTrendPoint(**p) for p in trend.get("score_trend", [])
            ],
        )

    # ---- 同步接口 ----

    @staticmethod
    async def trigger_sync(
        since_date: date | None = None,
    ) -> ContributorSyncResponse:
        from datetime import UTC, datetime, timedelta
        from collector.contributor_sync import sync_contributors

        if since_date is None:
            since_dt = datetime.now(UTC) - timedelta(days=7)
        else:
            from datetime import time
            since_dt = datetime.combine(since_date, time.min, tzinfo=UTC)

        try:
            result = await sync_contributors(since_date=since_dt)

            return ContributorSyncResponse(
                contributors_synced=result.contributors_synced,
                commits_classified=result.commits_classified,
                errors=result.errors,
                duration_seconds=result.duration_seconds,
                synced_at=result.synced_at or datetime.now(UTC),
            )
        except Exception as e:
            logger.error("Contributor sync failed: %s", e, exc_info=True)
            return ContributorSyncResponse(
                errors=[str(e)],
                synced_at=datetime.now(UTC),
            )


_service_instance: ContributorBoardService | None = None


def get_contributor_board_service() -> ContributorBoardService:
    global _service_instance
    if _service_instance is None:
        _service_instance = ContributorBoardService()
    return _service_instance
