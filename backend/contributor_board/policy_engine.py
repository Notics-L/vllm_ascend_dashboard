"""
自动策略引擎

根据贡献者的象限位置、指标、趋势，生成自动化策略建议：
- 自动执行策略 (auto): 系统可自动执行的动作（如 CI 配置、标签设置）
- 建议执行策略 (suggestion): 需要人工确认的建议（如推荐 Issue、配对 Mentor）
- 需关注策略 (alert): 需要立即关注的警告（如权限限制）
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
    COMMEND_ACTIVE = "commend_active"
    ISSUE_TRIAGE_ASSIST = "issue_triage_assist"
    SKILL_GROWTH_PLAN = "skill_growth_plan"
    CODE_QUALITY_TRACK = "code_quality_track"


class PolicySeverity(str, Enum):
    """策略严重程度"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PolicyExecution(str, Enum):
    """策略执行方式"""
    AUTO = "auto"              # 系统自动执行
    SUGGESTION = "suggestion"  # 建议执行人工确认
    ALERT = "alert"            # 需要立即关注


@dataclass
class PolicyItem:
    """单条策略建议"""
    action: PolicyAction
    severity: PolicySeverity
    execution: PolicyExecution
    description: str
    reason: str
    impact_level: str = ""     # 影响等级描述，如 "高", "中", "低"


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
    def auto_policies(self) -> list[PolicyItem]:
        return [p for p in self.policies if p.execution == PolicyExecution.AUTO]

    @property
    def suggestion_policies(self) -> list[PolicyItem]:
        return [p for p in self.policies if p.execution == PolicyExecution.SUGGESTION]

    @property
    def alert_policies(self) -> list[PolicyItem]:
        return [p for p in self.policies if p.execution == PolicyExecution.ALERT]

    @property
    def has_critical(self) -> bool:
        return any(p.severity == PolicySeverity.CRITICAL for p in self.policies)

    @property
    def has_warning(self) -> bool:
        return any(p.severity == PolicySeverity.WARNING for p in self.policies)


# ── 策略规则配置 ──────────────────────────────────────────────
# 每条规则: (action, min_score, min_error, min_review, meta)

# 激励区策略 (INCENTIVE): 高贡献 + 低错误
INCENTIVE_RULES = [
    (
        PolicyAction.MAINTAINER_CANDIDATE, 100.0, 0, 10,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.SUGGESTION,
            "description": "提名 Maintainer 候选人",
            "reason_template": "贡献分数 {score:.1f} 且 Review {reviews} 次，满足 Maintainer 候选条件",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.HIGH_PRIORITY_MERGE, 50.0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.AUTO,
            "description": "自动开启 PR 高优先级合并通道",
            "reason_template": "贡献分数 {score:.1f}，系统已为其 PR 标记高优先级",
            "impact_level": "中",
        },
    ),
    (
        PolicyAction.COMMEND_ACTIVE, 30.0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.AUTO,
            "description": "自动生成贡献者 commend 标签",
            "reason_template": "贡献分数 {score:.1f}，系统已颁发 '活跃贡献者' 徽章",
            "impact_level": "低",
        },
    ),
    (
        PolicyAction.ISSUE_TRIAGE_ASSIST, 80.0, 0, 5,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.SUGGESTION,
            "description": "邀请参与 Issue 分派决策",
            "reason_template": "贡献分数 {score:.1f} 且具备 Rich Review 经验，适合参与 Issue 分派",
            "impact_level": "中",
        },
    ),
]

