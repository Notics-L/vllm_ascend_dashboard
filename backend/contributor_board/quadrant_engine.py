"""
贡献者四象限计算引擎

根据贡献者的"贡献数量"和"出错数量"将其划分到四个象限：
- 第一象限 (INCENTIVE - 激励): 高贡献、低出错 -> 维持+激励
- 第二象限 (IMPROVE - 改进): 高贡献、高出错 -> 需要改进
- 第三象限 (ENCOURAGE - 鼓励): 低贡献、低出错 -> 新手鼓励
- 第四象限 (REPLACE - 置换): 低贡献、高出错 -> 观察/限制
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class QuadrantPoint:
    login: str
    avatar_url: str | None
    contribution_score: float
    error_score: float
    quadrant: str  # INCENTIVE/IMPROVE/ENCOURAGE/REPLACE
    merged_pr_count: int
    is_community: bool


def determine_quadrant(
    contribution_score: float,
    error_score: float,
    quad_x: float,
    quad_y: float,
) -> str:
    """
    根据分数和中位数判定象限

    Args:
        contribution_score: 贡献者加权贡献分数
        error_score: 贡献者加权出错分数
        quad_x: X轴中位数（贡献分数中位数）
        quad_y: Y轴中位数（出错分数中位数）

    Returns:
        象限名称: INCENTIVE/IMPROVE/ENCOURAGE/REPLACE
    """
    high_contribution = contribution_score >= quad_x
    low_error = error_score < quad_y

    if high_contribution and low_error:
        return "INCENTIVE"
    elif high_contribution and not low_error:
        return "IMPROVE"
    elif not high_contribution and low_error:
        return "ENCOURAGE"
    else:
        return "REPLACE"


def calculate_quadrants(
    contributors_metrics: list[dict[str, Any]],
) -> tuple[list[QuadrantPoint], float, float]:
    """
    计算所有贡献者的四象限位置

    Args:
        contributors_metrics: 贡献者指标列表，每项包含:
            - login: str
            - avatar_url: str | None
            - contribution_score: float
            - error_score: float
            - merged_pr_count: int
            - is_community: bool

    Returns:
        (quadrant_points, quad_x_median, quad_y_median)
    """
    count = len(contributors_metrics)
    logger.info("开始计算 %d 位贡献者的四象限位置", count)

    # 边界情况：空列表
    if count == 0:
        logger.warning("贡献者列表为空，返回空结果")
        return [], 0.0, 0.0

    # 提取分数列表用于计算中位数
    contribution_scores = [m["contribution_score"] for m in contributors_metrics]
    error_scores = [m["error_score"] for m in contributors_metrics]

    quad_x_median = statistics.median(contribution_scores)
    quad_y_median = statistics.median(error_scores)

    logger.info(
        "计算中位数 - X轴(贡献): %.2f, Y轴(出错): %.2f",
        quad_x_median,
        quad_y_median,
    )

    # 特殊处理：只有一个贡献者时，直接归入 ENCOURAGE 象限
    if count == 1:
        m = contributors_metrics[0]
        point = QuadrantPoint(
            login=m["login"],
            avatar_url=m.get("avatar_url"),
            contribution_score=m["contribution_score"],
            error_score=m["error_score"],
            quadrant="ENCOURAGE",
            merged_pr_count=m.get("merged_pr_count", 0),
            is_community=m.get("is_community", False),
        )
        logger.info("仅1位贡献者 %s，归入 ENCOURAGE 象限", point.login)
        return [point], quad_x_median, quad_y_median

    # 计算每个贡献者的象限
    quadrant_points: list[QuadrantPoint] = []
    quadrant_counts: dict[str, int] = {
        "INCENTIVE": 0,
        "IMPROVE": 0,
        "ENCOURAGE": 0,
        "REPLACE": 0,
    }

    for m in contributors_metrics:
        login = m["login"]
        cs = m["contribution_score"]
        es = m["error_score"]

        quadrant = determine_quadrant(cs, es, quad_x_median, quad_y_median)

        point = QuadrantPoint(
            login=login,
            avatar_url=m.get("avatar_url"),
            contribution_score=cs,
            error_score=es,
            quadrant=quadrant,
            merged_pr_count=m.get("merged_pr_count", 0),
            is_community=m.get("is_community", False),
        )
        quadrant_points.append(point)
        quadrant_counts[quadrant] += 1

    # 记录各象限统计
    for quadrant_name, q_count in quadrant_counts.items():
        logger.info("象限 %s: %d 位贡献者", quadrant_name, q_count)

    logger.info("四象限计算完成，共 %d 位贡献者", len(quadrant_points))

    return quadrant_points, quad_x_median, quad_y_median
