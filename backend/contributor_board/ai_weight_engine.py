"""AI 辅助权重分析引擎

向 LLM 发送贡献指标的数据分布摘要，获取权重建议和推理过程。

权重维度与 scoring_engine.calculate_score 的 7 维严格对齐
（见 scoring_engine.DEFAULT_WEIGHTS），确保 AI 返回的权重能直接用于算分。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from contributor_board.scoring_engine import DEFAULT_WEIGHTS

logger = logging.getLogger(__name__)

# 7 维度键 → 人类可读标签（与 scoring_engine.DEFAULT_WEIGHTS 键一致）
_DIMENSION_LABELS: dict[str, str] = {
    "commit_count": "提交数",
    "code_files": "代码文件变更数",
    "doc_files": "文档文件变更数",
    "pr_merged_rate": "PR 合并率",
    "pr_total": "PR 总量",
    "active_days_90": "近90天活跃天数",
    "span_days": "贡献持续期(天)",
}

DEFAULT_TOTAL = 100.0  # 权重合计基准（calculate_score 期望合计 100）


# ── Prompt 构造 ──────────────────────────────────────────────


def _build_prompt(data_summary: dict[str, Any]) -> str:
    """
    构造给 LLM 的 Prompt。

    输入为 data_summary（来自 auto_weight_engine._build_data_summary），
    包含每个维度的 mean / std / min / max / skewness 统计。

    要求 LLM 返回 7 个固定维度键的权重，合计 100（与 scoring_engine 一致）。
    """
    metrics = data_summary.get("metrics", {})
    contributor_count = data_summary.get("contributor_count", 0)

    # 只输出有数据统计的维度（7 个固定 key）
    available_dims = [k for k in _DIMENSION_LABELS if k in metrics]

    lines = [
        "你是一个开源项目贡献评估专家。你的任务是分析以下 7 个贡献指标的数据分布，",
        "为每个维度分配权重分数（0~100 之间的整数或一位小数），使最终的综合贡献分数能公平反映贡献者的实际价值。",
        "",
        f"项目共有 {contributor_count} 位贡献者。以下各指标的描述和统计数据：",
        "",
    ]

    for dim in available_dims:
        m = metrics[dim]
        label = _DIMENSION_LABELS.get(dim, dim)
        lines.append(f"【{label}】（权重键: {dim}）")
        lines.append(f"  均值: {m['mean']:.2f}, 标准差: {m['std']:.2f}, "
                       f"最小值: {m['min']:.2f}, 最大值: {m['max']:.2f}, "
                       f"偏度: {m['skewness']:.2f}")
        lines.append("")

    lines.extend([
        "请根据以下原则进行权重分配：",
        "1. 离散程度（标准差 / 均值）高的指标区分度好，应赋予更高权重",
        "2. 偏度大的指标说明少数人贡献极高，需注意合理性",
        "3. 最大值为 0 的指标无区分度，权重应设为 0 或极低值",
        "4. 所有权重非负，且 7 个维度的权重之和必须恰好等于 100",
        f"当前参考默认权重（供你比较）: {json.dumps({k: float(v) for k, v in DEFAULT_WEIGHTS.items()}, ensure_ascii=False)}",
        "",
        "请以 JSON 格式回答（不要包含任何其他内容、不要 markdown 代码块），格式如下：",
        "{",
        '  "weights": {"commit_count": xx, "code_files": xx, "doc_files": xx, "pr_merged_rate": xx, "pr_total": xx, "active_days_90": xx, "span_days": xx},',
        '  "analysis": "简要分析你的权重分配依据（200 字以内）"',
        "}",
    ])

    return "\n".join(lines)


# ── 结果解析 ────────────────────────────────────────────────


def _extract_json_block(text: str) -> str | None:
    """从 LLM 回复中提取完整的 JSON 对象（支持 markdown 代码块）。

    策略：
    1. 先尝试剥离 markdown ```json ... ``` 代码块
    2. 用花括号计数找到第一个完整的顶层 JSON 对象
    """
    text = text.strip()

    # 剥离 markdown 代码块
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("{"):
                text = p
                break
            # 处理 ```json\n{...}\n``` 的情况
            if p.startswith("json"):
                continue
            if "{" in p and "}" in p:
                candidate = p[p.find("{"):p.rfind("}") + 1].strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

    # 花括号计数法：找到第一个平衡的 { ... }
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_llm_response(text: str) -> tuple[dict[str, float], str]:
    """
    从 LLM 返回的文本中解析权重和建议。

    Returns: (weights_dict, analysis_text)
    """
    json_text = _extract_json_block(text)
    if json_text is None:
        logger.warning("No JSON block found in LLM response: %s", text[:300])
        return {}, text

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM JSON: %s", json_text[:300])
        return {}, text

    weights_raw = parsed.get("weights", {})
    analysis = parsed.get("analysis", "")

    # 标准化：只保留 7 个固定维度键，取值 clamp 到非负
    weights: dict[str, float] = {}
    for dim in _DIMENSION_LABELS:
        val = weights_raw.get(dim, 0)
        try:
            weights[dim] = max(0.0, float(val))
        except (TypeError, ValueError):
            weights[dim] = 0.0

    # 归一化到合计 100（calculate_score 期望权重合计 100）
    total = sum(weights.values())
    if total < 1e-9:
        logger.warning("AI 返回的权重全为 0，回退到默认权重")
        return dict(DEFAULT_WEIGHTS), analysis or "LLM 未返回有效权重，使用默认权重"
    weights = {k: round(v / total * DEFAULT_TOTAL, 2) for k, v in weights.items()}
    return weights, analysis or "LLM 未提供分析说明"


# ── 公共接口 ────────────────────────────────────────────────


async def analyze_weights_with_ai(
    db: Any,
    data_summary: dict[str, Any],
    model: str | None = None,
    provider: str | None = None,
) -> tuple[dict[str, float], str, str]:
    """
    调用 LLM 分析权重分配建议。

    Args:
        db: 数据库 session（用于获取 LLM 配置）
        data_summary: 来自 auto_weight_engine 的数据摘要
        model: 指定模型名，不指定则使用默认启用模型
        provider: 指定提供商标识，不指定则使用默认激活模型

    Returns:
        (weights, analysis_text, model_name)
    """
    prompt = _build_prompt(data_summary)

    # 获取启用的 LLM 配置
    from contributor_board.llm_helpers import get_active_llm_config

    llm_config = await get_active_llm_config(db, model, provider=provider)
    if not llm_config:
        raise RuntimeError("没有可用的 LLM 配置，请先在系统配置中启用至少一个大模型供应商")

    # 调用 LLM
    from infrastructure.clients.llm_client import LLMClient, LLMError

    client = LLMClient()
    try:
        result = await client.generate(
            provider=llm_config.provider,
            model=llm_config.default_model,
            api_key=llm_config.decrypted_api_key,
            api_base=llm_config.api_base_url or None,
            system_prompt="",
            user_prompt=prompt,
            temperature=0.3,
            max_tokens=2048,
        )
    except LLMError as exc:
        raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    weights, analysis = _parse_llm_response(result.content)
    model_used = llm_config.default_model
    return weights, analysis, model_used



