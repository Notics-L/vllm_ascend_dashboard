"""Collector-only resource metric collection and retention work."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.schemas.resource_metrics import RESOURCE_METRICS_CONFIG_KEY
from infrastructure.clients.resource_sync import get_remote_resource_dashboard_client
from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    KubernetesClusterConfig,
    ProjectDashboardConfig,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
)
from resource_dashboard.service import ResourceDashboardService

logger = logging.getLogger(__name__)


class ResourceMetricsCollector:
    """Writes node/NPU snapshots; it is intentionally unavailable to API routes."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def collect_snapshot(self) -> int:
        clusters = list(
            (
                await self.db.execute(
                    select(KubernetesClusterConfig).where(
                        KubernetesClusterConfig.enabled.is_(True)
                    )
                )
            ).scalars()
        )
        if not clusters:
            logger.info("No enabled clusters; skipping resource metric collection")
            return 0

        remote_client = get_remote_resource_dashboard_client()
        if remote_client.enabled:
            logger.info("Using remote resource metrics source: %s", settings.RESOURCE_METRICS_REMOTE_URL)
            dashboard = await remote_client.fetch_dashboard()
            self._localize_cluster_ids(dashboard, clusters)
        else:
            dashboard = await asyncio.wait_for(
                ResourceDashboardService().build_dashboard(clusters, include_pods=True),
                timeout=30,
            )
        now = datetime.now(UTC)
        executed_by_cluster: dict[int, list] = {}
        for pod in dashboard.executed_pods:
            executed_by_cluster.setdefault(pod.cluster_id, []).append(pod)
        collected = 0
        for summary in dashboard.clusters:
            if summary.error:
                logger.warning("Skipping cluster %s: %s", summary.cluster_name, summary.error)
                continue
            pods = summary.executing_pods or []
            # PR demand needs every currently executing NPU pod. Keeping only
            # the five largest pods undercounts PRs with a wider test matrix.
            resource_pods = sorted(pods, key=lambda pod: pod.requests.npu, reverse=True)
            timeline_pods = [*pods, *executed_by_cluster.get(summary.cluster_id, [])]
            self.db.add(
                ResourceNpuMetrics(
                    cluster_id=summary.cluster_id,
                    cluster_name=summary.cluster_name,
                    npu_total=summary.total.npu,
                    npu_used=summary.used.npu,
                    npu_available=summary.available.npu,
                    npu_utilization=self._percentage(summary.used.npu, summary.total.npu),
                    executing_pods_count=summary.executing_pods_count,
                    pr_count=len({pod.pr_number for pod in pods if pod.pr_number}),
                    top_pods_json=[self._serialize_pod(pod) for pod in resource_pods],
                    pod_timeline_json=[self._serialize_pod(pod) for pod in timeline_pods],
                    collected_at=now,
                )
            )
            collected += 1
            for node in summary.node_resources or []:
                if node.total.npu <= 0:
                    continue
                self.db.add(
                    ResourceNodeMetrics(
                        cluster_id=summary.cluster_id,
                        cluster_name=summary.cluster_name,
                        node_name=node.node_name,
                        cpu_cores_total=node.total.cpu_cores,
                        cpu_cores_used=node.used.cpu_cores,
                        cpu_cores_available=node.available.cpu_cores,
                        cpu_utilization=self._percentage(node.used.cpu_cores, node.total.cpu_cores),
                        memory_bytes_total=node.total.memory_bytes,
                        memory_bytes_used=node.used.memory_bytes,
                        memory_bytes_available=node.available.memory_bytes,
                        memory_utilization=self._percentage(node.used.memory_bytes, node.total.memory_bytes),
                        npu_total=node.total.npu,
                        npu_used=node.used.npu,
                        npu_available=node.available.npu,
                        npu_utilization=self._percentage(node.used.npu, node.total.npu),
                        executing_pods_count=node.executing_pods_count,
                        collected_at=now,
                    )
                )
        await self.db.commit()
        return collected

    @staticmethod
    def _localize_cluster_ids(dashboard, clusters: list[KubernetesClusterConfig]) -> None:
        """Map production cluster IDs to the local imported configuration IDs."""
        ids_by_name = {cluster.name: cluster.id for cluster in clusters}
        for summary in dashboard.clusters:
            local_id = ids_by_name.get(summary.cluster_name)
            if local_id is None:
                continue
            summary.cluster_id = local_id
            for pod in summary.executing_pods:
                pod.cluster_id = local_id
        for pod in [*dashboard.executing_pods, *dashboard.executed_pods]:
            local_id = ids_by_name.get(pod.cluster_name)
            if local_id is not None:
                pod.cluster_id = local_id

    async def cleanup_old_metrics(self) -> int:
        config = await self._get_config()
        cutoff = datetime.now(UTC) - timedelta(days=int(config["retention_days"]))
        npu_result = await self.db.execute(
            delete(ResourceNpuMetrics).where(ResourceNpuMetrics.collected_at < cutoff)
        )
        node_result = await self.db.execute(
            delete(ResourceNodeMetrics).where(ResourceNodeMetrics.collected_at < cutoff)
        )
        await self.db.commit()
        return npu_result.rowcount + node_result.rowcount

    async def _get_config(self) -> dict:
        row = (
            await self.db.execute(
                select(ProjectDashboardConfig).where(
                    ProjectDashboardConfig.config_key == RESOURCE_METRICS_CONFIG_KEY
                )
            )
        ).scalar_one_or_none()
        return dict(row.config_value) if row and row.config_value else {
            "interval_minutes": 1,
            "retention_days": 30,
        }

    @staticmethod
    def _serialize_pod(pod) -> dict:
        """Keep enough lifecycle state to render the 24-hour Pod timeline."""
        return {
            "name": pod.name,
            "namespace": pod.namespace,
            "npu": pod.requests.npu,
            "repository": pod.repository,
            "repository_url": pod.repository_url,
            "workflow_path": pod.workflow_path,
            "workflow_url": pod.workflow_url,
            "pr_number": pod.pr_number,
            "pr_url": pod.pr_url,
            "job_workflow_ref": pod.job_workflow_ref,
            "phase": pod.phase,
            "created_at": pod.created_at.isoformat() if pod.created_at else None,
            "started_at": pod.started_at.isoformat() if pod.started_at else None,
            "finished_at": pod.finished_at.isoformat() if pod.finished_at else None,
            "duration_seconds": pod.duration_seconds,
        }

    @staticmethod
    def _percentage(used: float, total: float) -> float:
        return round(used / total * 100, 2) if total > 0 else 0
