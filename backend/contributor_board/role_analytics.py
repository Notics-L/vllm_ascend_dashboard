"""
贡献者角色分析模块

根据贡献者的行为特征自动识别角色：
- developer: 有提交 PR 和代码的贡献者
- reviewer: 有 review 行为的贡献者
- maintainer: 有 merge 权限（内部成员）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Integer, and_, desc, func, or_, select

from contracts.schemas.contributor_board import (
    ContributorRankingItem,
    ContributorRankingResponse,
    ModuleHotspotItem,
    ModuleHotspotResponse,
    RoleStats,
)
from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorMetrics,
    ErrorRecord,
)

logger = logging.getLogger(__name__)

# 角色判定阈值
REVIEWER_THRESHOLD = 3
MAINTAINER_MIN_MERGE = 5

# 内部贡献者标志
_COMMUNITY_COMPANY_KEYWORDS: list[str] = []


@dataclass
class RoleTypeInfo:
    """角色类型信息"""
    role: str
    label: str
    color: str
    description: str


ROLE_TYPES: dict[str, RoleTypeInfo] = {
    "developer": RoleTypeInfo(
        role="developer",
        label="开发者",
        color="#1890ff",
        description="有代码贡献的开发者",
    ),
    "reviewer": RoleTypeInfo(
        role="reviewer",
        label="检视者",
        color="#722ed1",
        description="审核代码的检视者",
    ),
    "maintainer": RoleTypeInfo(
        role="maintainer",
        label="Maintainer",
        color="#fa8c16",
        description="合并 PR 的维护者",
    ),
}


def detect_roles(
    merged_pr_count: int,
    review_count: int,
    approval_count: int,
    merge_count: int,
    code_additions: int,
) -> list[str]:
    """
    根据行为数据推断贡献者角色

    Args:
        merged_pr_count: 合并 PR 数
        review_count: review 数
        approval_count: approval 数
        merge_count: merge 数
        code_additions: 代码添加行数

    Returns:
        角色列表，如 ["developer", "reviewer"]
    """
    roles: list[str] = []

    # 开发者判定：有合并 PR 或代码贡献
    if merged_pr_count > 0 or code_additions > 0:
        roles.append("developer")

    # 检视者判定：有一定数量的 review
    if review_count >= REVIEWER_THRESHOLD:
        roles.append("reviewer")

    # Maintainer 判定：有 merge 权限
    if merge_count >= MAINTAINER_MIN_MERGE:
        if "maintainer" not in roles:
            roles.append("maintainer")

    # 如果没有匹配到任何角色，默认给 developer
    if not roles:
        roles.append("developer")

    return roles


async def refresh_all_roles(db) -> int:
    """
    遍历所有贡献者，根据 ContributorMetrics 重新计算角色

    Returns:
        更新的贡献者数量
    """
    stmt = (
        select(
            Contributor,
            ContributorMetrics,
        )
        .outerjoin(
            ContributorMetrics,
            Contributor.id == ContributorMetrics.contributor_id,
        )
        .order_by(desc(ContributorMetrics.updated_at))
    )

    result = await db.execute(stmt)
    rows = result.all()

    # 按 login 分组取最新的 metrics
    contributor_map: dict[str, tuple[Contributor, ContributorMetrics | None]] = {}
    for contributor, metrics in rows:
        login = contributor.login
        if login not in contributor_map:
            contributor_map[login] = (contributor, metrics)

    updated = 0
    for login, (contributor, metrics) in contributor_map.items():
        if metrics:
            roles = detect_roles(
                merged_pr_count=metrics.merged_pr_count or 0,
                review_count=metrics.review_count or 0,
                approval_count=metrics.approval_count or 0,
                merge_count=metrics.merge_count or 0,
                code_additions=metrics.code_additions or 0,
            )
        else:
            roles = ["developer"]

        old_roles = contributor.role_types or []
        if old_roles != roles:
            contributor.role_types = roles
            updated += 1
            logger.debug("Updated roles for %s: %s -> %s", login, old_roles, roles)

    logger.info("Refreshed roles for %d contributors", updated)
    return updated


async def get_role_stats(db, repo_name: str | None = None) -> RoleStats:
    """
    获取角色维度统计（从 pull_requests 表实时聚合，与排行榜数据源一致）

    Args:
        db: 数据库会话
        repo_name: 仓库名过滤

    Returns:
        角色统计信息
    """
    from contributor_board.pr_enrich import get_full_contributor_metrics_from_prs

    all_metrics = await get_full_contributor_metrics_from_prs(db, repo_name=repo_name)

    role_data: dict[str, dict[str, int | float]] = {
        "developer": {"count": 0, "total_pr": 0, "total_code": 0, "total_score": 0},
        "reviewer": {"count": 0, "total_review": 0, "total_approval": 0, "total_score": 0},
        "maintainer": {"count": 0, "total_merge": 0, "total_score": 0},
    }

    for m in all_metrics:
        roles = detect_roles(
            merged_pr_count=m.get("merged_pr_count", 0),
            review_count=m.get("review_count", 0),
            approval_count=m.get("approval_count", 0),
            merge_count=0,
            code_additions=m.get("code_additions", 0),
        )

        if "developer" in roles:
            role_data["developer"]["count"] += 1
            role_data["developer"]["total_pr"] += m.get("merged_pr_count", 0)
            role_data["developer"]["total_code"] += m.get("code_additions", 0)
            role_data["developer"]["total_score"] += m.get("contribution_score", 0)

        if "reviewer" in roles:
            role_data["reviewer"]["count"] += 1
            role_data["reviewer"]["total_review"] += m.get("review_count", 0)
            role_data["reviewer"]["total_approval"] += m.get("approval_count", 0)
            role_data["reviewer"]["total_score"] += m.get("contribution_score", 0)

        if "maintainer" in roles:
            role_data["maintainer"]["count"] += 1
            role_data["maintainer"]["total_merge"] += m.get("merged_pr_count", 0)
            role_data["maintainer"]["total_score"] += m.get("contribution_score", 0)

    return RoleStats(
        developer_count=int(role_data["developer"]["count"]),
        reviewer_count=int(role_data["reviewer"]["count"]),
        maintainer_count=int(role_data["maintainer"]["count"]),
        total_pr=int(role_data["developer"]["total_pr"]),
        total_code_lines=int(role_data["developer"]["total_code"]),
        total_reviews=int(role_data["reviewer"]["total_review"]),
        total_approvals=int(role_data["reviewer"]["total_approval"]),
    )


async def get_module_hotspot(db, repo_name: str | None = None, period_start: date | None = None, period_end: date | None = None) -> ModuleHotspotResponse:
    """
    获取模块热点统计

    Args:
        db: 数据库会话
        repo_name: 仓库名过滤
        period_start: 时间范围起始
        period_end: 时间范围结束

    Returns:
        模块热点列表，包含百分比和贡献者数
    """
    from infrastructure.persistence.models import ContributorMetrics as Metrics

    base_stmt = select(CommitClassification.modules_touched, CommitClassification.contributor_login)
    conditions = []

    if repo_name:
        # 通过关联 contributor_metrics 获取 repo 过滤
        base_stmt = base_stmt.outerjoin(
            Metrics,
            and_(
                CommitClassification.contributor_login == Metrics.contributor_login,
                Metrics.repo_name == repo_name,
            )
        )
        conditions.append(Metrics.repo_name == repo_name)

    if period_start:
        conditions.append(CommitClassification.created_at >= period_start)
    if period_end:
        conditions.append(CommitClassification.created_at <= period_end)

    base_stmt = base_stmt.where(
        and_(
            CommitClassification.modules_touched.isnot(None),
            *conditions
        )
    )

    hotspot_result = await db.execute(base_stmt)
    module_counts: dict[str, int] = {}
    module_contributors: dict[str, set[str]] = {}

    for modules, login in hotspot_result.all():
        if isinstance(modules, list):
            for mod in modules:
                module_counts[mod] = module_counts.get(mod, 0) + 1
                if mod not in module_contributors:
                    module_contributors[mod] = set()
                if login:
                    module_contributors[mod].add(login)

    total_changes = sum(module_counts.values())

    # 按热度排序
    sorted_modules = sorted(module_counts.items(), key=lambda x: x[1], reverse=True)

    items: list[ModuleHotspotItem] = [
        ModuleHotspotItem(
            module=mod,
            count=count,
            percentage=round(count / total_changes * 100, 2) if total_changes > 0 else 0,
            contributors=len(module_contributors.get(mod, set())),
        )
        for mod, count in sorted_modules
    ]

    return ModuleHotspotResponse(
        modules=items,
        total_changes=total_changes,
        repo_name=repo_name,
        period_start=period_start,
        period_end=period_end,
    )


async def get_role_ranking(
    db,
    role_type: str = "developer",
    repo_name: str | None = None,
    limit: int = 10,
) -> ContributorRankingResponse:
    """
    获取指定角色的排行榜

    Args:
        db: 数据库会话
        role_type: 角色类型 (developer/reviewer/maintainer)
        repo_name: 仓库名过滤
        limit: 返回数量上限

    Returns:
        排行榜响应
    """
    # 筛选具有该角色的贡献者
    contributors_stmt = select(Contributor).where(
        Contributor.role_types.contains([role_type])
    )
    contributors_result = await db.execute(contributors_stmt)
    contributors = contributors_result.scalars().all()

    if not contributors:
        return ContributorRankingResponse(role_type=role_type, rankings=[], repo_name=repo_name)

    logins = [c.login for c in contributors]
    contributor_map = {c.login: c for c in contributors}

    # 获取这些贡献者的 metrics
    metrics_stmt = (
        select(ContributorMetrics)
        .where(ContributorMetrics.contributor_login.in_(logins))
    )
    if repo_name:
        metrics_stmt = metrics_stmt.where(ContributorMetrics.repo_name == repo_name)
    metrics_result = await db.execute(metrics_stmt)
    all_metrics = metrics_result.scalars().all()

    # 按 login 取最新的 metrics
    best_metrics: dict[str, ContributorMetrics] = {}
    for m in all_metrics:
        login = m.contributor_login
        if login not in best_metrics or (m.updated_at or m.period_start) > (
            best_metrics[login].updated_at or best_metrics[login].period_start
        ):
            best_metrics[login] = m

    # 根据角色类型选择排序分数
    role_items: list[tuple[str, ContributorMetrics]] = []
    for login, m in best_metrics.items():
        role_items.append((login, m))

    if role_type == "reviewer":
        role_items.sort(key=lambda x: x[1].review_count or 0, reverse=True)
    elif role_type == "maintainer":
        role_items.sort(key=lambda x: x[1].merge_count or 0, reverse=True)
    else:
        role_items.sort(key=lambda x: x[1].contribution_score or 0, reverse=True)

    rankings: list[ContributorRankingItem] = []
    for i, (login, m) in enumerate(role_items[:limit]):
        c = contributor_map.get(login)
        rankings.append(
            ContributorRankingItem(
                login=login,
                avatar_url=c.avatar_url if c else None,
                employee_id=c.employee_id if c else None,
                score=float(m.contribution_score or 0),
                rank=i + 1,
                merged_pr_count=int(m.merged_pr_count or 0),
                pending_pr_count=int(m.pending_pr_count or 0),
                code_additions=int(m.code_additions or 0),
                code_deletions=int(m.code_deletions or 0),
                review_count=int(m.review_count or 0),
                approval_count=int(m.approval_count or 0),
                contribution_score=float(m.contribution_score or 0),
                quadrant=m.quadrant,
            )
        )

    return ContributorRankingResponse(role_type=role_type, rankings=rankings, repo_name=repo_name)
