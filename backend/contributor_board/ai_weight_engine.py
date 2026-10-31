"""AI 辅助权重分析引擎

向 LLM 发送贡献指标的数据分布摘要，获取权重建议和推理过程。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from contributor_board.auto_weight_engine import DIMENSIONS, DimensionRegistry

logger = logging.getLogger(__name__)

# 维度名称到人类可读标签的映射
_DIMENSION_LABELS: dict[str, str] = {
    "merged_pr_weight": "PR 合并数",
    "code_line_weight": "代码行数",
    "review_weight": "Code Review 次数",
    "approval_weight": "Code Approval 次数",
    "core_module_weight": "核心模块贡献",
    "files_changed_weight": "变更文件数",
    "bugfix_weight": "Bug 修复数",
    "feature_weight": "新功能开发",
    "active_duration_weight": "活跃持续时间",
}


# ── Prompt 构造 ──────────────────────────────────────────────


def _build_prompt(data_summary: dict[str, Any]) -> str:
    """
    构造给 LLM 的 Prompt。

    输入为 data_summary（来自 auto_weight_engine._build_data_summary），
    包含每个维度的 mean / std / min / max / skewness 统计。
    """
    metrics = data_summary.get("metrics", {})
    contributor_count = data_summary.get("contributor_count", 0)

    # 只输出有数据统计的维度
    available_dims = [d for d in DIMENSIONS if d in metrics]

    lines = [
        "你是一个开源项目贡献评估专家。你的任务是分析以下贡献指标的数据分布，",
        "为每个维度分配合适的权重系数，使最终的综合贡献分数能公平反映贡献者的实际价值。",
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
        "请根据以下原则进行分析：",
        "1. 离散程度（标准差 / 均值）高的指标区分度好，应赋予更高权重",
        "2. 偏度大的指标说明少数人贡献极高，需注意合理性",
        "3. 最大值为 0 的指标无区分度，权重应为 0",
        "4. 所有权重非负，总和为 1",
        "",
        "请以 JSON 格式回答（不要包含任何其他内容），格式如下：",
        '{',
        '  "weights": {"merged_pr_weight": 0.xx, ...},',
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

    # 标准化权重：只保留已注册维度，值转为 float
    weights = {}
    for dim in DIMENSIONS:
        val = weights_raw.get(dim, 0)
        try:
            weights[dim] = round(float(val), 4)
        except (TypeError, ValueError):
            weights[dim] = 0.0

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



