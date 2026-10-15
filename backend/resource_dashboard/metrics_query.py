"""Build resource dashboard responses from persisted metric snapshots."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas import (
    ClusterResourceSummary,
    ResourceDashboardResponse,
    ResourceNodeInfo,
    ResourcePodInfo,
    ResourceQuantity,
)
from infrastructure.persistence.models import (
    KubernetesClusterConfig,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
)


class PersistedResourceMetricsService:
    """Read the latest local snapshot without contacting Kubernetes."""

    async def build_dashboard(
        self,
        db: AsyncSession,
        clusters: list[KubernetesClusterConfig],
        cluster_ids: list[int] | None = None,
        include_pods: bool = True,
    ) -> ResourceDashboardResponse:
        selected = [
            cluster
            for cluster in clusters
            if not cluster_ids or cluster.id in cluster_ids
        ]
        summaries = await self._build_summaries(db, selected, include_pods)
        executing_pods = [pod for summary in summaries for pod in summary.executing_pods]
        overall = self._overall(summaries)
        return ResourceDashboardResponse(
            overall=overall,
            clusters=summaries,
            executing_pods=executing_pods,
            executed_pods=[],
        )

    async def build_cluster_summary(
        self,
        db: AsyncSession,
        cluster: KubernetesClusterConfig,
        include_pods: bool = True,
    ) -> ClusterResourceSummary:
        summaries = await self._build_summaries(db, [cluster], include_pods)
        return summaries[0]

    async def _build_summaries(
        self,
        db: AsyncSession,
        clusters: list[KubernetesClusterConfig],
        include_pods: bool,
    ) -> list[ClusterResourceSummary]:
        if not clusters:
            return []
        cluster_ids = [cluster.id for cluster in clusters]
        # A summary and its node rows must come from the same collection run.
        # Selecting their latest timestamps independently previously combined a
        # fresh zero-only summary with an older non-zero node snapshot.
        latest_snapshot = (
            select(
                ResourceNodeMetrics.cluster_id,
                func.max(ResourceNodeMetrics.collected_at).label("collected_at"),
            )
            .where(ResourceNodeMetrics.cluster_id.in_(cluster_ids))
            .group_by(ResourceNodeMetrics.cluster_id)
            .subquery()
        )
        npu_rows = (
            await db.execute(
                select(ResourceNpuMetrics).join(
                    latest_snapshot,
                    and_(
                        ResourceNpuMetrics.cluster_id == latest_snapshot.c.cluster_id,
                        ResourceNpuMetrics.collected_at == latest_snapshot.c.collected_at,
                    ),
                )
            )
        ).scalars().all()
        node_rows = (
            await db.execute(
                select(ResourceNodeMetrics).join(
                    latest_snapshot,
                    and_(
                        ResourceNodeMetrics.cluster_id == latest_snapshot.c.cluster_id,
                        ResourceNodeMetrics.collected_at == latest_snapshot.c.collected_at,
                    ),
                )
            )
        ).scalars().all()

        npu_by_cluster = {row.cluster_id: row for row in npu_rows}
        nodes_by_cluster: dict[int, list[ResourceNodeMetrics]] = {}
        for row in node_rows:
            nodes_by_cluster.setdefault(row.cluster_id, []).append(row)

        summaries = []
        for cluster in clusters:
            npu = npu_by_cluster.get(cluster.id)
            nodes = nodes_by_cluster.get(cluster.id, [])
            if npu is None:
                summaries.append(
                    ClusterResourceSummary(
                        cluster_id=cluster.id,
                        cluster_name=cluster.name,
                        total=ResourceQuantity(),
                        used=ResourceQuantity(),
                        available=ResourceQuantity(),
                        scope={"source": "persisted_remote_metrics"},
                        error="no persisted resource metrics available",
                    )
                )
                continue

            node_resources = [
                ResourceNodeInfo(
                    node_name=row.node_name,
                    total=ResourceQuantity(
                        cpu_cores=row.cpu_cores_total or 0,
                        memory_bytes=row.memory_bytes_total or 0,
                        npu=row.npu_total or 0,
                    ),
                    used=ResourceQuantity(
                        cpu_cores=row.cpu_cores_used or 0,
                        memory_bytes=row.memory_bytes_used or 0,
                        npu=row.npu_used or 0,
                    ),
                    available=ResourceQuantity(
                        cpu_cores=row.cpu_cores_available or 0,
                        memory_bytes=row.memory_bytes_available or 0,
                        npu=row.npu_available or 0,
                    ),
                    executing_pods_count=row.executing_pods_count or 0,
                )
                for row in nodes
            ]
            pods = self._pods(npu, include_pods)
            pending_pods = [pod for pod in pods if pod.phase == "Pending"]
            node_total_cpu = sum(row.cpu_cores_total or 0 for row in nodes)
            node_used_cpu = sum(row.cpu_cores_used or 0 for row in nodes)
            node_available_cpu = sum(row.cpu_cores_available or 0 for row in nodes)
            node_total_memory = sum(row.memory_bytes_total or 0 for row in nodes)
            node_used_memory = sum(row.memory_bytes_used or 0 for row in nodes)
            node_available_memory = sum(row.memory_bytes_available or 0 for row in nodes)
            # Node metrics are the physical-resource source of truth.  Using
            # their sums keeps the cluster aggregate and displayed node rows
            # on exactly one NPU accounting basis.
            node_total_npu = sum(row.npu_total or 0 for row in nodes)
            node_used_npu = sum(row.npu_used or 0 for row in nodes)
            node_available_npu = sum(row.npu_available or 0 for row in nodes)
            summaries.append(
                ClusterResourceSummary(
                    cluster_id=cluster.id,
                    cluster_name=cluster.name,
                    total=ResourceQuantity(
                        cpu_cores=node_total_cpu,
                        memory_bytes=node_total_memory,
                        npu=node_total_npu,
                    ),
                    used=ResourceQuantity(
                        cpu_cores=node_used_cpu,
                        memory_bytes=node_used_memory,
                        npu=node_used_npu,
                    ),
                    available=ResourceQuantity(
                        cpu_cores=node_available_cpu,
                        memory_bytes=node_available_memory,
                        npu=node_available_npu,
                    ),
                    executing_pods_count=npu.executing_pods_count or 0,
                    pending_pods_count=len(pending_pods),
                    pending_npu=sum(pod.requests.npu for pod in pending_pods),
                    node_resources=node_resources,
                    executing_pods=pods,
                    scope={
                        "source": "persisted_remote_metrics",
                        "collected_at": _timestamp(npu.collected_at),
                    },
                )
            )
        return summaries

    @staticmethod
    def _pods(row: ResourceNpuMetrics, include_pods: bool) -> list[ResourcePodInfo]:
        if not include_pods or not row.top_pods_json:
            return []
        pods: list[ResourcePodInfo] = []
        for item in row.top_pods_json:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            pods.append(
                ResourcePodInfo(
                    cluster_id=row.cluster_id,
                    cluster_name=row.cluster_name,
                    namespace=str(item.get("namespace") or ""),
                    name=str(item["name"]),
                    phase=item.get("phase"),
                    pr_number=item.get("pr_number"),
                    pr_url=item.get("pr_url"),
                    requests=ResourceQuantity(npu=item.get("npu") or 0),
                )
            )
        return pods

    @staticmethod
    def _overall(summaries: Iterable[ClusterResourceSummary]) -> ClusterResourceSummary:
        total = ResourceQuantity()
        used = ResourceQuantity()
        available = ResourceQuantity()
        running = 0
        executing = 0
        pending = 0
        pending_npu = 0.0
        for summary in summaries:
            if summary.error:
                continue
            total.cpu_cores += summary.total.cpu_cores
            total.memory_bytes += summary.total.memory_bytes
            total.npu += summary.total.npu
            used.cpu_cores += summary.used.cpu_cores
            used.memory_bytes += summary.used.memory_bytes
            used.npu += summary.used.npu
            available.cpu_cores += summary.available.cpu_cores
            available.memory_bytes += summary.available.memory_bytes
            available.npu += summary.available.npu
            running += summary.running_instances
            executing += summary.executing_pods_count
            pending += summary.pending_pods_count
            pending_npu += summary.pending_npu
        return ClusterResourceSummary(
            cluster_id=0,
            cluster_name="Total",
            total=total,
            used=used,
            available=available,
            running_instances=running,
            executing_pods_count=executing,
            pending_pods_count=pending,
            pending_npu=pending_npu,
            scope={"source": "persisted_remote_metrics", "all_clusters": True},
        )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()
