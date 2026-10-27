"""Create ten development-only Pending-Pod samples for NPU queue section 02.

This seed owns only the ``local-npu-queue-demo`` cluster.  It is idempotent:
rerunning it replaces metrics for that cluster and never touches users or
metrics belonging to configured clusters.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

repository_root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repository_root / "backend"), str(repository_root)]

from infrastructure.db.base import SessionLocal, engine
from infrastructure.persistence.models import (
    KubernetesClusterConfig,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
)

CLUSTER_NAME = "local-npu-queue-demo"


def _pending_pod(index: int, now: datetime) -> dict:
    cards = (1, 2, 4, 2, 1, 4, 2, 1, 4, 2)[index]
    waits = (7, 11, 18, 26, 34, 48, 61, 76, 92, 125)
    reasons = (
        ("Unschedulable", "0/3 nodes are available: 3 Insufficient huawei.com/Ascend910."),
        ("Unschedulable", "0/3 nodes are available: 3 node(s) did not match Pod's node affinity/selector."),
        ("Unschedulable", "0/3 nodes are available: 2 Insufficient huawei.com/Ascend910, 1 node(s) had untolerated taint."),
        ("Unschedulable", "0/3 nodes are available: 3 Insufficient huawei.com/Ascend910."),
        ("Unschedulable", "0/3 nodes are available: 3 node(s) did not match Pod's node affinity/selector."),
    )
    reason, message = reasons[index % len(reasons)]
    return {
        "name": f"queue-demo-{index + 1:02d}", "namespace": "local-demo", "phase": "Pending",
        "npu": cards, "created_at": (now - timedelta(minutes=waits[index])).isoformat(),
        "last_observed_at": now.isoformat(), "duration_seconds": None,
        "scheduling_reason": reason, "scheduling_message": message,
        "node_selector": {"npu.huawei.com/model": "Ascend910B"} if index in {1, 4, 6} else {},
        "repository": "vllm-ascend/vllm-ascend", "repository_url": "https://github.com/vllm-ascend/vllm-ascend",
        "workflow_path": ".github/workflows/npu-ci.yml", "workflow_url": None,
        "workflow_run_id": 9_100_000 + index, "run_url": None, "pr_number": 500 + index, "pr_url": None,
    }


async def seed() -> None:
    if engine.dialect.name != "mysql":
        raise RuntimeError("This local demo seed requires MySQL.")
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    try:
        async with SessionLocal() as db:
            cluster = await db.scalar(select(KubernetesClusterConfig).where(KubernetesClusterConfig.name == CLUSTER_NAME))
            if cluster is None:
                cluster = KubernetesClusterConfig(
                    name=CLUSTER_NAME, description="Development-only NPU queue demo data",
                    kubeconfig_encrypted="local-demo", namespaces="local-demo",
                    npu_resource_name="huawei.com/Ascend910", enabled=True, display_order=9_999,
                )
                db.add(cluster)
                await db.flush()

            # This cluster is created and exclusively owned by this script.
            await db.execute(delete(ResourceNodeMetrics).where(ResourceNodeMetrics.cluster_id == cluster.id))
            await db.execute(delete(ResourceNpuMetrics).where(ResourceNpuMetrics.cluster_id == cluster.id))
            pods = [_pending_pod(index, now) for index in range(10)]
            db.add(ResourceNpuMetrics(
                cluster_id=cluster.id, cluster_name=CLUSTER_NAME, npu_total=24, npu_used=18,
                npu_available=6, npu_utilization=75, executing_pods_count=6, pr_count=10,
                top_pods_json=pods, pod_timeline_json=pods, collected_at=now,
            ))
            for name, total, used in (("demo-910b-01", 8, 8), ("demo-910b-02", 8, 6), ("demo-910b-03", 8, 4)):
                db.add(ResourceNodeMetrics(
                    cluster_id=cluster.id, cluster_name=CLUSTER_NAME, node_name=name,
                    cpu_cores_total=96, cpu_cores_used=48, cpu_cores_available=48,
                    memory_bytes_total=512 * 1024**3, memory_bytes_used=256 * 1024**3,
                    memory_bytes_available=256 * 1024**3, npu_total=total, npu_used=used,
                    npu_available=total - used, npu_utilization=used / total * 100,
                    executing_pods_count=2, collected_at=now,
                ))
            await db.commit()
    finally:
        await engine.dispose()
    print(f"Seeded 10 Pending Pods in {CLUSTER_NAME} at {now.isoformat()}.")


if __name__ == "__main__":
    asyncio.run(seed())
