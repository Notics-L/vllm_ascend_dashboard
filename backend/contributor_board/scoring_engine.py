"""
贡献者评分引擎 — 7 维评分 + 四层分区。

对齐 vllm-ascend 社区评估模型。每个维度在 [0,1] 归一化后乘以权重(合计 100)，
得到 0-100 的 total_score，再据此落入四层分区。

7 维(默认权重):
    commit_count   25  提交数          (log 缩放)
    code_files     15  代码文件变更数   (log)
    doc_files      10  文档文件变更数   (log, 文档/方案贡献)
    pr_merged_rate 15  PR 合并率       (merged / total)
    pr_total       10  PR 总量         (log)
    active_days_90 15  近 90 天活跃天数 (线性, 0-90)
    span_days      10  贡献持续期(天)   (log)

四层分区(阈值基于社区分布校准):
    INCENTIVE  score >= 40
    ENCOURAGE  score >= 25 或 active_days_90 >= 15
    IMPROVE    score >= 15
    REPLACE    其余
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ── 默认权重(合计 100) ──
DEFAULT_WEIGHTS: dict[str, float] = {
    "commit_count": 25.0,
    "code_files": 15.0,
    "doc_files": 10.0,
    "pr_merged_rate": 15.0,
    "pr_total": 10.0,
    "active_days_90": 15.0,
    "span_days": 10.0,
}

# ── 四层分区阈值 ──
TIER_INCENTIVE = 40.0
TIER_ENCOURAGE = 25.0
TIER_IMPROVE = 15.0
TIER_ENCOURAGE_ACTIVE_DAYS = 15

# ── 归一化分母 ──
NORM_COMMIT = 100.0      # log10(1+100)=2 => 满分参考 ~100 commit
NORM_DOC = 20.0          # log10(1+20)~1.31
NORM_PR = 40.0           # log10(1+40)~1.61
NORM_SPAN = 720.0        # log10(1+720)~2.86
NORM_ACTIVE_DAYS = 90.0  # 线性满分 90 天


def log10p1(x: float) -> float:
    """log10(1+x)，对 0 安全。"""
    return math.log10(1.0 + max(0.0, x))


def get_default_weights() -> dict[str, float]:
    """获取默认 7 维权重配置。"""
    return dict(DEFAULT_WEIGHTS)


def get_tier_thresholds() -> dict[str, float]:
    """获取四层分区阈值。"""
    return {
        "incentive": TIER_INCENTIVE,
        "encourage": TIER_ENCOURAGE,
        "improve": TIER_IMPROVE,
        "encourage_active_days": TIER_ENCOURAGE_ACTIVE_DAYS,
    }


def calculate_score(metrics: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """
    计算 0-100 综合分。

    Args:
        metrics: 贡献者 7 维原始指标，键:
            commit_count, code_files, doc_files,
            pr_merged, pr_open, pr_closed,
            active_days_90, span_days
        weights: 权重配置, 不传则用默认

    Returns:
        total_score (0-100)
    """
    if weights is None:
        weights = get_default_weights()

    pr_merged = metrics.get("pr_merged", 0) or 0
    pr_open = metrics.get("pr_open", 0) or 0
    pr_closed = metrics.get("pr_closed", 0) or 0
    pr_total = pr_merged + pr_open + pr_closed
    merge_rate = (pr_merged / pr_total) if pr_total > 0 else 0.0

    parts = {
        "commit_count": log10p1(metrics.get("commit_count", 0)) / log10p1(NORM_COMMIT),
        "code_files": log10p1(metrics.get("code_files", 0)) / log10p1(NORM_COMMIT),
        "doc_files": log10p1(metrics.get("doc_files", 0)) / log10p1(NORM_DOC),
        "pr_merged_rate": merge_rate,
        "pr_total": log10p1(pr_total) / log10p1(NORM_PR),
        "active_days_90": min(1.0, (metrics.get("active_days_90", 0) or 0) / NORM_ACTIVE_DAYS),
        "span_days": log10p1(metrics.get("span_days", 0)) / log10p1(NORM_SPAN),
    }

    # 裁剪归一化值到 [0,1]
    parts = {k: min(1.0, max(0.0, v)) for k, v in parts.items()}

    total = sum(parts[k] * weights.get(k, 0.0) for k in DEFAULT_WEIGHTS)
    return round(min(100.0, max(0.0, total)), 2)


def assign_tier(score: float, active_days_90: int = 0) -> str:
    """根据分数与近 90 天活跃天数判断四层分区。"""
    if score >= TIER_INCENTIVE:
        return "INCENTIVE"
    if score >= TIER_ENCOURAGE or active_days_90 >= TIER_ENCOURAGE_ACTIVE_DAYS:
        return "ENCOURAGE"
    if score >= TIER_IMPROVE:
        return "IMPROVE"
    return "REPLACE"


def infer_role(metrics: dict[str, Any]) -> str:
    """
    推断贡献者角色(兼容 reviewer 定位)。

    代码少但 review/approval 多 → reviewer(检视者)；否则 developer。
    """
    review_count = metrics.get("review_count", 0) or 0
    approval_count = metrics.get("approval_count", 0) or 0
    code_files = metrics.get("code_files", 0) or 0
    committed_work = (metrics.get("code_files", 0) or 0) + (metrics.get("commit_count", 0) or 0)
    review_work = review_count + approval_count
    # 检视贡献达到代码贡献的 1/2 且检视 > 0 => reviewer
    if review_work > 0 and review_work * 2 >= max(1, committed_work):
        return "reviewer"
    if code_files > 0 or metrics.get("commit_count", 0):
        return "developer"
    return "contributor"


def calculate_contribution_score(
    metrics: dict[str, Any],
    weights: dict[str, float] | None = None,
    commit_type_counts: dict[str, int] | None = None,
) -> float:
    """旧接口兼容包装。

    旧 scoring_engine 接收 legacy 指标(merged_pr_count/code_additions/review_count 等)，
    返回旧权重的 contribution_score。重构后改为：
    - 把 legacy 指标映射为新 7 维近似(commit_count≈pr_count, code_files≈code_additions//2 等)
    - 用 calculate_score 计算
    - 结果裁剪到 [0, 100]

    commit_type_counts 参数保留兼容性, 不再使用。
    """
    if metrics is None:
        return 0.0
    _ = commit_type_counts  # 兼容旧调用, 未使用
    mapped = {
        # legacy -> new 7-dim 近似映射
        "commit_count": metrics.get("pr_count", metrics.get("commit_count", 0)) or 0,
        "code_files": (metrics.get("code_additions", 0) or 0) // 2,  # 粗粒度: 每 2 行变更 ≈ 1 文件
        "doc_files": metrics.get("doc_files", 0) or 0,
        "pr_merged": metrics.get("merged_pr_count", 0) or 0,
        "pr_open": metrics.get("pending_pr_count", 0) or 0,
        "pr_closed": metrics.get("closed_pr_count", 0) or 0,
        "active_days_90": metrics.get("active_days_90", 0) or 0,
        "span_days": metrics.get("span_days", 0) or 0,
        # reviewer 维度(供 infer_role 使用)
        "review_count": metrics.get("review_count", 0) or 0,
        "approval_count": metrics.get("approval_count", 0) or 0,
    }
    return calculate_score(mapped, weights)


async def recalculate_all_scores(
    db,
    weights: dict[str, float] | None = None,
    repo_name: str | None = None,
) -> int:
    """
    从 contributor_profiles 的 7 维原始指标批量重算 total_score / tier / role。

    Args:
        db: AsyncSession
        weights: 权重配置, 不传则用默认
        repo_name: 可选, 限定仓库

    Returns:
        重算的贡献者数量
    """
    if weights is None:
        weights = get_default_weights()

    from sqlalchemy import select

    from infrastructure.persistence.models import ContributorProfile

    stmt = select(ContributorProfile)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)
    result = await db.execute(stmt)
    profiles = result.scalars().all()

    count = 0
    for p in profiles:
        metrics = {
            "commit_count": p.commit_count or 0,
            "code_files": p.code_files or 0,
            "doc_files": p.doc_files or 0,
            "pr_merged": p.pr_merged or 0,
            "pr_open": p.pr_open or 0,
            "pr_closed": p.pr_closed or 0,
            "active_days_90": p.active_days_90 or 0,
            "span_days": p.span_days or 0,
            "review_count": p.review_count or 0,
            "approval_count": p.approval_count or 0,
        }
        p.total_score = calculate_score(metrics, weights)
        p.tier = assign_tier(p.total_score, p.active_days_90 or 0)
        p.role = infer_role(metrics)
        count += 1

    await db.commit()
    logger.info("Recalculated scores for %d contributor profiles", count)
    return count
