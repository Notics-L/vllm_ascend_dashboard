import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas.resource_metrics import RESOURCE_METRICS_CONFIG_KEY
from infrastructure.persistence.models import (
    KubernetesClusterConfig,
    ProjectDashboardConfig,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"interval_minutes": 1, "retention_days": 30}

TIME_RANGE_GRANULARITY = {
    "1h": 1,
    "24h": 5,
    "7d": 60,
    "30d": 360,
}

TIME_RANGE_DURATION = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


class ResourceMetricsQueryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def query_npu_metrics(
        self,
        cluster_ids: list[int] | None = None,
        time_range: str = "24h",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict:
        granularity_minutes = TIME_RANGE_GRANULARITY.get(time_range, 5)
        duration = TIME_RANGE_DURATION.get(time_range, timedelta(hours=24))

        if end_time is None:
            end_time = datetime.now(UTC)
        if start_time is None:
            start_time = end_time - duration
        start_time, end_time = self._as_mysql_utc(start_time), self._as_mysql_utc(end_time)

        cluster_query = select(KubernetesClusterConfig).where(KubernetesClusterConfig.enabled.is_(True))
        if cluster_ids:
            cluster_query = cluster_query.where(KubernetesClusterConfig.id.in_(cluster_ids))
        cluster_query = cluster_query.order_by(KubernetesClusterConfig.display_order.asc(), KubernetesClusterConfig.name.asc())
        cluster_result = await self.db.execute(cluster_query)
        clusters = list(cluster_result.scalars().all())

        raw_by_cluster: dict[int, list[ResourceNpuMetrics]] = {
            cluster.id: [] for cluster in clusters
        }
        if raw_by_cluster:
            # One bounded range scan is materially cheaper than running the
            # same range query once per resource pool.
            stmt = select(ResourceNpuMetrics).where(
                ResourceNpuMetrics.cluster_id.in_(raw_by_cluster),
                ResourceNpuMetrics.collected_at >= start_time,
                ResourceNpuMetrics.collected_at <= end_time,
            ).order_by(ResourceNpuMetrics.cluster_id.asc(), ResourceNpuMetrics.collected_at.asc())
            for metric in (await self.db.execute(stmt)).scalars().all():
                raw_by_cluster[metric.cluster_id].append(metric)

        result_clusters = []
        for cluster in clusters:
            raw_metrics = raw_by_cluster[cluster.id]

            if granularity_minutes <= 1:
                aggregated = raw_metrics
            else:
                aggregated = self._aggregate_metrics(raw_metrics, granularity_minutes)

            points = [self._normalize_metric(m) for m in aggregated]

            result_clusters.append({
                "cluster_id": cluster.id,
                "cluster_name": cluster.name,
                "metrics": points,
            })

        return {"clusters": result_clusters}

    async def query_node_metrics(
        self,
        cluster_ids: list[int] | None = None,
        node_names: list[str] | None = None,
        time_range: str = "24h",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict:
        granularity_minutes = TIME_RANGE_GRANULARITY.get(time_range, 5)
        duration = TIME_RANGE_DURATION.get(time_range, timedelta(hours=24))

        if end_time is None:
            end_time = datetime.now(UTC)
        if start_time is None:
            start_time = end_time - duration
        start_time, end_time = self._as_mysql_utc(start_time), self._as_mysql_utc(end_time)

        cluster_query = select(KubernetesClusterConfig).where(KubernetesClusterConfig.enabled.is_(True))
        if cluster_ids:
            cluster_query = cluster_query.where(KubernetesClusterConfig.id.in_(cluster_ids))
        cluster_query = cluster_query.order_by(
            KubernetesClusterConfig.display_order.asc(), KubernetesClusterConfig.name.asc()
        )
        cluster_result = await self.db.execute(cluster_query)
        clusters = list(cluster_result.scalars().all())

        result_clusters = []

        for cluster in clusters:
            stmt = select(ResourceNodeMetrics).where(
                ResourceNodeMetrics.cluster_id == cluster.id,
                ResourceNodeMetrics.collected_at >= start_time,
                ResourceNodeMetrics.collected_at <= end_time,
            ).order_by(ResourceNodeMetrics.collected_at.asc())

            normalized_node_names: list[str] = []
            if node_names:
                normalized_node_names = [name.strip() for name in node_names if name and name.strip()]
                if normalized_node_names:
                    # MySQL/aiomysql has produced inconsistent results for a
                    # one-element expanding IN bind in this read path. Use a
                    # scalar comparison for the common single-node filter;
                    # retain IN for the multi-node case.
                    if len(normalized_node_names) == 1:
                        stmt = stmt.where(ResourceNodeMetrics.node_name == normalized_node_names[0])
                    else:
                        stmt = stmt.where(ResourceNodeMetrics.node_name.in_(normalized_node_names))

            metrics_result = await self.db.execute(stmt)
            raw_metrics = list(metrics_result.scalars().all())

            # Some MySQL/aiomysql combinations can return no rows for a
            # single-value node predicate even when the same rows are
            # returned without that predicate. Retry the bounded cluster/time
            # query and apply the normalized name filter in Python so a driver
            # quirk cannot make an otherwise valid dashboard empty.
            if normalized_node_names and not raw_metrics:
                fallback_stmt = select(ResourceNodeMetrics).where(
                    ResourceNodeMetrics.cluster_id == cluster.id,
                    ResourceNodeMetrics.collected_at >= start_time,
                    ResourceNodeMetrics.collected_at <= end_time,
                ).order_by(ResourceNodeMetrics.collected_at.asc())
                fallback_result = await self.db.execute(fallback_stmt)
                allowed_names = set(normalized_node_names)
                raw_metrics = [
                    metric
                    for metric in fallback_result.scalars().all()
                    if metric.node_name and metric.node_name.strip() in allowed_names
                ]

            # 按 node_name 分组
            node_groups: dict[str, list[ResourceNodeMetrics]] = {}
            for m in raw_metrics:
                node_groups.setdefault(m.node_name, []).append(m)

            nodes = []
            for node_name in sorted(node_groups.keys()):
                node_raw = node_groups[node_name]
                if granularity_minutes <= 1:
                    aggregated = node_raw
                else:
                    aggregated = self._aggregate_node_metrics(node_raw, granularity_minutes)
                points = [self._normalize_node_metric(m) for m in aggregated]
                nodes.append({
                    "node_name": node_name,
                    "metrics": points,
                })

            result_clusters.append({
                "cluster_id": cluster.id,
                "cluster_name": cluster.name,
                "nodes": nodes,
            })

        return {"clusters": result_clusters}

    def _aggregate_metrics(self, raw_metrics: list[ResourceNpuMetrics], granularity_minutes: int) -> list[dict]:
        if not raw_metrics:
            return []

        grouped: dict[str, list[ResourceNpuMetrics]] = {}
        for m in raw_metrics:
            bucket = self._time_bucket(m.collected_at, granularity_minutes)
            if bucket not in grouped:
                grouped[bucket] = []
            grouped[bucket].append(m)

        result = []
        for bucket_key in sorted(grouped.keys()):
            group = grouped[bucket_key]
            avg_utilization = sum(m.npu_utilization for m in group) / len(group)
            avg_pods_count = sum(m.executing_pods_count for m in group) / len(group)
            avg_pr_count = sum(m.pr_count for m in group) / len(group)
            last_metric = group[-1]

            result.append({
                "collected_at": last_metric.collected_at,
                "npu_utilization": round(avg_utilization, 2),
                "npu_total": last_metric.npu_total,
                "npu_used": last_metric.npu_used,
                "npu_available": last_metric.npu_available,
                "executing_pods_count": round(avg_pods_count),
                "pr_count": round(avg_pr_count),
                "top_pods": last_metric.top_pods_json or [],
            })

        return result

    def _normalize_metric(self, m: ResourceNpuMetrics | dict) -> dict:
        if isinstance(m, ResourceNpuMetrics):
            dt = m.collected_at
            result = {
                "npu_utilization": m.npu_utilization,
                "npu_total": m.npu_total,
                "npu_used": m.npu_used,
                "npu_available": m.npu_available,
                "executing_pods_count": m.executing_pods_count,
                "pr_count": m.pr_count,
                "top_pods": m.top_pods_json or [],
            }
        else:
            dt = m.get("collected_at")
            result = dict(m)
        # MySQL TIMESTAMP 按 session 时区返回 naive datetime（容器为 UTC），
        # 补上 UTC 时区，使 Pydantic 序列化为带 +00:00 的 ISO 串，
        # 前端 dayjs 据此按浏览器本地时区显示，避免 8 小时偏差。
        if dt is not None and getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=UTC)
        result["collected_at"] = dt
        return result

    def _aggregate_node_metrics(
        self, raw_metrics: list[ResourceNodeMetrics], granularity_minutes: int
    ) -> list[dict]:
        if not raw_metrics:
            return []

        grouped: dict[str, list[ResourceNodeMetrics]] = {}
        for m in raw_metrics:
            bucket = self._time_bucket(m.collected_at, granularity_minutes)
            if bucket not in grouped:
                grouped[bucket] = []
            grouped[bucket].append(m)

        result = []
        for bucket_key in sorted(grouped.keys()):
            group = grouped[bucket_key]
            avg_npu_utilization = sum(m.npu_utilization for m in group) / len(group)
            avg_cpu_utilization = sum(m.cpu_utilization for m in group) / len(group)
            avg_memory_utilization = sum(m.memory_utilization for m in group) / len(group)
            avg_pods_count = sum(m.executing_pods_count for m in group) / len(group)
            last_metric = group[-1]

            result.append({
                "collected_at": last_metric.collected_at,
                "npu_utilization": round(avg_npu_utilization, 2),
                "npu_total": last_metric.npu_total,
                "npu_used": last_metric.npu_used,
                "npu_available": last_metric.npu_available,
                "cpu_utilization": round(avg_cpu_utilization, 2),
                "memory_utilization": round(avg_memory_utilization, 2),
                "executing_pods_count": round(avg_pods_count),
            })

        return result

    def _normalize_node_metric(self, m: ResourceNodeMetrics | dict) -> dict:
        if isinstance(m, ResourceNodeMetrics):
            dt = m.collected_at
            result = {
                "npu_utilization": m.npu_utilization,
                "npu_total": m.npu_total,
                "npu_used": m.npu_used,
                "npu_available": m.npu_available,
                "cpu_utilization": m.cpu_utilization,
                "memory_utilization": m.memory_utilization,
                "executing_pods_count": m.executing_pods_count,
            }
        else:
            dt = m.get("collected_at")
            result = dict(m)
        if dt is not None and getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=UTC)
        result["collected_at"] = dt
        return result

    def _time_bucket(self, dt: datetime, granularity_minutes: int) -> str:
        total_minutes = int(dt.timestamp()) // 60
        bucket = total_minutes // granularity_minutes
        return str(bucket)

    @staticmethod
    def _as_mysql_utc(value: datetime) -> datetime:
        """MySQL DATETIME is timezone-naive; compare it with naive UTC values."""
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    async def get_config(self) -> dict:
        return await self._get_config()

    async def update_config(self, interval_minutes: int | None = None, retention_days: int | None = None) -> dict:
        current = await self._get_config()
        if interval_minutes is not None:
            current["interval_minutes"] = interval_minutes
        if retention_days is not None:
            current["retention_days"] = retention_days

        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == RESOURCE_METRICS_CONFIG_KEY
        )
        result = await self.db.execute(stmt)
        config_row = result.scalar_one_or_none()

        if config_row:
            config_row.config_value = current
            config_row.updated_at = datetime.now(UTC)
        else:
            config_row = ProjectDashboardConfig(
                config_key=RESOURCE_METRICS_CONFIG_KEY,
                config_value=current,
                description="NPU 指标采集配置",
            )
            self.db.add(config_row)

        await self.db.commit()
        return current

    async def _get_config(self) -> dict:
        stmt = select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == RESOURCE_METRICS_CONFIG_KEY
        )
        result = await self.db.execute(stmt)
        config_row = result.scalar_one_or_none()
        if config_row and config_row.config_value:
            return dict(config_row.config_value)
        return dict(DEFAULT_CONFIG)
