"""Durable snapshots for the current Queue / Running state."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

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


def _metrics_for_pool(
    rows: Iterable[ResourceNpuMetrics], pool: str | None = None
) -> list[ResourceNpuMetrics]:
    metrics = list(rows)
    return metrics if not pool else [row for row in metrics if row.cluster_name == pool]


def physical_queue_state_from_metrics(
    rows: Iterable[ResourceNpuMetrics], pool: str | None = None
) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    """Aggregate a single Kubernetes resource-metrics collection."""
    metrics = _metrics_for_pool(rows, pool)
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


async def physical_queue_trend(
    db: AsyncSession, *, now: datetime, hours: int = 24, pool: str | None = None
) -> list[tuple[datetime, dict[str, float | int]]]:
    """Build Queue / Running history solely from Kubernetes resource snapshots."""
    filters = [ResourceNpuMetrics.collected_at >= now - timedelta(hours=hours)]
    if pool:
        filters.append(ResourceNpuMetrics.cluster_name == pool)
    rows = list(
        (
            await db.scalars(
                select(ResourceNpuMetrics)
                .where(*filters)
                .order_by(ResourceNpuMetrics.collected_at.asc())
            )
        ).all()
    )
    grouped: dict[datetime, list[ResourceNpuMetrics]] = {}
    for row in rows:
        grouped.setdefault(row.collected_at, []).append(row)
    return [
        (captured_at, physical_queue_state_from_metrics(metrics, pool)[0])
        for captured_at, metrics in grouped.items()
    ]


def physical_queue_detail_from_metrics(
    rows: Iterable[ResourceNpuMetrics], pool: str | None = None
) -> tuple[dict[str, float | int], list[dict[str, Any]]]:
    """Build Pending/Running Pod detail from one persisted Kubernetes snapshot."""
    metrics = _metrics_for_pool(rows, pool)
    counts, _ = physical_queue_state_from_metrics(metrics)
    pods = [
        {
            **pod,
            "cluster_id": row.cluster_id,
            "cluster_name": row.cluster_name,
        }
        for row in metrics
        for pod in (row.top_pods_json or [])
        if isinstance(pod, dict) and pod.get("phase") in {"Pending", "Running", "Unknown"}
    ]
    return counts, pods


async def physical_queue_detail(
    db: AsyncSession, *, captured_at: datetime, pool: str | None = None
) -> tuple[datetime, dict[str, float | int], list[dict[str, Any]]] | None:
    """Load the latest persisted snapshot at or before a selected chart point."""
    lookup_time = captured_at.astimezone(UTC).replace(tzinfo=None)
    filters = [ResourceNpuMetrics.collected_at <= lookup_time]
    if pool:
        filters.append(ResourceNpuMetrics.cluster_name == pool)
    actual_time = await db.scalar(
        select(func.max(ResourceNpuMetrics.collected_at)).where(*filters)
    )
    if actual_time is None:
        return None
    rows = list(
        (
            await db.scalars(
                select(ResourceNpuMetrics).where(
                    ResourceNpuMetrics.collected_at == actual_time,
                    *([ResourceNpuMetrics.cluster_name == pool] if pool else []),
                )
            )
        ).all()
    )
    counts, pods = physical_queue_detail_from_metrics(rows, pool)
    if actual_time.tzinfo is None:
        actual_time = actual_time.replace(tzinfo=UTC)
    return actual_time, counts, pods


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
