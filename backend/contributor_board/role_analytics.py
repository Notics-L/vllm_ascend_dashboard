"""
贡献者角色分析模块

基于 ContributorProfile 宽表，根据 7 维原始指标推断角色：
- developer: 有合并 PR 或代码文件变更
- reviewer: 有 review/approval 行为
- maintainer: 合并 PR 数较多（内部成员）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import desc, select

from contracts.schemas.contributor_board import (
    ContributorRankingItem,
    ContributorRankingResponse,
    ModuleHotspotItem,
    ModuleHotspotResponse,
    RoleStats,
)
from infrastructure.persistence.models import (
    Contributor,
    ContributorProfile,
)

logger = logging.getLogger(__name__)

# 角色判定阈值
REVIEWER_THRESHOLD = 3
MAINTAINER_MIN_MERGE = 5


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
    根据行为数据推断贡献者角色（保留旧签名，collector 引用）

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

    if merged_pr_count > 0 or code_additions > 0:
        roles.append("developer")

    if review_count >= REVIEWER_THRESHOLD:
        roles.append("reviewer")

    if merge_count >= MAINTAINER_MIN_MERGE:
        if "maintainer" not in roles:
            roles.append("maintainer")

    if not roles:
        roles.append("developer")

    return roles


async def refresh_all_roles(db) -> int:
    """
    遍历 ContributorProfile，根据 7 维指标重新计算角色并写回 profile.role
    与 contributors.role_types。

    Returns:
        更新行数
    """
    stmt = select(ContributorProfile).order_by(desc(ContributorProfile.last_seen))
    result = await db.execute(stmt)
    profiles = result.scalars().all()

    if not profiles:
        return 0

    logins = {p.login for p in profiles}
    contributor_stmt = select(Contributor).where(Contributor.login.in_(logins))
    c_result = await db.execute(contributor_stmt)
    contributor_map: dict[str, Contributor] = {c.login: c for c in c_result.scalars().all()}

    updated = 0
    for p in profiles:
        roles = detect_roles(
            merged_pr_count=p.pr_merged or 0,
            review_count=p.review_count or 0,
            approval_count=p.approval_count or 0,
            merge_count=p.pr_merged or 0,
            code_additions=p.code_files or 0,
        )
        role_list = sorted(set(roles))

        old = p.role
        if old != role_list[0]:
            p.role = role_list[0]
            updated += 1

        c = contributor_map.get(p.login)
        if c:
            old_roles = c.role_types or []
            if old_roles != role_list:
                c.role_types = role_list
                updated += 1

    logger.info("Refreshed roles for %d contributor profiles", updated)
    return updated


async def get_role_stats(
    db,
    repo_name: str | None = None,
    days: int = 30,
) -> RoleStats:
    """
    从 ContributorProfile 宽表按角色聚合统计。

    developer_count = role 含 developer 的人数
    reviewer_count  = role 含 reviewer 的人数
    total_pr        = sum(pr_merged + pr_open + pr_closed)
    total_code_lines = sum(code_files + doc_files)
    total_reviews   = sum(review_count)
    total_approvals = sum(approval_count)
    """
    stmt = select(ContributorProfile)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)
    if days and days > 0:
        # 仅取近 N 天内活跃的（last_seen 在 N 天内）
        from datetime import UTC, datetime
        since = datetime.now(UTC).date() - timedelta(days=days)
        stmt = stmt.where(ContributorProfile.last_seen >= since)

    result = await db.execute(stmt)
    profiles = result.scalars().all()

    developer_count = 0
    reviewer_count = 0
    maintainer_count = 0
    total_pr = 0
    total_code_lines = 0
    total_reviews = 0
    total_approvals = 0

    for p in profiles:
        roles = [p.role] if p.role else []
        dev = (p.pr_merged or 0) > 0 or (p.code_files or 0) > 0
        rev = (p.review_count or 0) >= REVIEWER_THRESHOLD
        main = (p.pr_merged or 0) >= MAINTAINER_MIN_MERGE
        if dev:
            developer_count += 1
        if rev:
            reviewer_count += 1
        if main:
            maintainer_count += 1
        total_pr += (p.pr_merged or 0) + (p.pr_open or 0) + (p.pr_closed or 0)
        total_code_lines += (p.code_files or 0) + (p.doc_files or 0)
        total_reviews += p.review_count or 0
        total_approvals += p.approval_count or 0

    return RoleStats(
        developer_count=developer_count,
        reviewer_count=reviewer_count,
        maintainer_count=maintainer_count,
        total_pr=total_pr,
        total_code_lines=total_code_lines,
        total_reviews=total_reviews,
        total_approvals=total_approvals,
    )


async def get_module_hotspot(
    db,
    repo_name: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> ModuleHotspotResponse:
    """
    获取模块热点统计。

    新宽表不再保存 modules_touched 明细（依赖已删除的 commit_classifications 表）。
    当前实现基于 ContributorProfile 聚合每个贡献者的 code_files 贡献量，
    按 "code" 单一维度返回，仅作为过渡降级方案。
    后续若需按模块拆分，应从 pull_requests / files JSON 实时聚合。
    """
    stmt = select(ContributorProfile)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)
    if period_start:
        stmt = stmt.where(ContributorProfile.last_seen >= period_start)
    if period_end:
        stmt = stmt.where(ContributorProfile.first_seen <= period_end)

    result = await db.execute(stmt)
    profiles = result.scalars().all()

    if not profiles:
        return ModuleHotspotResponse(
            modules=[],
            total_changes=0,
            repo_name=repo_name,
            period_start=period_start,
            period_end=period_end,
        )

    total_changes = sum((p.code_files or 0) + (p.doc_files or 0) for p in profiles)
    code_total = sum(p.code_files or 0 for p in profiles)
    doc_total = sum(p.doc_files or 0 for p in profiles)
    code_contributors = len({p.login for p in profiles if (p.code_files or 0) > 0})
    doc_contributors = len({p.login for p in profiles if (p.doc_files or 0) > 0})

    items: list[ModuleHotspotItem] = []
    if code_total > 0:
        items.append(ModuleHotspotItem(
            module="code",
            count=code_total,
            percentage=round(code_total / total_changes * 100, 2) if total_changes > 0 else 0,
            contributors=code_contributors,
        ))
    if doc_total > 0:
        items.append(ModuleHotspotItem(
            module="docs",
            count=doc_total,
            percentage=round(doc_total / total_changes * 100, 2) if total_changes > 0 else 0,
            contributors=doc_contributors,
        ))

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

    - developer: 按 total_score 排序
    - reviewer: 按 review_count 排序
    - maintainer: 按 pr_merged 排序
    """
    stmt = select(ContributorProfile)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)

    # 角色过滤：developer/reviewer/maintainer 基于原始指标判定
    profiles = (await db.execute(stmt)).scalars().all()

    def _role_match(p: ContributorProfile) -> bool:
        if role_type == "developer":
            return (p.pr_merged or 0) > 0 or (p.code_files or 0) > 0
        if role_type == "reviewer":
            return (p.review_count or 0) >= REVIEWER_THRESHOLD
        if role_type == "maintainer":
            return (p.pr_merged or 0) >= MAINTAINER_MIN_MERGE
        return True

    matched = [p for p in profiles if _role_match(p)]

    if role_type == "reviewer":
        matched.sort(key=lambda p: p.review_count or 0, reverse=True)
    elif role_type == "maintainer":
        matched.sort(key=lambda p: p.pr_merged or 0, reverse=True)
    else:
        matched.sort(key=lambda p: p.total_score or 0, reverse=True)

    if not matched:
        return ContributorRankingResponse(role_type=role_type, rankings=[], repo_name=repo_name)

    # 批量取 Contributor 信息（头像/工号/邮箱等）
    logins = {p.login for p in matched}
    c_stmt = select(Contributor).where(Contributor.login.in_(logins))
    c_result = await db.execute(c_stmt)
    contributor_map: dict[str, Contributor] = {c.login: c for c in c_result.scalars().all()}

    rankings: list[ContributorRankingItem] = []
    for i, p in enumerate(matched[:limit]):
        c = contributor_map.get(p.login)
        pr_total = (p.pr_merged or 0) + (p.pr_open or 0) + (p.pr_closed or 0)
        rankings.append(
            ContributorRankingItem(
                login=p.login,
                avatar_url=c.avatar_url if c else None,
                employee_id=c.employee_id if c else None,
                company=c.company if c else None,
                is_bot=False,
                score=float(p.total_score or 0),
                rank=i + 1,
                merged_pr_count=int(p.pr_merged or 0),
                pending_pr_count=int(p.pr_open or 0),
                code_additions=int(p.code_files or 0),
                code_deletions=int(0),
                review_count=int(p.review_count or 0),
                approval_count=int(p.approval_count or 0),
                contribution_score=float(p.total_score or 0),
                commit_count=int(p.commit_count or 0),
                dynamic_quadrant=p.tier,
                role_label=p.role,
                contributor_type="reviewer" if p.role == "reviewer" else "author",
            )
        )

    return ContributorRankingResponse(
        role_type=role_type,
        rankings=rankings,
        total=len(matched),
        repo_name=repo_name,
    )
