"""
自动策略引擎

根据贡献者的象限位置和指标，生成自动化策略建议：
- INCENTIVE (激励区): Maintainer 候选、高优先级合并
- IMPROVE   (改进区): PR 标记额外 Review、强制 CI 全量测试
- ENCOURAGE (鼓励区): 推荐新手 Issue、配对 Mentor
- REPLACE   (置换区): 权限限制、双重 Review
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import Integer, and_, desc, func, select

from infrastructure.persistence.models import (
    CommitClassification,
    Contributor,
    ContributorMetrics,
    ImageBuildPlan,
)

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    """策略动作类型"""
    MAINTAINER_CANDIDATE = "maintainer_candidate"
    HIGH_PRIORITY_MERGE = "high_priority_merge"
    EXTRA_REVIEW = "extra_review"
    MANDATORY_FULL_CI = "mandatory_full_ci"
    RECOMMEND_BEGINNER_ISSUE = "recommend_beginner_issue"
    PAIR_MENTOR = "pair_mentor"
    PERMISSION_RESTRICTION = "permission_restriction"
    DOUBLE_REVIEW = "double_review"


class PolicySeverity(str, Enum):
    """策略严重程度"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class PolicyItem:
    """单条策略建议"""
    action: PolicyAction
    severity: PolicySeverity
    description: str
    reason: str


@dataclass
class PolicyResult:
    """策略评估结果"""
    login: str
    quadrant: str
    contribution_score: float
    error_score: float
    policies: list[PolicyItem] = field(default_factory=list)
    evaluated_at: datetime | None = None

    @property
    def has_critical(self) -> bool:
        return any(p.severity == PolicySeverity.CRITICAL for p in self.policies)

    @property
    def has_warning(self) -> bool:
        return any(p.severity == PolicySeverity.WARNING for p in self.policies)


# ── 策略规则配置 ──────────────────────────────────────────────

# 激励区策略 (INCENTIVE): 高贡献 + 低错误
INCENTIVE_RULES: list[tuple[str, float, float, list[dict[str, Any]]]] = [
    # (action, min_score, min_review_count, meta)
    (
        PolicyAction.MAINTAINER_CANDIDATE,
        100.0,
        10,
        {
            "severity": PolicySeverity.INFO,
            "description": "建议提升为 Maintainer 候选人",
            "reason_template": "贡献分数 {score:.1f} 且 Review {reviews} 次，满足 Maintainer 候选条件",
        },
    ),
    (
        PolicyAction.HIGH_PRIORITY_MERGE,
        50.0,
        0,
        {
            "severity": PolicySeverity.INFO,
            "description": "PR 可享受高优先级合并",
            "reason_template": "贡献分数 {score:.1f}，属于核心贡献者",
        },
    ),
]

# 改进区策略 (IMPROVE): 高贡献 + 高错误
IMPROVE_RULES: list[tuple[str, float, float, list[dict[str, Any]]]] = [
    (
        PolicyAction.EXTRA_REVIEW,
        0,
        0,
        {
            "severity": PolicySeverity.WARNING,
            "description": "PR 需要额外 Review",
            "reason_template": "出错分数 {error:.1f} 较高，建议至少 2 人 Review",
        },
    ),
    (
        PolicyAction.MANDATORY_FULL_CI,
        0,
        0,
        {
            "severity": PolicySeverity.WARNING,
            "description": "强制全量 CI 测试",
            "reason_template": "历史记录中存在错误被合并，需要全量 CI 保障",
        },
    ),
]

# 鼓励区策略 (ENCOURAGE): 低贡献 + 低错误
ENCOURAGE_RULES: list[tuple[str, float, float, list[dict[str, Any]]]] = [
    (
        PolicyAction.RECOMMEND_BEGINNER_ISSUE,
        0,
        0,
        {
            "severity": PolicySeverity.INFO,
            "description": "推荐新手友好 Issue",
            "reason_template": "贡献分数 {score:.1f}，建议从 good first issue 开始",
        },
    ),
    (
        PolicyAction.PAIR_MENTOR,
        0,
        0,
        {
            "severity": PolicySeverity.INFO,
            "description": "配对 Mentor 指导",
            "reason_template": "贡献分数 {score:.1f}，配对 Mentor 可加速成长",
        },
    ),
]

