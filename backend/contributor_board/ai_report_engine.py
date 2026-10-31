"""AI 综合分析报表引擎

基于多周期四象限快照数据，调用 LLM 生成综合分析报告和决策建议。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from contributor_board.constants import QUADRANT_LABELS

logger = logging.getLogger(__name__)

def _build_prompt(snapshot_data: list[dict[str, Any]], context: dict[str, Any]) -> str:
    """构造给 LLM 的分析 Prompt。"""

    lines = [
        "你是一个开源项目贡献评估和团队管理专家。",
        "请根据以下周期性四象限人群数据，撰写一份面向决策者的综合分析报告。",
        "",
    ]

    # 周期信息
    periods = context.get("periods", [])
    if periods:
        lines.append(f"分析涵盖 {len(periods)} 个统计周期：")
        for p in periods:
            lines.append(f"  - {p.get('start', '?')} ~ {p.get('end', '?')}")
        lines.append("")

    # 汇总统计
    summary = context.get("summary", {})
    total = summary.get("total_contributors", 0)
    lines.append(f"=== 总体概况 ===")
    lines.append(f"累计贡献者: {total} 人")
    lines.append("")

    # 各周期四象限分布
    lines.append("=== 各周期四象限分布 ===")
    for snap in snapshot_data:
        date_str = snap.get("snapshot_date", "?")
        quad_dist = snap.get("quadrant_distribution", {})
        lines.append(f"\n周期 {date_str} ({snap.get('contributor_count', 0)} 人):")
        for q_key in ["INCENTIVE", "ENCOURAGE", "IMPROVE", "REPLACE"]:
            count = quad_dist.get(q_key, 0)
            pct = (count / snap.get("contributor_count", 1) * 100) if snap.get("contributor_count") else 0
            lines.append(f"  {QUADRANT_LABELS.get(q_key, q_key)}: {count} 人 ({pct:.1f}%)")
    lines.append("")

    # 权重变更记录
    weight_changes = context.get("weight_changes", [])
    if weight_changes:
        lines.append("=== 评分权重变更记录 ===")
        for wc in weight_changes[:5]:
            lines.append(f"  {wc.get('version', '?')}: {wc.get('method', '?')}, "
                        f"说明: {wc.get('description', 'N/A')}")
        lines.append("")

    # Top 贡献者
    top_contributors = context.get("top_contributors", [])
    if top_contributors:
        lines.append("=== 本期 Top 贡献者 ===")
        for tc in top_contributors[:10]:
            lines.append(f"  {tc.get('login', '?')}: {tc.get('score', 0):.1f}分 "
                        f"({QUADRANT_LABELS.get(tc.get('quadrant', ''), '?')})")
        lines.append("")

    # 人员变动
    changes = context.get("contributor_changes", {})
    if changes:
        lines.append("=== 人员变动 ===")
        new_count = changes.get("new_contributors", 0)
        left_count = changes.get("left_contributors", 0)
        shifted = changes.get("quadrant_shifts", [])
        lines.append(f"  新增贡献者: {new_count} 人")
        lines.append(f"  离开贡献者: {left_count} 人")
        if shifted:
            lines.append(f"  象限变动:")
            for s in shifted[:10]:
                lines.append(f"    {s.get('login', '?')}: "
                            f"{QUADRANT_LABELS.get(s.get('from', ''), '?')} -> "
                            f"{QUADRANT_LABELS.get(s.get('to', ''), '?')}")
        lines.append("")

    # 分析要求
    lines.extend([
        "请从以下维度进行详细分析（每个维度至少 5-8 句）：",
        "1. 团队健康度：四象限分布是否合理，各区域人数变化趋势，与上期对比的显著变化",
        "2. 核心贡献者稳定性：对排行榜前 10 名贡献者逐一进行简短评价（姓名、分数、象限、表现点评）",
        "3. 新增贡献者融入：新贡献者的表现和融入情况",
        "4. 改进区与置换区：逐一点评需要关注的人员，给出具体的改进方向或关注措施",
        "5. 决策建议：基于数据给管理团队的 3-5 条具体可执行建议",
        "",
        "【重要输出格式要求】",
        "你的 JSON 中 report_content 字段必须使用 Markdown 格式撰写，使用 ### 三级标题分节，",
        "用 **加粗** 突出关键数字和结论，用 - 列出要点，用表格展示对比数据。",
        "其他字段（executive_summary、team_health 等）使用纯文本，不要含 Markdown 标记。",
        "",
        "请以 JSON 格式回答（不要包含任何其他内容），格式如下：",
        "{",
        '  "executive_summary": "100字以内的执行摘要（纯文本，不含Markdown标记）",',
        '  "report_content": "用 ### 标题分节的完整 Markdown 报告，包含五个分析维度，对排行榜前列人员进行逐一评价，用 **加粗** 突出关键数据，用表格展示四象限对比",',
        '  "team_health": "团队健康度分析（纯文本）",',
        '  "core_contributors": "核心贡献者稳定性分析（纯文本）",',
        '  "new_contributors": "新增贡献者融入分析（纯文本）",',
        '  "improvement_areas": "需要关注的人员和问题（纯文本）",',
        '  "recommendations": ["建议1", "建议2", "建议3"],',
        '  "risk_level": "low/medium/high"',
        "}",
    ])

    return "\n".join(lines)


def _parse_response(text: str) -> dict[str, Any]:
    """从 LLM 返回的文本中解析分析报告。"""
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        json_text = text[start:end]
    else:
        json_text = text

    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM report response: %s", text[:200])
        return {
            "executive_summary": "LLM 返回格式错误",
            "report_content": text,
            "team_health": text[:500],
            "core_contributors": "",
            "new_contributors": "",
            "improvement_areas": "",
            "recommendations": [],
            "risk_level": "medium",
        }


async def generate_ai_report(
    db: Any,
    snapshot_data: list[dict[str, Any]],
    context: dict[str, Any],
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """
    调用 LLM 生成综合分析报表。

    Args:
        db: 数据库 session
        snapshot_data: 各周期的四象限快照数据
        context: 额外上下文（top 贡献者、权重变更、人员变动等）
        model: 指定模型名

    Returns:
        (report_dict, model_name)
    """
    prompt = _build_prompt(snapshot_data, context)

    from contributor_board.llm_helpers import get_active_llm_config

    llm_config = await get_active_llm_config(db, model)
    if not llm_config:
        raise RuntimeError("没有可用的 LLM 配置")

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
            max_tokens=4096,
        )
    except LLMError as exc:
        raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    report = _parse_response(result.content)
    model_used = llm_config.default_model
    return report, model_used



async def build_report_context(db: Any, repo_name: str | None = None) -> dict[str, Any]:
    """
    从 pull_requests 表 + 快照表构建报表所需的上下文数据。

    包括：当前周期 PR 聚合指标、历史快照、top 贡献者、权重变更等。
    """
    from sqlalchemy import select as sa_select
    from infrastructure.persistence.models import (
        ContributorScoreSnapshot,
        ContributorScoringVersion,
    )

    from contributor_board.pr_enrich import get_full_contributor_metrics_from_prs

    context = {}

    # 1. 从 pull_requests 实时聚合当前贡献者指标
    pr_metrics = await get_full_contributor_metrics_from_prs(db, repo_name=repo_name)

    # 计算象限
    from contributor_board.quadrant_engine import calculate_quadrants
    active_prs = [m for m in pr_metrics if m.get("contribution_score", 0) > 0]
    points, _, _ = calculate_quadrants(active_prs, y_axis_type="review_count")
    quad_map = {p.login: p.quadrant for p in points}

    cur_scores = []
    quad_dist = {}
    for m in active_prs:
        q = quad_map.get(m["login"], "unknown")
        cur_scores.append({
            "login": m["login"],
            "score": m.get("contribution_score", 0),
            "quadrant": q,
        })
        quad_dist[q] = quad_dist.get(q, 0) + 1

    # 当前周期快照
    from datetime import date
    today = str(date.today())
    context["snapshots"] = [{
        "snapshot_date": today,
        "quadrant_distribution": quad_dist,
        "contributor_count": len(cur_scores),
        "scores": cur_scores,
    }]
    context["periods"] = [{"start": today, "end": today}]
    context["summary"] = {"total_contributors": len(cur_scores)}

    # 2. Top 贡献者
    top_sorted = sorted(cur_scores, key=lambda x: x["score"], reverse=True)
    context["top_contributors"] = top_sorted[:10]

    # 3. 权重变更记录
    v_stmt = (
        sa_select(
            ContributorScoringVersion.version,
            ContributorScoringVersion.method,
            ContributorScoringVersion.description,
        )
        .order_by(ContributorScoringVersion.created_at.desc())
        .limit(5)
    )
    if repo_name:
        v_stmt = v_stmt.where(ContributorScoringVersion.repo_name == repo_name)

    v_result = await db.execute(v_stmt)
    context["weight_changes"] = [
        {"version": r[0], "method": r[1], "description": r[2] or ""}
        for r in v_result.all()
    ]

    # 4. 历史快照对比（用于人员变动检测）
    snap_stmt = (
        sa_select(ContributorScoreSnapshot)
        .order_by(ContributorScoreSnapshot.created_at.desc())
        .limit(2)
    )
    if repo_name:
        snap_stmt = snap_stmt.where(ContributorScoreSnapshot.repo_name == repo_name)
    snap_result = await db.execute(snap_stmt)
    hist_snaps = snap_result.scalars().all()

    prev_scores_map: dict[str, str] = {}
    if len(hist_snaps) >= 1:
        prev_snap = hist_snaps[0]
        prev_scores = prev_snap.scores or []
        for ps in prev_scores:
            prev_scores_map[ps.get("login", "")] = ps.get("quadrant", "unknown")

    curr_logins = {s["login"] for s in cur_scores}
    prev_logins = set(prev_scores_map.keys())

    new_c = curr_logins - prev_logins
    left_c = prev_logins - curr_logins

    shifts = []
    curr_quad_map = {s["login"]: s["quadrant"] for s in cur_scores}
    for login in curr_logins & prev_logins:
        if prev_scores_map.get(login) != curr_quad_map.get(login):
            shifts.append({
                "login": login,
                "from": prev_scores_map.get(login, "unknown"),
                "to": curr_quad_map.get(login, "unknown"),
            })

    context["contributor_changes"] = {
        "new_contributors": len(new_c),
        "left_contributors": len(left_c),
        "quadrant_shifts": shifts[:10],
    }

    return context
