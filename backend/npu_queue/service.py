"""Aggregate normalized CI NPU job facts for the dashboard read API."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.github_run_metadata import build_run_metadata
from infrastructure.persistence.models import CIJob, CINpuJobFact, CIResult
from npu_queue.snapshots import is_running, is_waiting, physical_queue_state, physical_queue_trend

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _beijing_time(value: datetime) -> datetime:
    """MySQL returns naive UTC timestamps; display dashboard times in Beijing time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    # MySQL returns naive UTC timestamps while live clock values are aware.
    # Normalize both sides before subtraction so queued jobs cannot break the
    # complete dashboard response.
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    else:
        start = start.astimezone(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    return max((end - start).total_seconds() / 60, 0)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 1)


def _queue_minutes(fact: CINpuJobFact) -> float | None:
    if not fact.queued_at or not fact.started_at or fact.started_at <= fact.queued_at:
        return None
    return _minutes(fact.queued_at, fact.started_at)


def _queue_delay_window(now: datetime) -> tuple[str, str]:
    """Return the seven Beijing calendar dates shown by the delay chart."""
    today = _beijing_time(now).date()
    return ((today - timedelta(days=6)).isoformat(), today.isoformat())


def _queue_delay_dates(now: datetime) -> list[str]:
    """Return every displayed date so empty days remain visible in the chart."""
    start, _ = _queue_delay_window(now)
    first_day = date.fromisoformat(start)
    return [(first_day + timedelta(days=offset)).isoformat() for offset in range(7)]


