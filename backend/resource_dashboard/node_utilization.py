"""Node active-time utilization heatmap built from persisted K8s snapshots."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import KubernetesClusterConfig, ResourceNodeMetrics


@dataclass(frozen=True)
class NodeMetricSample:
    """Minimal projection needed for heatmap interval integration."""

    cluster_id: int
    cluster_name: str
    node_name: str
    npu_used: float | None
    collected_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bucket_floor(value: datetime, hours: int) -> datetime:
    value = _utc(value)
    epoch = int(value.timestamp())
    size = hours * 3600
    # Dashboard dates follow Asia/Shanghai. Offset before flooring so a daily
    # bucket starts at local midnight (16:00 UTC of the previous date).
    offset = 8 * 3600
    return datetime.fromtimestamp(epoch - (epoch + offset) % size, tz=UTC)


def build_node_utilization_heatmap(
    rows: Iterable[ResourceNodeMetrics | NodeMetricSample],
    *,
    start: datetime,
    end: datetime,
    bucket_hours: int = 24,
) -> dict[str, Any]:
    """Integrate active intervals (`npu_used > 0`) into fixed UTC buckets."""
    start, end = _utc(start), _utc(end)
    groups: dict[tuple[int, str, str], list[ResourceNodeMetrics]] = defaultdict(list)
    for row in rows:
        groups[(row.cluster_id, row.cluster_name, row.node_name)].append(row)

    nodes = []
    total_active = 0.0
    window_seconds = max(0.0, (end - start).total_seconds())
    bucket_size = timedelta(hours=bucket_hours)
    first_bucket = _bucket_floor(start, bucket_hours)

    for (cluster_id, cluster_name, node_name), metrics in sorted(groups.items()):
        metrics.sort(key=lambda row: _utc(row.collected_at))
        timestamps = [_utc(row.collected_at) for row in metrics]
        deltas = [
            (right - left).total_seconds()
            for left, right in zip(timestamps, timestamps[1:], strict=False)
            if 0 < (right - left).total_seconds() <= 86400
        ]
        expected_seconds = median(deltas) if deltas else 60.0
        active_by_bucket: dict[datetime, float] = defaultdict(float)
        covered_by_bucket: dict[datetime, float] = defaultdict(float)

        for index, metric in enumerate(metrics):
            interval_start = max(start, _utc(metric.collected_at))
            next_time = timestamps[index + 1] if index + 1 < len(metrics) else end
            # A missing collector interval is not assumed to be active forever.
            interval_end = min(end, next_time, _utc(metric.collected_at) + timedelta(seconds=expected_seconds * 2))
            if interval_end <= interval_start:
                continue
            cursor = interval_start
            while cursor < interval_end:
                bucket_start = _bucket_floor(cursor, bucket_hours)
                segment_end = min(interval_end, bucket_start + bucket_size)
                seconds = (segment_end - cursor).total_seconds()
                covered_by_bucket[bucket_start] += seconds
                if (metric.npu_used or 0) > 0:
                    active_by_bucket[bucket_start] += seconds
                    total_active += seconds
                cursor = segment_end

        buckets = []
        cursor = first_bucket
        while cursor < end:
            bucket_end = min(cursor + bucket_size, end)
            effective_start = max(cursor, start)
            duration = (bucket_end - effective_start).total_seconds()
            active = active_by_bucket.get(cursor, 0.0)
            covered = covered_by_bucket.get(cursor, 0.0)
            buckets.append(
                {
                    "start": cursor.isoformat(),
                    "end": bucket_end.isoformat(),
                    "utilization": round(active / duration * 100, 2) if covered > 0 and duration > 0 else None,
                    "active_seconds": round(active),
                    "window_seconds": round(duration),
                    "coverage": round(covered / duration, 4) if duration > 0 else 0,
                }
            )
            cursor += bucket_size

        nodes.append(
            {
                "cluster_id": cluster_id,
                "cluster_name": cluster_name,
                "node_name": node_name,
                "buckets": buckets,
            }
        )

    denominator = len(nodes) * window_seconds
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket": f"{bucket_hours}h",
        "overall_utilization": round(total_active / denominator * 100, 2) if denominator else 0,
        "nodes": nodes,
    }


async def query_node_utilization_heatmap(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    pool: str | None = None,
    bucket_hours: int = 24,
) -> dict[str, Any]:
    cluster_stmt = select(KubernetesClusterConfig).where(KubernetesClusterConfig.enabled.is_(True))
    if pool:
        cluster_stmt = cluster_stmt.where(KubernetesClusterConfig.name == pool)
    clusters = list((await db.scalars(cluster_stmt)).all())
    cluster_ids = [cluster.id for cluster in clusters]
    if not cluster_ids:
        result = build_node_utilization_heatmap([], start=start, end=end, bucket_hours=bucket_hours)
        return {"pool": pool, **result}

    # Include one day before the window so the first interval can be bounded by
    # the first in-window sample without turning an unknown gap into zero use.
    # Do not instantiate hundreds of thousands of ORM objects just to read
    # four heatmap fields. The composite index keeps this bounded range scan
    # efficient for a seven-day, minute-level history.
    stmt = (
        select(
            ResourceNodeMetrics.cluster_id,
            ResourceNodeMetrics.cluster_name,
            ResourceNodeMetrics.node_name,
            ResourceNodeMetrics.npu_used,
            ResourceNodeMetrics.collected_at,
        )
        .where(
            ResourceNodeMetrics.cluster_id.in_(cluster_ids),
            ResourceNodeMetrics.collected_at >= _utc(start).replace(tzinfo=None) - timedelta(days=1),
            ResourceNodeMetrics.collected_at <= _utc(end).replace(tzinfo=None),
        )
        .order_by(ResourceNodeMetrics.cluster_id.asc(), ResourceNodeMetrics.node_name.asc(), ResourceNodeMetrics.collected_at.asc())
    )
    rows = [NodeMetricSample(*row) for row in (await db.execute(stmt)).tuples().all()]
    result = build_node_utilization_heatmap(rows, start=start, end=end, bucket_hours=bucket_hours)
    return {"pool": pool, **result}
