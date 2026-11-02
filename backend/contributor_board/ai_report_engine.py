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

    # 分析要求 — 严格结构化 JSON，每维 = metrics(量化指标) + content(markdown)
    lines.extend([
        "请从以下 5 个维度进行详细分析，每个维度的 content 至少 4-6 句：",
        "1. team_health 团队健康度：四象限分布是否合理、各区域人数变化趋势、与上期对比的显著变化",
        "2. core_contributors 核心贡献者稳定性：排行榜前列人员稳定性、头部贡献度集中度",
        "3. new_contributors 新增贡献者融入：新贡献者数量、表现、融入速度",
        "4. improvement_areas 改进区与置换区：需关注人员、具体改进方向或关注措施",
        "5. recommendations 决策建议：基于数据给管理团队的 3-5 条具体可执行建议",
        "",
        "【量化指标要求】每个维度的 metrics 字段必须从数据中真实引用以下数字（不存在则填 0 或 null）：",
        "- team_health.metrics: total_contributors, quadrant_distribution{INCENTIVE, ENCOURAGE, IMPROVE, REPLACE}",
        "- core_contributors.metrics: top10_score_sum, top10_contribution_pct, top1_score",
        "- new_contributors.metrics: new_contributors_count, left_contributors_count, quadrant_shifts_count",
        "- improvement_areas.metrics: improve_count, replace_count, focus_logins[]（最多5个需关注用户）",
        "- recommendations 不需要 metrics",
        "",
        "【top_contributors 字段】对排行榜前 10 名逐一点评，每条含 {login, score, quadrant, pr_merged, commit_count, evaluation(30字内客观评价)}",
        "（pr_merged/commit_count 如数据未提供可填 null）。evaluation 必须客观，避免吹捧，可指出短板。",
        "",
        "【重要输出格式要求 — 严格遵守】",
        "1. 以 JSON 格式回答，不要包含任何其他内容、不要 markdown 代码块（```）、不要注释",
        "2. report_content 字段必须使用 Markdown 格式（### 三级标题分节、**加粗**突出关键数字、- 列表、表格展示对比）",
        "3. 各维度的 content 字段使用纯文本（不含 Markdown 标记）",
        "4. executive_summary 使用纯文本，100 字以内",
        "",
        "请以 JSON 格式回答，格式如下（字段名严格一致）：",
        "{",
        '  "executive_summary": "100字以内的执行摘要（纯文本）",',
        '  "report_content": "完整 Markdown 报告，含 5 个 ### 分节 + 四象限对比表格 + Top10 明细表格",',
        '  "team_health": {"metrics": {"total_contributors": 0, "quadrant_distribution": {"INCENTIVE":0,"ENCOURAGE":0,"IMPROVE":0,"REPLACE":0}}, "content": "纯文本分析"},',
        '  "core_contributors": {"metrics": {"top10_score_sum":0, "top10_contribution_pct":0, "top1_score":0}, "content": "纯文本分析"},',
        '  "new_contributors": {"metrics": {"new_contributors_count":0, "left_contributors_count":0, "quadrant_shifts_count":0}, "content": "纯文本分析"},',
        '  "improvement_areas": {"metrics": {"improve_count":0, "replace_count":0, "focus_logins":[]}, "content": "纯文本分析"},',
        '  "top_contributors": [{"login":"", "score":0.0, "quadrant":"INCENTIVE", "pr_merged":0, "commit_count":0, "evaluation":"30字内客观评价"}, ...共10条...],',
        '  "quadrant_analysis": {"summary": "纯文本", "quadrants": {"INCENTIVE":0,"ENCOURAGE":0,"IMPROVE":0,"REPLACE":0}, "total": 0},',
        '  "recommendations": ["建议1(可执行)", "建议2", "建议3"],',
        '  "risk_level": "low",',
        '  "confidence_overall": 0.75',
        "}",
    ])

    return "\n".join(lines)


