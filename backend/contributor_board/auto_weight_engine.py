from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy import select as sa_select

logger = logging.getLogger(__name__)

# 权重维度注册表 — 维度键名到指标字段名的映射
DimensionRegistry = {
    "merged_pr_weight": "merged_pr_count",
    "code_line_weight": "code_additions",
    "review_weight": "review_count",
    "approval_weight": "approval_count",
    "core_module_weight": "core_module_count",
    "files_changed_weight": "files_changed_count",
    "bugfix_weight": "commit_count",
    "feature_weight": "commit_count",
    "active_duration_weight": "commit_count",
    "revert_rate_weight": "error_score",
}

DIMENSIONS = list(DimensionRegistry.keys())


async def compute_weights_from_db(db: Any, repo_name: str | None = None) -> tuple[dict[str, float], float, dict[str, Any]]:
    """从数据库查询 ContributorMetrics 并计算权重（封装完整流程）"""
    from infrastructure.persistence.models import ContributorMetrics

    stmt = sa_select(ContributorMetrics)
    if repo_name:
        stmt = stmt.where(ContributorMetrics.repo_name == repo_name)

    result = await db.execute(stmt)
    metrics_list = result.scalars().all()

    metrics_data = [m.to_weight_dict() for m in metrics_list]
    return compute_weights(metrics_data)


def compute_weights(metrics_data: list[dict[str, Any]]) -> tuple[dict[str, float], float, dict[str, Any]]:
    """
    基于熵权法计算各贡献维度的权重。

    流程：
    1. IQR 极值截断 — 防止极端高贡献者主导权重
    2. Min-Max 归一化 — 消除量纲差异
    3. 信息熵计算 — 离散程度高的维度信息量大，获得更高权重
    4. 权重 = 冗余度 / 总冗余度
    5. 变异系数法交叉验证，得到置信度

    Args:
        metrics_data: N 个贡献者的指标列表，每个元素为:
            {dimension_key: value, ...} (e.g. {"merged_pr_count": 5, "code_additions": 100})

    Returns:
        (weights_dict, confidence, data_summary)
    """
    n = len(metrics_data)
    if n == 0:
        return {d: 1.0 / len(DIMENSIONS) for d in DIMENSIONS}, 0.0, {"contributor_count": 0}

    values: dict[str, list[float]] = {}
    for d in DIMENSIONS:
        field = DimensionRegistry[d]
        raw = [float(m.get(field, 0)) for m in metrics_data]
        values[d] = _preprocess(raw)
        values[d] = [max(v, 1e-10) for v in values[d]]

    normalized = _normalize(values, DIMENSIONS)

    entropy = _compute_entropy(normalized, DIMENSIONS, n)

    redundancy = {d: 1.0 - entropy[d] for d in DIMENSIONS}
    total = sum(redundancy.values())

    if total < 1e-10:
        weights = {d: 1.0 / len(DIMENSIONS) for d in DIMENSIONS}
    else:
        weights = {d: redundancy[d] / total for d in DIMENSIONS}

    weights = {d: round(w, 4) for d, w in weights.items()}

    cv_weights = _compute_cv_weights(values, DIMENSIONS)
    
    # 综合置信度：熵分布均匀性 + 方法间相关性 + 样本量 + 维度稳定性
    entropy_conf = _entropy_distribution_confidence(weights)
    method_conf = _pearson_correlation(
        [weights[d] for d in DIMENSIONS],
        [cv_weights[d] for d in DIMENSIONS],
    )
    if method_conf < 0:
        method_conf = 0.0
    # 样本量因子：50人即达到高置信度，200人满分
    sample_factor = min(1.0, n / 200)
    sample_bonus = min(0.15, n / 500)
    # 维度稳定性：有多少维度有有效的数据区分度（std > 0）
    stable_dims = sum(1 for d in DIMENSIONS if len(set(values[d])) > 1)
    dimension_factor = stable_dims / len(DIMENSIONS)
    
    confidence = round(
        0.25 * entropy_conf
        + 0.25 * method_conf
        + 0.20 * sample_factor
        + 0.20 * dimension_factor
        + sample_bonus,
        4,
    )
    # 保底：有基础数据时不低于 0.40
    if n >= 10:
        confidence = max(0.40, confidence)
    elif n >= 3:
        confidence = max(0.25, confidence)

    data_summary = _build_data_summary(values, DIMENSIONS, n)

    return weights, confidence, data_summary