# 改进区策略 (IMPROVE): 高贡献 + 高错误
IMPROVE_RULES = [
    (
        PolicyAction.MANDATORY_FULL_CI, 0, 5, 0,
        {
            "severity": PolicySeverity.WARNING,
            "execution": PolicyExecution.AUTO,
            "description": "自动强制全量 CI 测试",
            "reason_template": "出错分数 {error:.1f} 较高，系统已为其 PR 启用全量 CI 保障",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.EXTRA_REVIEW, 0, 2, 0,
        {
            "severity": PolicySeverity.WARNING,
            "execution": PolicyExecution.AUTO,
            "description": "自动要求额外 Review",
            "reason_template": "出错分数 {error:.1f}，系统已要求其 PR 至少 2 人 Review 通过",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.CODE_QUALITY_TRACK, 0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.SUGGESTION,
            "description": "生成代码质量改进计划",
            "reason_template": "贡献分数 {score:.1f} 但出错分数 {error:.1f}，建议关注代码质量",
            "impact_level": "中",
        },
    ),
]

# 鼓励区策略 (ENCOURAGE): 低贡献 + 低错误
ENCOURAGE_RULES = [
    (
        PolicyAction.RECOMMEND_BEGINNER_ISSUE, 0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.AUTO,
            "description": "自动推送 'Good First Issue' 推荐列表",
            "reason_template": "贡献分数 {score:.1f}，系统已推送 3 个适合新手的好 issue",
            "impact_level": "中",
        },
    ),
    (
        PolicyAction.PAIR_MENTOR, 0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.SUGGESTION,
            "description": "建议配对 Mentor 指导",
            "reason_template": "贡献分数 {score:.1f}，配对 Mentor 可加速成长轨迹",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.SKILL_GROWTH_PLAN, 0, 0, 0,
        {
            "severity": PolicySeverity.INFO,
            "execution": PolicyExecution.SUGGESTION,
            "description": "生成个性化成长路径",
            "reason_template": "贡献分数 {score:.1f}，系统生成了 4 周成长路径计划",
            "impact_level": "中",
        },
    ),
]

# 置换区策略 (REPLACE): 低贡献 + 高错误
REPLACE_RULES = [
    (
        PolicyAction.PERMISSION_RESTRICTION, 0, 10, 0,
        {
            "severity": PolicySeverity.CRITICAL,
            "execution": PolicyExecution.ALERT,
            "description": "限制直接合并权限",
            "reason_template": "出错分数 {error:.1f} 过高，建议限制 write 权限",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.DOUBLE_REVIEW, 0, 0, 0,
        {
            "severity": PolicySeverity.CRITICAL,
            "execution": PolicyExecution.AUTO,
            "description": "自动启用双重 Review 机制",
            "reason_template": "连续多次 Revert，系统已要求所有 PR 双重 Review 才能合并",
            "impact_level": "高",
        },
    ),
    (
        PolicyAction.CODE_QUALITY_TRACK, 0, 0, 0,
        {
            "severity": PolicySeverity.WARNING,
            "execution": PolicyExecution.SUGGESTION,
            "description": "要求制定代码质量整改计划",
            "reason_template": "出错分数 {error:.1f} 且贡献分数 {score:.1f}，需要整改",
            "impact_level": "高",
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

    rules = rules_map.get(quadrant, ENCOURAGE_RULES)
    for action, min_score, min_error, min_reviews, meta in rules:
        if contribution_score >= min_score and error_score >= min_error and review_count >= min_reviews:
            reason = meta["reason_template"].format(
                score=contribution_score,
                error=error_score,
                reviews=review_count,
            )
            result.policies.append(PolicyItem(
                action=action,
                severity=meta["severity"],
                execution=meta["execution"],
                description=meta["description"],
                reason=reason,
                impact_level=meta.get("impact_level", ""),
            ))

    # 跨象限通用建议：当分数处于边界时，给出过渡性建议
    if contribution_score > 0 and contribution_score < 15:
        result.policies.append(PolicyItem(
            action=PolicyAction.SKILL_GROWTH_PLAN,
            severity=PolicySeverity.INFO,
            execution=PolicyExecution.SUGGESTION,
            description="建议参与代码阅读计划",
            reason=f"当前贡献分数 {contribution_score:.1f}，阅读 PR 可帮助快速入门",
            impact_level="低",
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
                "review_count": int(m.review_count or 0),
                "merged_pr_count": int(m.merged_pr_count or 0),
                "code_additions": int(m.code_additions or 0),
            }
            for m in metrics_list
        ],
    }