# 置换区策略 (REPLACE): 低贡献 + 高错误
REPLACE_RULES: list[tuple[str, float, float, list[dict[str, Any]]]] = [
    (
        PolicyAction.PERMISSION_RESTRICTION,
        0,
        0,
        {
            "severity": PolicySeverity.CRITICAL,
            "description": "限制直接合并权限",
            "reason_template": "出错分数 {error:.1f} 过高，限制 write 权限",
        },
    ),
    (
        PolicyAction.DOUBLE_REVIEW,
        0,
        0,
        {
            "severity": PolicySeverity.CRITICAL,
            "description": "所有 PR 需双重 Review",
            "reason_template": "连续多次 Revert，需要双重 Review 才能合并",
        },
    ),
]


def evaluate_policy(
    login: str,
    quadrant: str,
    contribution_score: float,
    error_score: float,
    review_count: int = 0,
) -> PolicyResult:
    """
    根据贡献者信息评估策略

    Args:
        login: 用户名
        quadrant: 象限
        contribution_score: 贡献分数
        error_score: 出错分数
        review_count: review 数量

    Returns:
        PolicyResult: 策略评估结果
    """
    result = PolicyResult(
        login=login,
        quadrant=quadrant,
        contribution_score=contribution_score,
        error_score=error_score,
        evaluated_at=datetime.now(UTC),
    )

    rules_map = {
        "INCENTIVE": INCENTIVE_RULES,
        "IMPROVE": IMPROVE_RULES,
        "ENCOURAGE": ENCOURAGE_RULES,
        "REPLACE": REPLACE_RULES,
    }

    rules = rules_map.get(quadrant, [])
    for action, min_score, min_reviews, meta in rules:
        if contribution_score >= min_score and review_count >= min_reviews:
            reason = meta["reason_template"].format(
                score=contribution_score,
                error=error_score,
                reviews=review_count,
            )
            result.policies.append(PolicyItem(
                action=action,
                severity=meta["severity"],
                description=meta["description"],
                reason=reason,
            ))

    return result


async def evaluate_policy_from_db(
    db,
    login: str,
) -> PolicyResult | None:
    """
    从数据库中获取贡献者最新指标并评估策略

    Args:
        db: 数据库会话
        login: 用户名

    Returns:
        PolicyResult 或 None (贡献者不存在)
    """
    stmt = (
        select(Contributor, ContributorMetrics)
        .outerjoin(
            ContributorMetrics,
            Contributor.id == ContributorMetrics.contributor_id,
        )
        .where(Contributor.login == login)
        .order_by(desc(ContributorMetrics.updated_at))
        .limit(1)
    )

    result = await db.execute(stmt)
    row = result.one_or_none()

    if not row:
        return None

    contributor, metrics = row

    score = float(metrics.contribution_score or 0) if metrics else 0
    error = float(metrics.error_score or 0) if metrics else 0
    reviews = int(metrics.review_count or 0) if metrics else 0
    quadrant = metrics.quadrant if metrics else "ENCOURAGE"

    return evaluate_policy(
        login=login,
        quadrant=quadrant,
        contribution_score=score,
        error_score=error,
        review_count=reviews,
    )


def get_trend_data(
    login: str,
    metrics_list: list[Any],
) -> dict[str, Any]:
    """
    从 ContributorMetrics 列表生成趋势数据

    Args:
        login: 用户名
        metrics_list: ContributorMetrics 对象列表

    Returns:
        包含趋势点的字典
    """
    if not metrics_list:
        return {
            "login": login,
            "commit_trend": [],
            "type_ratio_trend": [],
            "revert_rate_trend": [],
            "build_success_trend": [],
        }

    return {
        "login": login,
        "commit_trend": [
            {
                "date": str(m.period_start),
                "merged_pr_count": int(m.merged_pr_count or 0),
                "code_additions": int(m.code_additions or 0),
                "code_deletions": int(m.code_deletions or 0),
            }
            for m in metrics_list
        ],
        "review_trend": [
            {
                "date": str(m.period_start),
                "review_count": int(m.review_count or 0),
                "approval_count": int(m.approval_count or 0),
            }
            for m in metrics_list
        ],
        "score_trend": [
            {
                "date": str(m.period_start),
                "contribution_score": float(m.contribution_score or 0),
                "error_score": float(m.error_score or 0),
            }
            for m in metrics_list
        ],
    }
