"""
贡献者四象限计算引擎（重构版）。

X 轴: total_score (7 维综合分 0-100, 越高越好)
Y 轴: review_ratio  (检视参与度 = review / (review+pr_total), 越高越好)
      —— 区分「写代码的人」与「做检视/方案的人」。

象限含义:
- INCENTIVE (激励区): 高 score 高 review_ratio   -> 高质量 + 强检视/协作
- IMPROVE   (改进区): 高 score 低 review_ratio   -> 高产出但偏单兵, 缺检视协作
- ENCOURAGE (鼓励区): 低 score 高 review_ratio   -> 检视/方案贡献者(Reviewer), 代码少
- REPLACE   (置换区): 低 score 低 review_ratio   -> 低产出且低协作, 需关注

阈值用中位数自适应切分, 保证四象限分布均衡。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


@dataclass
class QuadrantPoint:
    login: str
    avatar_url: str | None
    x_value: float
    y_value: float
    quadrant: str
    merged_pr_count: int
    is_community: bool
    review_ratio: float = 0.0


def compute_low_activity_score(
    pending_pr: int = 0,
    last_activity_hours: float | None = None,
) -> float:
    """旧接口兼容：计算低活跃度分 (0-100, 越低越活跃)。

    重构后四象限固定使用 review_ratio 作为 Y 轴, 此函数仅用于兼容
    pr_enrich.get_pr_efficiency_metrics 等旧调用点。
    """
    score = 0.0
    # 积压 PR: 每个 +5 分, 上限 30
    score += min(30.0, (pending_pr or 0) * 5.0)
    # 距上次活动小时: 每 24h +5 分, 上限 50
    if last_activity_hours is not None:
        score += min(50.0, max(0.0, last_activity_hours) * (5.0 / 24.0))
    return round(min(100.0, max(0.0, score)), 2)


def determine_quadrant(
    x_value: float,
    y_value: float,
    threshold_x: float,
    threshold_y: float,
    y_direction: str = "higher_better",
    **_kwargs: Any,
) -> str:
    """
    判定象限。y_direction 默认 higher_better(review_ratio 越高越好)。
    保持旧签名兼容(支持 threshold_x/threshold_y 位置参数与 y_direction)。
    """
    high_x = x_value >= threshold_x
    if y_direction == "lower_better":
        high_y = y_value >= threshold_y
        low_y = y_value < threshold_y
    else:
        high_y = y_value >= threshold_y
        low_y = y_value < threshold_y

    if high_x and high_y:
        return "INCENTIVE"
    elif high_x and low_y:
        return "IMPROVE"
    elif not high_x and high_y:
        return "ENCOURAGE"
    else:
        return "REPLACE"


def calculate_quadrants(
    contributors_metrics: list[dict[str, Any]],
    y_axis_type: str | None = None,
) -> tuple[list[QuadrantPoint], float, float]:
    """
    计算所有贡献者的四象限位置。

    Args:
        contributors_metrics: 每项至少含:
            login, total_score(或 contribution_score), review_ratio(可选),
            avatar_url, merged_pr_count, is_community
        y_axis_type: 兼容性保留, 重构后固定使用 review_ratio

    Returns:
        (quadrant_points, threshold_x, threshold_y)
    """
    count = len(contributors_metrics)
    if count == 0:
        return [], 0.0, 0.0

    logger.info("计算 %d 位贡献者的四象限 (X=total_score, Y=review_ratio)", count)

    x_values = [
        (m.get("total_score", m.get("contribution_score", 0)) or 0) for m in contributors_metrics
    ]
    y_values = [
        (m.get("review_ratio", 0) or 0) for m in contributors_metrics
    ]

    threshold_x = _median(x_values)
    threshold_y = _median(y_values)
    logger.info("阈值 X(综合分)=%.2f Y(review_ratio)=%.4f", threshold_x, threshold_y)

    quadrant_counts: dict[str, int] = {"INCENTIVE": 0, "IMPROVE": 0, "ENCOURAGE": 0, "REPLACE": 0}
    quadrant_points: list[QuadrantPoint] = []

    for m in contributors_metrics:
        xv = m.get("total_score", m.get("contribution_score", 0)) or 0
        yv = m.get("review_ratio", 0) or 0
        quadrant = determine_quadrant(xv, yv, threshold_x, threshold_y, y_direction="higher_better")
        quadrant_points.append(QuadrantPoint(
            login=m["login"],
            avatar_url=m.get("avatar_url"),
            x_value=xv,
            y_value=yv,
            quadrant=quadrant,
            merged_pr_count=m.get("merged_pr_count", m.get("pr_merged", 0)) or 0,
            is_community=m.get("is_community", False),
            review_ratio=yv,
        ))
        quadrant_counts[quadrant] += 1

    for name, c in quadrant_counts.items():
        logger.info("象限 %s: %d 人", name, c)

    return quadrant_points, threshold_x, threshold_y
