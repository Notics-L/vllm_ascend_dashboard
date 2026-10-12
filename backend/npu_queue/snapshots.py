"""Durable snapshots for the current Queue / Running state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import CINpuJobFact, CINpuQueueSnapshot, ResourceNpuMetrics


def is_waiting(fact: CINpuJobFact) -> bool:
    return fact.status == "queued" or (fact.started_at == fact.queued_at and fact.completed_at is None)


def is_running(fact: CINpuJobFact) -> bool:
    return fact.started_at is not None and fact.completed_at is None and not is_waiting(fact)


def current_counts(facts: Iterable[CINpuJobFact]) -> dict[str, int]:
    matched = [fact for fact in facts if fact.mapping_status == "matched" and fact.mapped_cards]
    waiting = [fact for fact in matched if is_waiting(fact)]
    running = [fact for fact in matched if is_running(fact)]
    return {
        "waitingJobs": len(waiting),
        "waitingCards": sum(fact.mapped_cards or 0 for fact in waiting),
        "runningJobs": len(running),
        "runningCards": sum(fact.mapped_cards or 0 for fact in running),
    }


async def physical_running_counts(db: AsyncSession) -> dict[str, float | int]:
    """Read the latest Kubernetes-backed running demand for every resource pool."""
    latest_by_cluster = (
        select(
            ResourceNpuMetrics.cluster_id,
            func.max(ResourceNpuMetrics.collected_at).label("collected_at"),
        )
        .group_by(ResourceNpuMetrics.cluster_id)
        .subquery()
    )
    rows = list(
        (
            await db.scalars(
                select(ResourceNpuMetrics).join(
                    latest_by_cluster,
                    and_(
                        ResourceNpuMetrics.cluster_id == latest_by_cluster.c.cluster_id,
                        ResourceNpuMetrics.collected_at == latest_by_cluster.c.collected_at,
                    ),
                )
            )
        ).all()
    )
    return {
        "runningJobs": sum(row.executing_pods_count or 0 for row in rows),
        "runningCards": round(sum(row.npu_used or 0 for row in rows), 2),
    }


async def physical_queue_state(db: AsyncSession) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    """Read both Pending and Running demand from the same Kubernetes snapshot."""
    latest_by_cluster = (
        select(ResourceNpuMetrics.cluster_id, func.max(ResourceNpuMetrics.collected_at).label("collected_at"))
        .group_by(ResourceNpuMetrics.cluster_id)
        .subquery()
    )
    rows = list((await db.scalars(select(ResourceNpuMetrics).join(
        latest_by_cluster,
        and_(ResourceNpuMetrics.cluster_id == latest_by_cluster.c.cluster_id, ResourceNpuMetrics.collected_at == latest_by_cluster.c.collected_at),
    ))).all())
    return physical_queue_state_from_metrics(rows)


def physical_queue_state_from_metrics(rows: Iterable[ResourceNpuMetrics]) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    """Aggregate a single Kubernetes resource-metrics collection."""
    metrics = list(rows)
    pending = [
        {**pod, "cluster_id": row.cluster_id, "cluster_name": row.cluster_name}
        for row in metrics for pod in (row.top_pods_json or [])
        if isinstance(pod, dict) and pod.get("phase") == "Pending"
    ]
    return ({
        "waitingJobs": len(pending),
        "waitingCards": round(sum(float(pod.get("npu") or 0) for pod in pending), 2),
        "runningJobs": sum(row.executing_pods_count or 0 for row in metrics),
        "runningCards": round(sum(row.npu_used or 0 for row in metrics), 2),
    }, pending)


async def physical_queue_trend(db: AsyncSession, *, now: datetime, hours: int = 24) -> list[tuple[datetime, dict[str, float | int]]]:
    """Build Queue / Running history solely from Kubernetes resource snapshots."""
    rows = list((await db.scalars(
        select(ResourceNpuMetrics)
        .where(ResourceNpuMetrics.collected_at >= now - timedelta(hours=hours))
        .order_by(ResourceNpuMetrics.collected_at.asc())
    )).all())
    grouped: dict[datetime, list[ResourceNpuMetrics]] = {}
    for row in rows:
        grouped.setdefault(row.collected_at, []).append(row)
    return [
        (captured_at, physical_queue_state_from_metrics(metrics)[0])
        for captured_at, metrics in grouped.items()
    ]


async def collect_current_snapshot(db: AsyncSession, *, captured_at: datetime | None = None) -> CINpuQueueSnapshot:
    # Queue and running counts must share the Kubernetes snapshot source.
    counts, _ = await physical_queue_state(db)
    snapshot = CINpuQueueSnapshot(
        captured_at=captured_at or datetime.now(UTC),
        waiting_jobs=counts["waitingJobs"],
        waiting_cards=counts["waitingCards"],
        running_jobs=counts["runningJobs"],
        running_cards=counts["runningCards"],
    )
    db.add(snapshot)
    await db.commit()
    return snapshot


async def recent_snapshots(db: AsyncSession, *, now: datetime, hours: int = 24) -> list[CINpuQueueSnapshot]:
    since = now - timedelta(hours=hours)
    return list((await db.scalars(
        select(CINpuQueueSnapshot)
        .where(CINpuQueueSnapshot.captured_at >= since)
        .order_by(CINpuQueueSnapshot.captured_at.asc())
    )).all())


async def cleanup_snapshots(db: AsyncSession, *, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await db.execute(delete(CINpuQueueSnapshot).where(CINpuQueueSnapshot.captured_at < cutoff))
    await db.commit()
    return int(result.rowcount or 0)