def _extract_json_block(text: str) -> str | None:
    """从 LLM 回复中提取完整 JSON 对象（支持 markdown 代码块 + 花括号计数）。"""
    text = text.strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.startswith("json"):
                continue
            if p.startswith("{"):
                return p.rstrip("`").strip()
            if "{" in p and "}" in p:
                candidate = p[p.find("{"):p.rfind("}") + 1].strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_response(text: str) -> dict[str, Any]:
    """从 LLM 返回的文本中解析结构化分析报告。

    每个结构化维度字段若 LLM 返回的是纯字符串而非 dict，会包装为
    {"content": <字符串>, "metrics": {}}，保证前端字段稳定。
    """
    text = text.strip()
    json_text = _extract_json_block(text)

    if json_text:
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON: %s", json_text[:200])
            parsed = {}
    else:
        logger.warning("No JSON block found in LLM response: %s", text[:200])
        parsed = {}

    # 5 维字段：确保是 dict {"metrics": {...}, "content": "..."}
    for dim in ("team_health", "core_contributors", "new_contributors", "improvement_areas"):
        v = parsed.get(dim)
        if isinstance(v, dict):
            v.setdefault("metrics", {})
            v.setdefault("content", "")
            parsed[dim] = v
        elif isinstance(v, str) and v:
            parsed[dim] = {"metrics": {}, "content": v}
        else:
            parsed[dim] = {"metrics": {}, "content": ""}

    # top_contributors：确保是 list[dict]
    tc = parsed.get("top_contributors")
    if not isinstance(tc, list):
        parsed["top_contributors"] = []

    # quadrant_analysis：确保是 dict
    qa = parsed.get("quadrant_analysis")
    if not isinstance(qa, dict):
        parsed["quadrant_analysis"] = {"summary": "", "quadrants": {}, "total": 0}

    # recommendations：确保是 list
    recs = parsed.get("recommendations")
    if not isinstance(recs, list):
        parsed["recommendations"] = []

    # executive_summary / report_content / risk_level / confidence_overall
    parsed.setdefault("executive_summary", "")
    parsed.setdefault("report_content", text if not parsed.get("report_content") else parsed["report_content"])
    if parsed.get("risk_level") not in ("low", "medium", "high"):
        parsed["risk_level"] = "medium"
    try:
        parsed["confidence_overall"] = float(parsed.get("confidence_overall") or 0.5)
    except (TypeError, ValueError):
        parsed["confidence_overall"] = 0.5

    return parsed


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
        ContributorProfile,
        ContributorScoreSnapshot,
        ContributorScoringVersion,
    )

    context = {}

    # 1. 从 contributor_profiles 宽表读取当前贡献者指标
    stmt = sa_select(ContributorProfile)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)
    profiles = (await db.execute(stmt)).scalars().all()

    # 为每个 profile 计算 review_ratio（Y 轴：review_count / (code_files+commit_count+review_count)），
    # 宽表若已存 review_ratio 则优先用，否则按公式算
    def _review_ratio(p) -> float:
        if hasattr(p, "review_ratio") and (p.review_ratio is not None and p.review_ratio > 0):
            return float(p.review_ratio)
        committed = (p.code_files or 0) + (p.commit_count or 0)
        reviewed = (p.review_count or 0) + (p.approval_count or 0)
        total = committed + reviewed
        return (reviewed / total) if total > 0 else 0.0

    enriched = []
    quad_results = []
    for p in profiles:
        total_score = p.total_score or 0
        if total_score <= 0 and (p.pr_merged or 0) <= 0:
            continue  # 无有效贡献
        enriched.append({
            "profile": p,
            "login": p.login,
            "total_score": total_score,
            "review_ratio": _review_ratio(p),
            "commit_count": p.commit_count or 0,
            "code_files": p.code_files or 0,
            "doc_files": p.doc_files or 0,
            "pr_merged": p.pr_merged or 0,
            "pr_total": (p.pr_merged or 0) + (p.pr_open or 0) + (p.pr_closed or 0),
            "active_days_90": p.active_days_90 or 0,
            "span_days": p.span_days or 0,
            "tier": p.tier or "",
            "role": p.role or "",
        })

    # 计算象限（X=total_score, Y=review_ratio）
    metrics = [
        {
            "login": e["login"],
            "total_score": e["total_score"],
            "review_ratio": e["review_ratio"],
            "avatar_url": None,
            "merged_pr_count": e["pr_merged"],
            "is_community": False,
        }
        for e in enriched
    ]

    from contributor_board.quadrant_engine import calculate_quadrants
    points, _, _ = calculate_quadrants(metrics)
    quad_map = {p.login: p.quadrant for p in points}

    # 把象限挂到 enriched
    for e in enriched:
        e["quadrant"] = quad_map.get(e["login"], "unknown")
    # total 用于象限统计
    for e in enriched:
        e["total_score_display"] = round(e["total_score"], 2)

    cur_scores = []
    quad_dist = {}
    for e in enriched:
        cur_scores.append({
            "login": e["login"],
            "score": e["total_score_display"],
            "quadrant": e["quadrant"],
            "commit_count": e["commit_count"],
            "code_files": e["code_files"],
            "doc_files": e["doc_files"],
            "pr_merged": e["pr_merged"],
            "pr_total": e["pr_total"],
            "active_days_90": e["active_days_90"],
            "span_days": e["span_days"],
            "tier": e["tier"],
            "role": e["role"],
        })
        quad_dist[e["quadrant"]] = quad_dist.get(e["quadrant"], 0) + 1

    # 当前周期快照
    from datetime import date
    today = str(date.today())
    total_count = len(cur_scores)
    score_sum = sum(s["score"] for s in cur_scores)
    score_mean = round(score_sum / total_count, 2) if total_count else 0
    pr_merged_sum = sum(s["pr_merged"] for s in cur_scores)
    commit_sum = sum(s["commit_count"] for s in cur_scores)

    context["snapshots"] = [{
        "snapshot_date": today,
        "quadrant_distribution": quad_dist,
        "contributor_count": total_count,
        "scores": cur_scores,
    }]
    context["periods"] = [{"start": today, "end": today}]
    context["summary"] = {
        "total_contributors": total_count,
        "score_mean": score_mean,
        "pr_merged_sum": pr_merged_sum,
        "commit_sum": commit_sum,
        "quadrant_distribution": quad_dist,
    }

    # 2. Top 贡献者（按分数降序，取前 10，含 7 维量化数据）
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
