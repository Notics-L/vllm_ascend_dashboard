"""Aggregate the Daily dashboard from collected runs and tracked failures."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daily_pipeline.collection import RESULT_LABELS, timestamp
from daily_pipeline.trigger_config import DEFAULT_TIMEZONE
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE, CATEGORIES
from infrastructure.persistence.models import DailyPipelineCollectionNode as CollectionNode
from infrastructure.persistence.models import DailyPipelineCollectionRun as CollectionRun
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue as Issue
from infrastructure.persistence.models import DailyPipelineTestCase as DailyCase

RUN_BUCKET = {"success": "success", "failure": "failed", "queued": "running", "running": "running"}
PROCESSING_BUCKET = {"resolved": "resolved", "unresolved": "unresolved", "ended": "ended"}
CASE_TYPES = ("Daily", "UT", "E2E", "UT/E2E")
RECENT_RUN_LIMIT = 5


async def daily_overview(db: AsyncSession, days: int) -> dict:
    timezone = ZoneInfo(DEFAULT_TIMEZONE)
    today = datetime.now(timezone).date()
    first_day = today - timedelta(days=days - 1)
    start = datetime.combine(first_day, time.min, timezone).astimezone(UTC).replace(tzinfo=None)
    end = datetime.combine(today + timedelta(days=1), time.min, timezone).astimezone(UTC).replace(tzinfo=None)
    trend = {
        (first_day + timedelta(days=offset)).isoformat(): {
            "success": 0, "failed": 0, "running": 0, "other": 0,
        }
        for offset in range(days)
    }
    run_filters = (
        CollectionRun.deleted_at.is_(None),
        CollectionRun.started_at >= start,
        CollectionRun.started_at < end,
    )
    runs = await db.execute(select(CollectionRun.started_at, CollectionRun.result).where(*run_filters))
    recent = await db.scalars(
        select(CollectionRun).where(*run_filters)
        .order_by(CollectionRun.started_at.desc(), CollectionRun.run_id.desc())
        .limit(RECENT_RUN_LIMIT)
    )
    recent_runs = [
        {
            "id": str(run.run_id),
            "startedAt": timestamp(run.started_at),
            "endedAt": timestamp(run.finished_at),
            "result": RESULT_LABELS.get(run.result, "未获取"),
            "issue": {"number": run.issue_number} if run.issue_number is not None else None,
        }
        for run in recent
    ]
    for started_at, result in runs:
        date = started_at.replace(tzinfo=UTC).astimezone(timezone).date().isoformat()
        trend[date][RUN_BUCKET.get(result, "other")] += 1

    points = []
    successful_runs = completed_runs = running_runs = 0
    for date, counts in trend.items():
        completed = counts["success"] + counts["failed"] + counts["other"]
        points.append({
            "date": date, **counts,
            "successRate": round(100 * counts["success"] / completed, 1) if completed else None,
        })
        successful_runs += counts["success"]
        completed_runs += completed
        running_runs += counts["running"]

    effective_enabled = case((DailyCase.enabled & ~DailyCase.auto_disabled, 1), else_=0)
    case_counts = await db.execute(
        select(DailyCase.hardware, func.count(), func.sum(effective_enabled))
        .where(DailyCase.is_daily.is_(True)).group_by(DailyCase.hardware).order_by(DailyCase.hardware)
    )
    hardware = [
        {"hardware": name, "enabled": int(enabled), "disabled": count - int(enabled)}
        for name, count, enabled in case_counts
    ]
    issues = dict((await db.execute(
        select(Issue.state, func.count()).where(Issue.deleted_at.is_(None)).group_by(Issue.state)
    )).all())
    failure_counts = await db.execute(
        select(Failure.problem_category, Failure.processing_status, CollectionNode.case_type, func.count())
        .join(CollectionNode, CollectionNode.id == Failure.node_id)
        .where(Failure.discovered_at >= start, Failure.discovered_at < end)
        .group_by(Failure.problem_category, Failure.processing_status, CollectionNode.case_type)
    )
    categories = dict.fromkeys([*CATEGORIES.values(), "未分类"], 0)
    processing = {
        kind: {"type": kind, "resolved": 0, "unresolved": 0, "running": 0, "ended": 0}
        for kind in CASE_TYPES
    }
    for category, status, kind, count in failure_counts:
        categories[CATEGORIES.get(category, "未分类")] += count
        group = kind if kind in processing else "其他"
        row = processing.setdefault(group, {"type": group, "resolved": 0, "unresolved": 0, "running": 0, "ended": 0})
        row[PROCESSING_BUCKET.get(status, "running")] += count

    unresolved = await db.scalar(select(func.count()).select_from(Failure).where(Failure.processing_status.in_(ACTIVE)))
    return {
        "summary": {
            "totalCases": sum(row["enabled"] + row["disabled"] for row in hardware),
            "enabledCases": sum(row["enabled"] for row in hardware),
            "totalRuns": completed_runs + running_runs,
            "successfulRuns": successful_runs,
            "runningRuns": running_runs,
            "completedRuns": completed_runs,
            "successRate": round(100 * successful_runs / completed_runs, 1) if completed_runs else None,
            "failedCases": sum(categories.values()),
            "unresolvedCases": unresolved,
            "openIssues": issues.get("open", 0),
            "closedIssues": issues.get("closed", 0),
        },
        "trend": points,
        "recentRuns": recent_runs,
        "categories": [{"name": name, "count": count} for name, count in categories.items() if count],
        "processing": [row for row in processing.values() if any(row[key] for key in ("resolved", "unresolved", "running", "ended"))],
        "hardware": hardware,
    }
