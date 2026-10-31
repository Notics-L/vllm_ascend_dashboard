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
        if r:
            return AIReportDetailResponse.model_validate(r)
        return None

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
        report_content, model_name = await generate_ai_report(
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

        report = ContributorAIReport(
            report_name=report_name,
            report_type=request.report_type,
            period_start=request.period_start,
            period_end=request.period_end,
            generated_by=created_by,
            generation_source="manual",
            weight_version_id=active_version.id if active_version else None,
            llm_model=model_name,
            executive_summary=report_content.get("executive_summary", ""),
            quadrant_analysis=report_content.get("quadrant_analysis"),
            individual_evaluations=report_content.get("individual_evaluations"),
            recommendations=report_content.get("recommendations", []),
            confidence_overall=0.7,
            report_content=report_content.get("report_content") or str(report_content.get("executive_summary", "")),
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
