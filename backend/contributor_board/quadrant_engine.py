"""
贡献者四象限计算引擎

根据贡献者的多维度指标将其划分到四个象限，支持：
- X轴: contribution_score（贡献分数）
- Y轴: error_score（出错分数）, review_count（检视次数，标准化）, merged_pr_count（合并次数，标准化）

象限含义：
- INCENTIVE (激励):  高 X 低 Y -> 高质量贡献
- IMPROVE  (改进):  高 X 高 Y -> 需改进
- ENCOURAGE(鼓励):  低 X 低 Y -> 潜力新人
- REPLACE  (置换):  低 X 高 Y -> 需关注

当 Y 轴区分度不足时（所有值相同或集中），自动使用标准差/百分位切分。
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Y轴指标类型定义
AXIS_TYPES = {
    "error_score": {
        "label": "出错评分",
        "direction": "lower_better",  # 越低越好
    },
    "review_count": {
        "label": "检视次数",
        "direction": "higher_better",
    },
    "merged_pr_count": {
        "label": "PR合并数",
        "direction": "higher_better",
    },
    "code_additions": {
        "label": "代码行数",
        "direction": "higher_better",
    },
}


@dataclass
class QuadrantPoint:
    login: str
    avatar_url: str | None
    x_value: float
    y_value: float
    quadrant: str
    merged_pr_count: int
    is_community: bool


def _compute_threshold(
    values: list[float],
) -> float:
    """
    计算分割线阈值。优先使用百分位切分，当值域区分度不足时自适应调整。

    策略：
    1. 如果所有值相同 -> 用 0，使所有点在上方
    2. 如果值域极窄（标准差/均值 < 0.1）-> 用 1/3 百分位点
    3. 否则 -> 使用中位数
    """
    n = len(values)
    if n == 0:
        return 0.0

    unique = set(values)
    if len(unique) <= 1:
        # 所有值相同，分割线设为 0，所有人归为低侧
        return 0.0

    median = statistics.median(values)
    mean = statistics.mean(values)

    if mean > 0:
        std = statistics.stdev(values) if n > 1 else 0
        cv = std / mean  # 变异系数
    else:
        cv = 0

    if cv < 0.15:
        # 值域集中，用 1/3 分位点切分（33% vs 67%）
        sorted_vals = sorted(values)
        idx = n // 3
        if idx == 0:
            idx = 1
        return sorted_vals[idx]

    return median


def determine_quadrant(
    x_value: float,
    y_value: float,
    threshold_x: float,
    threshold_y: float,
    y_direction: str = "lower_better",
) -> str:
    """
    根据分数和自适应阈值判定象限。

    Y 轴 direction:
    - lower_better: Y 值低为正面（如 error_score）
    - higher_better: Y 值高为正面（如 review_count）
    """
    high_x = x_value >= threshold_x

    if y_direction == "lower_better":
        low_y = y_value < threshold_y
        high_y = y_value >= threshold_y
    else:
        high_y = y_value >= threshold_y
        low_y = y_value < threshold_y

    if high_x and low_y:
        return "INCENTIVE"
    elif high_x and high_y:
        return "IMPROVE"
    elif not high_x and low_y:
        return "ENCOURAGE"
    else:
        return "REPLACE"


def calculate_quadrants(
    contributors_metrics: list[dict[str, Any]],
    y_axis_type: str = "error_score",
) -> tuple[list[QuadrantPoint], float, float]:
    """
    计算所有贡献者的四象限位置。

    Args:
        contributors_metrics: 贡献者指标列表，每项包含:
            - login, avatar_url, contribution_score, error_score,
              review_count, merged_pr_count, code_additions, is_community
        y_axis_type: Y轴指标类型 (error_score/review_count/merged_pr_count/code_additions)

    Returns:
        (quadrant_points, threshold_x, threshold_y)
    """
    count = len(contributors_metrics)
    logger.info("开始计算 %d 位贡献者的四象限位置 (Y轴=%s)", count, y_axis_type)

    if count == 0:
        logger.warning("贡献者列表为空，返回空结果")
        return [], 0.0, 0.0

    config = AXIS_TYPES.get(y_axis_type, AXIS_TYPES["error_score"])
    y_direction = config["direction"]

    x_values = [m.get("contribution_score", 0) for m in contributors_metrics]
    y_key = y_axis_type
    y_values = [m.get(y_key, 0) for m in contributors_metrics]

    threshold_x = _compute_threshold(x_values)
    threshold_y = _compute_threshold(y_values)

    logger.info(
        "分割阈值 - X轴(贡献): %.2f, Y轴(%s): %.2f, direction=%s",
        threshold_x, y_axis_type, threshold_y, y_direction,
    )

    if count == 1:
        m = contributors_metrics[0]
        point = QuadrantPoint(
            login=m["login"],
            avatar_url=m.get("avatar_url"),
            x_value=m.get("contribution_score", 0),
            y_value=m.get(y_key, 0),
            quadrant="ENCOURAGE",
            merged_pr_count=m.get("merged_pr_count", 0),
            is_community=m.get("is_community", False),
        )
        logger.info("仅1位贡献者 %s，归入 ENCOURAGE 象限", point.login)
        return [point], threshold_x, threshold_y

    quadrant_points: list[QuadrantPoint] = []
    quadrant_counts: dict[str, int] = {
        "INCENTIVE": 0,
        "IMPROVE": 0,
        "ENCOURAGE": 0,
        "REPLACE": 0,
    }

    for m in contributors_metrics:
        login = m["login"]
        xv = m.get("contribution_score", 0)
        yv = m.get(y_key, 0)

        quadrant = determine_quadrant(xv, yv, threshold_x, threshold_y, y_direction)

        point = QuadrantPoint(
            login=login,
            avatar_url=m.get("avatar_url"),
            x_value=xv,
            y_value=yv,
            quadrant=quadrant,
            merged_pr_count=m.get("merged_pr_count", 0),
            is_community=m.get("is_community", False),
        )
        quadrant_points.append(point)
        quadrant_counts[quadrant] += 1

    for name, q_count in quadrant_counts.items():
        logger.info("象限 %s: %d 位贡献者", name, q_count)

    logger.info("四象限计算完成，共 %d 位贡献者", len(quadrant_points))

    return quadrant_points, threshold_x, threshold_y
