"""AI 综合分析报告 Service"""
from __future__ import annotations

import logging
from datetime import UTC, date
from typing import Any

from sqlalchemy import select, func

from contributor_board.ai_report_engine import build_report_context, generate_ai_report
from contributor_board.scoring_version_schemas import (
    AIReportDetailResponse,
    AIReportGenerateRequest,
    AIReportResponse,
)
from infrastructure.persistence.models import ContributorAIReport

logger = logging.getLogger(__name__)


async def _paginated_list(
    db,
    conditions: list,
    model_class: Any,
    order_col: Any,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list, int]:
    """通用分页查询：返回 (items, total)"""
    stmt = select(model_class)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(order_col.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    items = result.scalars().all()

    count_stmt = select(func.count(model_class.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    return items, total


def _ai_report_filter(repo_name: str | None = None, report_type: str | None = None):
    """构建 ContributorAIReport 查询条件"""
    conditions = []
    if repo_name:
        conditions.append(ContributorAIReport.repo_name == repo_name)
    if report_type:
        conditions.append(ContributorAIReport.report_type == report_type)
    return conditions


def _build_snapshot_query(repo_name: str | None = None):
    """构建 ContributorScoreSnapshot 查询语句"""
    from infrastructure.persistence.models import ContributorScoreSnapshot
    stmt = select(ContributorScoreSnapshot)
    if repo_name:
        stmt = stmt.where(ContributorScoreSnapshot.repo_name == repo_name)
    return stmt.order_by(ContributorScoreSnapshot.snapshot_date.desc()).limit(10)


def _build_active_version_query(repo_name: str | None = None):
    """构建 ContributorScoringVersion active 版本查询语句"""
    from infrastructure.persistence.models import ContributorScoringVersion
    stmt = select(ContributorScoringVersion).where(
        ContributorScoringVersion.status == "active"
    )
    if repo_name:
        stmt = stmt.where(ContributorScoringVersion.repo_name == repo_name)
    return stmt


class AIReportService:
    @staticmethod
    async def list_reports(
        db,
        repo_name: str | None = None,
        report_type: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        conditions = _ai_report_filter(repo_name, report_type)
        reports, total = await _paginated_list(
            db, conditions, ContributorAIReport,
            ContributorAIReport.generated_at, skip, limit,
        )
        return {
            "reports": [AIReportResponse.model_validate(r) for r in reports],
            "total": total,
        }

    @staticmethod
    async def get_report(db, report_id: int) -> AIReportDetailResponse | None:
        r = await db.get(ContributorAIReport, report_id)
        if not r:
            return None
        # 从 structured_analysis JSON 列拆出 5 维 + Top N + risk_level
        structured = r.structured_analysis or {}
        return AIReportDetailResponse(
            id=r.id,
            report_name=r.report_name,
            report_type=r.report_type,
            period_start=r.period_start,
            period_end=r.period_end,
            generated_at=r.generated_at,
            generated_by=r.generated_by,
            generation_source=r.generation_source,
            weight_version_id=r.weight_version_id,
            llm_model=r.llm_model,
            executive_summary=r.executive_summary,
            quadrant_analysis=r.quadrant_analysis,
            individual_evaluations=r.individual_evaluations,
            recommendations=r.recommendations,
            confidence_overall=r.confidence_overall,
            report_content=r.report_content,
            report_file_url=r.report_file_url,
            schedule_cron=r.schedule_cron,
            enabled=r.enabled if r.enabled is not None else True,
            repo_name=r.repo_name,
            created_at=r.created_at,
            updated_at=r.updated_at,
            # 拆自 structured_analysis
            team_health=structured.get("team_health"),
            core_contributors=structured.get("core_contributors"),
            new_contributors=structured.get("new_contributors"),
            improvement_areas=structured.get("improvement_areas"),
            top_contributors=structured.get("top_contributors"),
            risk_level=structured.get("risk_level"),
        )

    @staticmethod
    async def generate_report(
        db,
        request: AIReportGenerateRequest,
        created_by: str | None = None,
    ) -> AIReportResponse:
        repo_name = request.repo_name

        # Build context from PR data
        context = await build_report_context(db, repo_name=repo_name)

        # Build snapshot data from current PR context + history
        snapshot_data = []
        cur_snap = context.get("snapshots", [{}])[0]
        snapshot_data.append({
            "snapshot_date": cur_snap.get("snapshot_date", ""),
            "contributor_count": cur_snap.get("contributor_count", 0),
            "quadrant_distribution": cur_snap.get("quadrant_distribution", {}),
        })

        # Call LLM
        report_data, model_name = await generate_ai_report(
            db, snapshot_data, context, request.model
        )

        # Build report name
        period_str = ""
        if request.period_start and request.period_end:
            period_str = f" {request.period_start}~{request.period_end}"
        report_name = f"AI综合分析报告{period_str} ({request.report_type})"

        # Get active version ID
        ver_stmt = _build_active_version_query(repo_name)
        ver_result = await db.execute(ver_stmt)
        active_version = ver_result.scalar_one_or_none()

        # 结构化 5 维 + Top N + risk_level → 聚合 JSON（存入 structured_analysis 列）
        structured = {
            "team_health": report_data.get("team_health", {"metrics": {}, "content": ""}),
            "core_contributors": report_data.get("core_contributors", {"metrics": {}, "content": ""}),
            "new_contributors": report_data.get("new_contributors", {"metrics": {}, "content": ""}),
            "improvement_areas": report_data.get("improvement_areas", {"metrics": {}, "content": ""}),
            "top_contributors": report_data.get("top_contributors", []),
            "risk_level": report_data.get("risk_level", "medium"),
        }

        # 兼容旧字段：individual_evaluations = top_contributors（逐人评价兼容）
        individual_evaluations = [
            {"login": tc.get("login"), "score": tc.get("score"),
             "evaluation": tc.get("evaluation", "")}
            for tc in structured["top_contributors"]
        ] if structured["top_contributors"] else None

        report = ContributorAIReport(
            report_name=report_name,
            report_type=request.report_type,
            period_start=request.period_start,
            period_end=request.period_end,
            generated_by=created_by,
            generation_source="manual",
            weight_version_id=active_version.id if active_version else None,
            llm_model=model_name,
            executive_summary=report_data.get("executive_summary", ""),
            quadrant_analysis=report_data.get("quadrant_analysis"),
            individual_evaluations=individual_evaluations,
            recommendations=report_data.get("recommendations", []),
            confidence_overall=report_data.get("confidence_overall", 0.7),
            report_content=report_data.get("report_content")
                or report_data.get("executive_summary", ""),
            structured_analysis=structured,
            repo_name=repo_name,
        )
        db.add(report)
        await db.flush()

        # Create audit log
        from infrastructure.persistence.models import ContributorScoreUpdateAudit
        audit = ContributorScoreUpdateAudit(
            action="generate_ai_report",
            version_id=report.weight_version_id,
            repo_name=repo_name,
            details={"report_id": report.id, "report_type": request.report_type},
            summary=f"Generate AI report: {report_name}",
            created_by=created_by,
        )
        db.add(audit)
        await db.commit()

        return AIReportResponse.model_validate(report)


async def export_comprehensive_pdf(
    db,
    repo_name: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    top_n: int = 10,
    quadrant_filter: list[str] | None = None,
) -> tuple[bytes, str]:
    """
    生成并返回综合 PDF 报告 (pdf_bytes, filename)。
    可选 AI 评价(若可用 LLM 配置)；否则仅数据+模板。
    """
    from contributor_board.comprehensive_report_exporter import export_comprehensive_pdf as _do_export
    from infrastructure.persistence.models import ContributorProfile
    from contributor_board.llm_helpers import get_active_llm_config

    # 1. 查询贡献者
    stmt = select(ContributorProfile).where(ContributorProfile.total_score > 0)
    if repo_name:
        stmt = stmt.where(ContributorProfile.repo_name == repo_name)
    if quadrant_filter:
        stmt = stmt.where(ContributorProfile.tier.in_(quadrant_filter))
    result = await db.execute(stmt)
    profiles = result.scalars().all()

    if not profiles:
        return b"", ""

    # 2. 排序 + Top N
    profiles_sorted = sorted(profiles, key=lambda p: p.total_score or 0, reverse=True)
    top_n_list = [p.to_profile_dict() for p in profiles_sorted[: top_n * 3]]  # 取多供 AI 评价池
    # 截断 Top N
    top_n_list = top_n_list[:top_n]

    # 3. 统计
    scores = [p.total_score or 0 for p in profiles_sorted]
    total = len(profiles_sorted)
    quadrant_dist: dict[str, int] = {}
    for p in profiles_sorted:
        q = p.tier or "unknown"
        quadrant_dist[q] = quadrant_dist.get(q, 0) + 1

    dim_keys = ["commit_count", "code_files", "doc_files", "pr_merged", "pr_total",
                "active_days_90", "span_days", "review_count", "approval_count"]
    dim_averages: dict[str, float] = {}
    for k in dim_keys:
        vals = [getattr(p, k, 0) or 0 for p in profiles_sorted]
        dim_averages[k] = round(sum(vals) / total, 1) if total else 0

    statistics = {
        "total_contributors": total,
        "score_avg": round(sum(scores) / total, 1) if total else 0,
        "score_max": max(scores) if scores else 0,
        "score_min": min(scores) if scores else 0,
        "quadrant_distribution": quadrant_dist,
        "dimension_averages": dim_averages,
    }

    # 4. period desc
    if period_start and period_end:
        period_desc = f"{period_start} ~ {period_end}"
    elif period_start:
        period_desc = f"{period_start} 起"
    elif period_end:
        period_desc = f"至 {period_end}"
    else:
        period_desc = "全量"

    # 5. 尝试 AI 评价（可选）
    ai_evaluations: dict[str, str] = {}
    recommendations: list[str] = []
    risk_level: str | None = None
    confidence_overall: float | None = None

    llm_config = await get_active_llm_config(db)
    if llm_config:
        try:
            from contributor_board.llm_client import LLMClient
            from infrastructure.persistence.models.daily_summary import LLMProviderConfig

            client = LLMClient()
            top_n_brief = "\n".join(
                f"  {i+1}. {p['login']} 分数={p['total_score']} 象限={p['tier']} "
                f"PR={p['pr_merged']}/{p['pr_total']} 提交={p['commit_count']} 活跃={p['active_days_90']}d"
                for i, p in enumerate(top_n_list)
            )
            system_prompt = (
                "你是一位资深开源社区运营专家。请基于以下贡献者数据，"
                "生成简洁、客观、有洞察力的综合评价。"
                "回答必须为严格 JSON 格式（不要额外解释），包含字段: "
                "top_contributors(数组, 每项 {login, score, evaluation}), "
                "recommendations(数组, 每项为字符串), "
                "risk_level(low/medium/high), confidence_overall(0~1 float)。\n"
                "每个 evaluation 不超过 50 字，聚焦其贡献亮点与改进方向。"
            )
            user_prompt = (
                f"时间范围: {period_desc}\n"
                f"贡献者总数: {total}, 平均分: {statistics['score_avg']}\n"
                f"象限分布: {quadrant_dist}\n"
                f"Top {len(top_n_list)} 贡献者:\n{top_n_brief}\n"
                f"请生成 JSON。"
            )
            result_llm = await client.generate(
                provider=llm_config.provider,
                model=llm_config.default_model,
                api_key=llm_config.decrypted_api_key,
                api_base=llm_config.api_base_url,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
                max_tokens=2000,
            )
            import json, re
            content = getattr(result_llm, "content", "") or ""
            # 提取 JSON
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    for tc in parsed.get("top_contributors", [])[:top_n]:
                        ai_evaluations[tc.get("login", "")] = tc.get("evaluation", "")
                    recommendations = parsed.get("recommendations", [])
                    risk_level = parsed.get("risk_level")
                    conf = parsed.get("confidence_overall")
                    confidence_overall = float(conf) if conf is not None else None
                except Exception as exc:
                    logger.warning("comprehensive PDF AI parse error: %s", exc)
        except Exception as exc:
            logger.warning("comprehensive PDF AI call skipped: %s", exc)

    # 6. 组装
    top_n_payload = []
    for p in top_n_list:
        top_n_payload.append({
            "login": p["login"],
            "score": p["total_score"],
            "tier": p["tier"],
            "role": p["role"],
            "commit_count": p["commit_count"],
            "code_files": p["code_files"],
            "pr_merged": p["pr_merged"],
            "pr_total": p["pr_merged"] + p["pr_open"] + p["pr_closed"],
            "active_days_90": p["active_days_90"],
            "span_days": p["span_days"],
            "review_count": p["review_count"],
        })

    return await _do_export(
        db=db,
        top_n_list=top_n_payload,
        period_desc=period_desc,
        statistics=statistics,
        ai_evaluations=ai_evaluations or None,
        recommendations=recommendations or None,
        risk_level=risk_level,
        confidence_overall=confidence_overall,
        repo_name=repo_name,
    )