def _preprocess(raw: list[float]) -> list[float]:
    """IQR 极值截断"""
    if len(raw) < 2:
        return raw

    sorted_vals = sorted(raw)
    n = len(sorted_vals)
    Q1 = _percentile(sorted_vals, 25)
    Q3 = _percentile(sorted_vals, 75)
    IQR = Q3 - Q1

    if IQR == 0:
        return raw

    clip_min = max(0, Q1 - 1.5 * IQR)
    clip_max = Q3 + 1.5 * IQR
    return [max(clip_min, min(clip_max, v)) for v in raw]


def _percentile(sorted_vals: list[float], p: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    k = (n - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _normalize(values: dict[str, list[float]], dims: list[str]) -> dict[str, list[float]]:
    result = {}
    for d in dims:
        v = values[d]
        min_v, max_v = min(v), max(v)
        if max_v - min_v < 1e-10:
            result[d] = [0.5] * len(v)
        else:
            result[d] = [(x - min_v) / (max_v - min_v) for x in v]
    return result


def _compute_entropy(
    normalized: dict[str, list[float]],
    dims: list[str],
    n: int,
) -> dict[str, float]:
    k = 1.0 / math.log(n) if n > 1 else 1.0
    entropy = {}
    for d in dims:
        vals = normalized[d]
        s = sum(vals) + n
        p = [(v + 1) / s for v in vals]
        H = -k * sum(pj * math.log(pj) for pj in p if pj > 0)
        entropy[d] = H
    return entropy


def _compute_cv_weights(
    values: dict[str, list[float]],
    dims: list[str],
) -> dict[str, float]:
    """变异系数法 — 作为置信度交叉验证"""
    cv = {}
    for d in dims:
        v = values[d]
        if len(v) < 2:
            cv[d] = 0.0
            continue
        mean_v = sum(v) / len(v)
        if mean_v < 1e-10:
            cv[d] = 0.0
            continue
        std_v = math.sqrt(sum((x - mean_v) ** 2 for x in v) / len(v))
        cv[d] = std_v / mean_v
    total = sum(cv.values())
    if total < 1e-10:
        return {d: 1.0 / len(dims) for d in dims}
    return {d: cv[d] / total for d in dims}


def _entropy_distribution_confidence(weights: dict[str, float]) -> float:
    """
    权重分布置信度：如果权重有明显区分（非均匀分布），说明数据有足够的离散度，结果更可信。
    """
    vals = list(weights.values())
    n = len(vals)
    if n < 2:
        return 0.5
    # 计算与均匀分布的距离（KL 散度的近似）
    uniform = 1.0 / n
    distance = sum(abs(w - uniform) for w in vals)
    # 最大可能的距离是当某个权重为 1 其他为 0 时 = 2*(n-1)/n
    max_distance = 2.0 * (n - 1) / n
    return min(1.0, distance / max_distance) if max_distance > 0 else 0.5


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    denom = math.sqrt(sxx * syy)
    if denom < 1e-10:
        return 0.0
    return sxy / denom


def _build_data_summary(
    values: dict[str, list[float]],
    dims: list[str],
    n: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"contributor_count": n, "metrics": {}}
    for d in dims:
        v = sorted(values[d])
        mean_v = sum(v) / n if n > 0 else 0
        std_v = math.sqrt(sum((x - mean_v) ** 2 for x in v) / n) if n > 1 else 0
        skew = 0.0
        if n > 2 and std_v > 1e-10:
            m3 = sum((x - mean_v) ** 3 for x in v) / n
            skew = m3 / (std_v ** 3)
        summary["metrics"][d] = {
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "min": round(min(v), 4) if v else 0,
            "max": round(max(v), 4) if v else 0,
            "skewness": round(skew, 4),
        }
    return summary
