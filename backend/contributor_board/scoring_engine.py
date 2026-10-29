"""
统一贡献者评分引擎

从 DB 读取 ScoringWeightsConfig，根据贡献者多维度指标动态计算贡献分数。
支持新增维度：文件变更数、BugFix 贡献、活跃持续时间等。

设计原则：
- 设置与计算统一：权重配置在 DB 中统一存储，评分时实时读取
- 可扩展：新增维度只需添加字段和权重，不影响已有逻辑
- 可重算：权重变更后支持批量重算所有贡献者分数
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 默认权重配置（与 ScoringWeightsConfig 保持一致）
_default_weights: dict[str, float] = {
    "merged_pr_weight": 10.0,
    "code_line_weight": 0.5,
    "review_weight": 3.0,
    "approval_weight": 5.0,
    "core_module_weight": 8.0,
    "files_changed_weight": 0.3,
    "bugfix_weight": 6.0,
    "feature_weight": 4.0,
    "active_duration_weight": 0.01,
    "revert_rate_weight": 8.0,
}


def get_default_weights() -> dict[str, float]:
    """获取默认权重配置"""
    return _default_weights.copy()


def calculate_contribution_score(
    metrics: dict[str, Any],
    weights: dict[str, float] | None = None,
    commit_type_counts: dict[str, int] | None = None,
    first_contribution_at: datetime | None = None,
    last_contribution_at: datetime | None = None,
) -> float:
    """
    根据贡献者指标和权重配置计算贡献分数。

    Args:
        metrics: 贡献者指标字典，包含:
            - merged_pr_count: 合并 PR 数
            - code_additions: 代码增加行数
            - code_deletions: 代码删除行数
            - review_count: Review 次数
            - approval_count: Approval 次数
            - core_module_count: 核心模块变更数
            - commit_count: 提交次数
            - files_changed_count: 变更文件数（可选）
        weights: 权重配置字典，不传则使用默认值
        commit_type_counts: commit 类型统计 {BugFix: N, Feature: N, ...}（可选）
        first_contribution_at: 首次贡献时间（可选，用于计算活跃持续时间）
        last_contribution_at: 最后贡献时间（可选，用于计算活跃持续时间）

    Returns:
        计算后的贡献分数
    """
    if weights is None:
        weights = get_default_weights()

    score = 0.0

    # 基础维度
    score += metrics.get("merged_pr_count", 0) * weights.get("merged_pr_weight", 10.0)
    score += metrics.get("code_additions", 0) * weights.get("code_line_weight", 0.5)
    score += metrics.get("review_count", 0) * weights.get("review_weight", 3.0)
    score += metrics.get("approval_count", 0) * weights.get("approval_weight", 5.0)
    score += metrics.get("core_module_count", 0) * weights.get("core_module_weight", 8.0)

    # 新增维度：文件变更数（衡量代码影响范围）
    files_changed = metrics.get("files_changed_count", 0)
    if files_changed > 0:
        score += files_changed * weights.get("files_changed_weight", 0.3)

    # 新增维度：BugFix 贡献（从 commit 类型统计中获取）
    if commit_type_counts:
        bugfix_count = commit_type_counts.get("BugFix", 0)
        score += bugfix_count * weights.get("bugfix_weight", 6.0)

        # Feature 贡献额外加分
        feature_count = commit_type_counts.get("Feature", 0)
        feature_weight = weights.get("feature_weight", 4.0)
        score += feature_count * feature_weight

    # 新增维度：活跃持续时间（天），贡献时间跨度越长活跃度越高
    if first_contribution_at and last_contribution_at:
        duration_days = (last_contribution_at - first_contribution_at).total_seconds() / 86400
        if duration_days > 0:
            score += duration_days * weights.get("active_duration_weight", 0.01)

    # revert 扣分：error_score 中已记录了 revert 次数，直接按权重扣减
    error_score = metrics.get("error_score", 0)
    if error_score > 0:
        revert_penalty = error_score * weights.get("revert_rate_weight", 8.0)
        score -= revert_penalty

    return round(score, 2)


async def recalculate_all_scores(
    db,
    weights: dict[str, float],
    repo_name: str | None = None,
) -> int:
    """
    批量重算所有贡献者的 contribution_score。

    Args:
        db: AsyncSession 数据库会话
        weights: 新的权重配置
        repo_name: 可选，指定仓库名

    Returns:
        重算的贡献者数量
    """
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import AsyncSession

    from contributor_board.quadrant_engine import determine_quadrant
    from infrastructure.persistence.models import (
        CommitClassification,
        Contributor,
        ContributorMetrics,
    )

    # 获取所有贡献者 metrics 记录
    stmt = select(ContributorMetrics)
    if repo_name:
        stmt = stmt.where(ContributorMetrics.repo_name == repo_name)

    result = await db.execute(stmt)
    metrics_rows = result.scalars().all()

    count = 0
    for m in metrics_rows:
        # 构建指标字典
        metrics = {
            "merged_pr_count": m.merged_pr_count or 0,
            "code_additions": m.code_additions or 0,
            "code_deletions": m.code_deletions or 0,
            "review_count": m.review_count or 0,
            "approval_count": m.approval_count or 0,
            "core_module_count": m.core_module_count or 0,
            "commit_count": m.commit_count or 0,
        }

        # 获取该贡献者的 commit 类型统计
        commit_stmt = (
            select(
                CommitClassification.commit_type,
                func.count(CommitClassification.id),
            )
            .where(CommitClassification.contributor_login == m.contributor_login)
            .group_by(CommitClassification.commit_type)
        )

        if repo_name:
            commit_stmt = commit_stmt.where(CommitClassification.repo_name == repo_name)

        commit_result = await db.execute(commit_stmt)
        commit_type_counts: dict[str, int] = {k: int(v) for k, v in commit_result.all()}

        # 获取贡献时间窗口
        contrib_stmt = select(Contributor).where(Contributor.login == m.contributor_login)
        contrib_result = await db.execute(contrib_stmt)
        contributor = contrib_result.scalar_one_or_none()

        first_at = contributor.first_contribution_at if contributor else None
        last_at = contributor.last_contribution_at if contributor else None

        # 计算新分数
        new_score = calculate_contribution_score(
            metrics,
            weights,
            commit_type_counts,
            first_at,
            last_at,
        )

        # 更新分数
        old_score = m.contribution_score or 0
        m.contribution_score = new_score

        # 重新计算四象限
        error_score = m.error_score or 0
        quadrant = determine_quadrant(
            new_score, error_score,
            threshold_x=30, threshold_y=2,
            y_direction="lower_better",
        )
        m.quadrant = quadrant

        count += 1
        if new_score != old_score:
            logger.debug(
                "Recalculated score for %s: %.2f -> %.2f",
                m.contributor_login, old_score, new_score,
            )

    await db.commit()
    logger.info("Recalculated scores for %d contributors", count)
    return count
