"""Aggregate normalized CI NPU job facts for the dashboard read API."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import CINpuJobFact
from npu_queue.snapshots import is_running, is_waiting, physical_running_counts, recent_snapshots

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _beijing_time(value: datetime) -> datetime:
    """MySQL returns naive UTC timestamps; display dashboard times in Beijing time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
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


async def build_dashboard(db: AsyncSession) -> dict[str, Any]:
    facts = list((await db.scalars(select(CINpuJobFact).order_by(CINpuJobFact.queued_at.desc()))).all())
    now = datetime.now(UTC)
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

    physical_running = await physical_running_counts(db)
    current_point = {
        "timestamp": _beijing_time(now).strftime("%H:%M"),
        "waitingCards": sum(row.mapped_cards or 0 for row in all_waiting),
        "runningCards": physical_running["runningCards"],
        "waitingJobs": len(all_waiting), "runningJobs": physical_running["runningJobs"],
    }
    snapshots = await recent_snapshots(db, now=now)
    trend = [{
        "timestamp": _beijing_time(snapshot.captured_at).strftime("%H:%M"),
        "waitingCards": snapshot.waiting_cards,
        "runningCards": snapshot.running_cards,
        "waitingJobs": snapshot.waiting_jobs,
        "runningJobs": snapshot.running_jobs,
    } for snapshot in snapshots]
    # Snapshots provide the 24h history; retain a live point between two runs.
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
        card_hours = runtime_minutes * (fact.mapped_cards or 0) / 60
        day = fact.completed_at.date().isoformat()
        daily_usage[day]["cardHours"] += card_hours
        daily_usage[day]["jobs"] += 1
        if fact.conclusion != "success":
            daily_usage[day]["failedCardHours"] += card_hours
        key = f"pr-{fact.pr_number}" if fact.pr_number else f"run-{fact.run_id}"
        cost = pr_costs.setdefault(key, {"key": key, "title": fact.job_name or key, "branch": key, "cardHours": 0.0, "allCardHours": 0.0, "runs": set(), "successfulRuns": set()})
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
    daily_usage_rows = [{"date": day, "cardHours": round(values["cardHours"], 2), "jobs": int(values["jobs"]), "failedCardHours": round(values["failedCardHours"], 2)} for day, values in sorted(daily_usage.items())]
    pr_cost_rows = [{**cost, "cardHours": round(cost["cardHours"], 2), "allCardHours": round(cost["allCardHours"], 2), "runs": len(cost["runs"]), "successfulRuns": len(cost["successfulRuns"])} for cost in pr_costs.values()]
    return {
        "generatedAt": now.isoformat(), "pools": sorted(pool_rows, key=lambda row: row["name"]),
        "trend": trend, "dailyQueue": daily_queue_rows, "queueDelaySummary": queue_delay_summary,
        "dailyUsage": daily_usage_rows, "queue": queue_rows,
        "prCosts": sorted(pr_cost_rows, key=lambda row: row["allCardHours"], reverse=True)[:15],
        "dataQuality": {"matched_jobs": len(matched), "unmatched_jobs": len(facts) - len(matched)},
    }