async def build_dashboard(
    db: AsyncSession,
    *,
    consumption_pool: str | None = None,
    consumption_days: int = 7,
) -> dict[str, Any]:
    facts = list((await db.scalars(select(CINpuJobFact).order_by(CINpuJobFact.queued_at.desc()))).all())
    run_ids = {fact.run_id for fact in facts}
    result_rows = []
    if run_ids:
        result_rows = list((await db.scalars(
            select(CIResult).where(CIResult.run_id.in_(run_ids))
        )).all())
    results_by_run_id = {result.run_id: result for result in result_rows}
    fact_job_ids = {fact.job_id for fact in facts}
    ci_jobs = list((await db.scalars(
        select(CIJob).where(CIJob.job_id.in_(fact_job_ids))
    )).all()) if fact_job_ids else []
    ci_jobs_by_id = {job.job_id: job for job in ci_jobs}
    now = datetime.now(UTC)
    consumption_start = now - timedelta(days=consumption_days)
    queue_window_start, queue_window_end = _queue_delay_window(now)
    matched = [fact for fact in facts if fact.mapping_status == "matched" and fact.mapped_cards]
    pools: dict[str, list[CINpuJobFact]] = defaultdict(list)
    for fact in matched:
        pools[fact.pool or "未映射资源池"].append(fact)

    pool_rows = []
    all_waiting: list[CINpuJobFact] = []
    all_running: list[CINpuJobFact] = []
    queue_samples: list[float] = []
    for name, rows in pools.items():
        waiting = [row for row in rows if is_waiting(row)]
        running = [row for row in rows if is_running(row)]
        samples = [value for row in rows if (value := _queue_minutes(row)) is not None and value >= 2]
        all_waiting.extend(waiting)
        all_running.extend(running)
        queue_samples.extend(samples)
        pool_rows.append({
            "key": name,
            "name": name,
            "capacity": None,
            "waitingJobs": len(waiting),
            "waitingCards": sum(row.mapped_cards or 0 for row in waiting),
            "runningJobs": len(running),
            "runningCards": sum(row.mapped_cards or 0 for row in running),
            "todayCardHours": 0,
            "queueP50": _percentile(samples, .5),
            "queueP90": _percentile(samples, .9),
            "samples": len(samples),
            "physicalRunningCards": None,
            "collectedAt": now.isoformat(),
        })

    queue_rows = []
    for fact in sorted(all_waiting, key=lambda row: row.queued_at or now):
        wait = _minutes(fact.queued_at, now) or 0
        queue_rows.append({
            "key": str(fact.job_id), "waitMinutes": round(wait), "run": f"#{fact.run_id}",
            "title": fact.job_name or "", "job": fact.job_name or "", "repository": fact.repository,
            "pool": fact.pool or "未映射", "cards": fact.mapped_cards or 0,
            "createdAt": _beijing_time(fact.queued_at).isoformat() if fact.queued_at else "—",
            "state": "short" if wait < 2 else "waiting",
        })

    # CI facts can lag behind the latest CI Job record.  The latter determines
    # whether a grey CI segment is current or has already ended; stale facts
    # must never be rendered as a wait extending to "now".
    ci_waiting_rows = []
    for fact in sorted((row for row in matched if row.status == "queued"), key=lambda row: row.queued_at or now):
        ci_job = ci_jobs_by_id.get(fact.job_id)
        if ci_job is None or fact.queued_at is None:
            continue
        current = ci_job.status == "queued" and ci_job.started_at is None and ci_job.completed_at is None
        ended_at = None if current else (ci_job.started_at or ci_job.completed_at)
        if not current and ended_at is None:
            continue
        if ended_at and _minutes(ended_at, now) is not None and _minutes(ended_at, now) > 24 * 60:
            continue
        wait = _minutes(fact.queued_at, ended_at or now) or 0
        repository_url = f"https://github.com/{fact.repository}" if fact.repository else None
        ci_waiting_rows.append({
            "key": f"ci-{fact.run_id}-{fact.job_id}",
            "waitMinutes": round(wait),
            "title": fact.job_name or f"Job {fact.job_id}",
            "repository": fact.repository,
            "repositoryUrl": repository_url,
            "pool": fact.pool,
            "cards": fact.mapped_cards or 0,
            "createdAt": fact.queued_at.isoformat() if fact.queued_at else None,
            "endedAt": ended_at.isoformat() if ended_at else None,
            "isCurrent": current,
            "workflowName": fact.workflow_name,
            "runUrl": fact.run_url,
            "prNumber": fact.pr_number,
            "prUrl": f"{repository_url}/pull/{fact.pr_number}" if repository_url and fact.pr_number else None,
        })

    physical_state, pending_pods = await physical_queue_state(db)
    # Current queue pressure is defined by Kubernetes Pending Pods only.  CI
    # jobs before a Pod exists have no confirmed physical-card demand.
    pending_by_pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pod in pending_pods:
        pending_by_pool[str(pod.get("cluster_name") or "未映射资源池")].append(pod)

    pool_rows = [
        {
            "key": name,
            "name": name,
            "capacity": None,
            "waitingJobs": len(pods),
            "waitingCards": round(sum(float(pod.get("npu") or 0) for pod in pods), 2),
            "runningJobs": 0,
            "runningCards": 0,
            "todayCardHours": 0,
            "queueP50": None,
            "queueP90": None,
            "samples": 0,
            "physicalRunningCards": None,
            "collectedAt": now.isoformat(),
        }
        for name, pods in pending_by_pool.items()
    ]
    queue_rows = []
    for pod in sorted(pending_pods, key=lambda item: str(item.get("created_at") or "")):
        created_at = pod.get("created_at")
        created = None
        if isinstance(created_at, str):
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                pass
        wait = _minutes(created, now) or 0
        queue_rows.append({
            "key": f"{pod.get('cluster_id')}/{pod.get('namespace')}/{pod.get('name')}",
            "waitMinutes": round(wait),
            "run": "—",
            "title": pod.get("name") or "",
            "job": pod.get("name") or "",
            "repository": pod.get("namespace") or "—",
            "pool": pod.get("cluster_name") or "未映射资源池",
            "cards": float(pod.get("npu") or 0),
            "createdAt": _beijing_time(created).isoformat() if created else "—",
            "state": "short" if wait < 2 else "waiting",
        })
    current_point = {
        "timestamp": _beijing_time(now).strftime("%H:%M"),
        "waitingCards": physical_state["waitingCards"],
        "runningCards": physical_state["runningCards"],
        "waitingJobs": physical_state["waitingJobs"], "runningJobs": physical_state["runningJobs"],
    }
    trend = [{
        "timestamp": _beijing_time(captured_at).strftime("%H:%M"),
        **counts,
    } for captured_at, counts in await physical_queue_trend(db, now=now)]
    # Retain a live point between two Kubernetes resource collections.
    if not trend or trend[-1] != current_point:
        trend.append(current_point)
    daily_queue: dict[str, list[float]] = defaultdict(list)
    daily_usage: dict[str, dict[str, float]] = defaultdict(lambda: {"cardHours": 0, "jobs": 0, "failedCardHours": 0})
    pr_costs: dict[str, dict[str, Any]] = {}
    for fact in matched:
        queue_minutes = _queue_minutes(fact)
        if queue_minutes is not None and queue_minutes >= 2 and fact.started_at:
            # The date labels, curve and summary all use the same Beijing 7-day window.
            started_date = _beijing_time(fact.started_at).date().isoformat()
            if queue_window_start <= started_date <= queue_window_end:
                daily_queue[started_date].append(queue_minutes)
        runtime_minutes = _minutes(fact.started_at, fact.completed_at)
        if runtime_minutes is None or not fact.completed_at:
            continue
        completed_at = (
            fact.completed_at.replace(tzinfo=UTC)
            if fact.completed_at.tzinfo is None
            else fact.completed_at.astimezone(UTC)
        )
        # Consumption is a separate view: filter before aggregation, rather
        # than returning all historical Run data for the browser to trim.
        if completed_at < consumption_start:
            continue
        if consumption_pool and fact.pool != consumption_pool:
            continue
        card_hours = runtime_minutes * (fact.mapped_cards or 0) / 60
        # Keep the PR-consumption completion date consistent with the Beijing
        # calendar used by the seven-day queue-delay chart.
        day = _beijing_time(fact.completed_at).date().isoformat()
        daily_usage[day]["cardHours"] += card_hours
        daily_usage[day]["jobs"] += 1
        if fact.conclusion != "success":
            daily_usage[day]["failedCardHours"] += card_hours
        organization, _, repository = fact.repository.partition("/")
        metadata = build_run_metadata(
            organization=organization or None,
            repository=repository or None,
            workflow_run_id=fact.run_id,
            workflow_ref=None,
            job_display_name=fact.job_name,
            ci_result=results_by_run_id.get(fact.run_id),
            pr_number=fact.pr_number,
        )
        # Run-level aggregation keeps multiple jobs/occupancy records under one
        # GitHub Actions Run and preserves its PR as metadata instead of merging
        # distinct runs only because they belong to the same PR.
        key = f"run-{fact.repository}-{fact.run_id}"
        cost = pr_costs.setdefault(key, {
            "key": key,
            "title": fact.job_name or metadata["run_display_name"],
            "branch": metadata["run_display_name"],
            "repository": fact.repository,
            "kind": "pr" if metadata["pr_number"] else "run",
            "detailUrl": metadata["run_url"] or fact.run_url,
            "workflowRunId": metadata["workflow_run_id"],
            "workflowName": metadata["workflow_name"],
            "runNumber": metadata["run_number"],
            "runDisplayName": metadata["run_display_name"],
            "workflowRef": metadata["workflow_ref"],
            "prNumber": metadata["pr_number"],
            "event": metadata["event"],
            "triggerScope": metadata["trigger_scope"],
            "cardHours": 0.0,
            "allCardHours": 0.0,
            "runs": set(),
            "successfulRuns": set(),
        })
        cost["allCardHours"] += card_hours
        cost["runs"].add(fact.run_id)
        if fact.conclusion == "success":
            cost["cardHours"] += card_hours
            cost["successfulRuns"].add(fact.run_id)

    daily_queue_rows = [{
        "date": day,
        "p50": _percentile(daily_queue.get(day, []), .5),
        "p90": _percentile(daily_queue.get(day, []), .9),
        "samples": len(daily_queue.get(day, [])),
        "abandoned": 0,
    } for day in _queue_delay_dates(now)]
    queue_window_samples = [value for values in daily_queue.values() for value in values]
    queue_delay_summary = {
        "startDate": queue_window_start,
        "endDate": queue_window_end,
        "p50": _percentile(queue_window_samples, .5),
        "p90": _percentile(queue_window_samples, .9),
        "samples": len(queue_window_samples),
    }
    completed_facts = [
        fact
        for fact in matched
        if fact.completed_at
        and (
            (fact.completed_at.replace(tzinfo=UTC) if fact.completed_at.tzinfo is None else fact.completed_at.astimezone(UTC))
            >= consumption_start
        )
        and (not consumption_pool or fact.pool == consumption_pool)
    ]
    # Read runtime-configured sync settings only when the dashboard payload is
    # requested; helper imports remain usable in isolated unit tests.
    from infrastructure.core.config import settings

    usage_period = {
        "startDate": _beijing_time(min(fact.completed_at for fact in completed_facts)).date().isoformat()
        if completed_facts else None,
        "endDate": _beijing_time(max(fact.completed_at for fact in completed_facts)).date().isoformat()
        if completed_facts else None,
        "syncIntervalMinutes": settings.CI_SYNC_INTERVAL_MINUTES,
        "initialLookbackDays": settings.CI_SYNC_DAYS_BACK,
    }
    daily_usage_rows = [{"date": day, "cardHours": round(values["cardHours"], 2), "jobs": int(values["jobs"]), "failedCardHours": round(values["failedCardHours"], 2)} for day, values in sorted(daily_usage.items())]
    pr_cost_rows = [{**cost, "cardHours": round(cost["cardHours"], 2), "allCardHours": round(cost["allCardHours"], 2), "runs": len(cost["runs"]), "successfulRuns": len(cost["successfulRuns"])} for cost in pr_costs.values()]
    return {
        "generatedAt": now.isoformat(), "pools": sorted(pool_rows, key=lambda row: row["name"]),
        "trend": trend, "dailyQueue": daily_queue_rows, "queueDelaySummary": queue_delay_summary,
        "dailyUsage": daily_usage_rows, "queue": queue_rows, "ciWaiting": ci_waiting_rows,
        "prCosts": sorted(pr_cost_rows, key=lambda row: row["allCardHours"], reverse=True)[:10],
        "usagePeriod": usage_period,
        "dataQuality": {"matched_jobs": len(matched), "unmatched_jobs": len(facts) - len(matched)},
    }
