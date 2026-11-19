"""Tests for ResourceMetricsService node-level metrics and cleanup.

Covers issue #150: CI 资源监控看板新增机器维度 NPU 利用率趋势视图.
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

backend_dir = str(Path(__file__).resolve().parents[2] / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from tests.mysql_test_db import create_test_engine, reset_tables  # noqa: E402

from collector.resource_metrics import ResourceMetricsCollector  # noqa: E402
from contracts.schemas.resource_metrics import RESOURCE_METRICS_CONFIG_KEY  # noqa: E402
from infrastructure.persistence.models import (  # noqa: E402
    KubernetesClusterConfig,
    ProjectDashboardConfig,
    ResourceNodeMetrics,
    ResourceNpuMetrics,
)
from infrastructure.persistence.read_models.resource_metrics import (  # noqa: E402
    ResourceMetricsQueryService,  # noqa: E402
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session():
    """Create an isolated MySQL database with resource metrics tables."""
    engine = create_test_engine()
    await reset_tables(engine, [
        KubernetesClusterConfig.__table__, ResourceNpuMetrics.__table__,
        ResourceNodeMetrics.__table__, ProjectDashboardConfig.__table__,
    ])

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            ProjectDashboardConfig(
                config_key=RESOURCE_METRICS_CONFIG_KEY,
                config_value={"interval_minutes": 1, "retention_days": 30},
                description="NPU 指标采集配置",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


def _make_cluster(name: str = "A2 资源池", display_order: int = 0, enabled: bool = True) -> KubernetesClusterConfig:
    return KubernetesClusterConfig(
        name=name,
        kubeconfig_encrypted="dummy",
        namespaces="vllm-project",
        enabled=enabled,
        display_order=display_order,
    )


def _make_node_metric(
    cluster_id: int,
    cluster_name: str,
    node_name: str,
    npu_util: float,
    npu_total: float = 8,
    cpu_util: float = 50.0,
    mem_util: float = 50.0,
    pods: int = 1,
    collected_at: datetime | None = None,
) -> ResourceNodeMetrics:
    return ResourceNodeMetrics(
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        node_name=node_name,
        cpu_cores_total=64,
        cpu_cores_used=32,
        cpu_cores_available=32,
        cpu_utilization=cpu_util,
        memory_bytes_total=256 * 1024**3,
        memory_bytes_used=128 * 1024**3,
        memory_bytes_available=128 * 1024**3,
        memory_utilization=mem_util,
        npu_total=npu_total,
        npu_used=npu_util / 100 * npu_total,
        npu_available=npu_total - npu_util / 100 * npu_total,
        npu_utilization=npu_util,
        executing_pods_count=pods,
        collected_at=collected_at or datetime.now(UTC),
    )


async def test_query_node_metrics_basic(db_session: AsyncSession):
    """返回结构正确，clusters → nodes → metrics 嵌套层级正确."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    c2 = _make_cluster("A3 资源池", display_order=1)
    db_session.add_all([c1, c2])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 70.0, collected_at=now),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-02", 30.0, collected_at=now),
        _make_node_metric(c2.id, "A3 资源池", "node-a3-01", 60.0, collected_at=now),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(
        time_range="1h",
        start_time=now - timedelta(minutes=1),
        end_time=now + timedelta(minutes=1),
    )

    clusters = result["clusters"]
    assert len(clusters) == 2
    assert clusters[0]["cluster_name"] == "A2 资源池"
    assert len(clusters[0]["nodes"]) == 2
    assert clusters[0]["nodes"][0]["node_name"] == "node-a2-01"
    assert len(clusters[0]["nodes"][0]["metrics"]) == 1
    assert clusters[0]["nodes"][0]["metrics"][0]["npu_utilization"] == 70.0
    assert clusters[1]["cluster_name"] == "A3 资源池"
    assert len(clusters[1]["nodes"]) == 1


async def test_query_node_metrics_filter_cluster(db_session: AsyncSession):
    """cluster_ids 过滤生效."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    c2 = _make_cluster("A3 资源池", display_order=1)
    db_session.add_all([c1, c2])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 70.0, collected_at=now),
        _make_node_metric(c2.id, "A3 资源池", "node-a3-01", 60.0, collected_at=now),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(
        cluster_ids=[c2.id],
        time_range="1h",
        start_time=now - timedelta(minutes=1),
        end_time=now + timedelta(minutes=1),
    )

    clusters = result["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["cluster_id"] == c2.id
    assert clusters[0]["cluster_name"] == "A3 资源池"


async def test_query_node_metrics_filter_node_names(db_session: AsyncSession):
    """node_names 过滤生效."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    db_session.add(c1)
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 70.0, collected_at=now),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-02", 30.0, collected_at=now),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(
        node_names=["node-a2-01"],
        time_range="1h",
        start_time=now - timedelta(minutes=1),
        end_time=now + timedelta(minutes=1),
    )

    clusters = result["clusters"]
    assert len(clusters) == 1
    nodes = clusters[0]["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_name"] == "node-a2-01"


async def test_query_node_metrics_time_range(db_session: AsyncSession):
    """不同 time_range 降采样粒度正确（1h 原始 / 24h 5min）."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    db_session.add(c1)
    await db_session.flush()

    # Use timestamps in the past to avoid being filtered out by end_time = now()
    base = (datetime.now(UTC) - timedelta(minutes=10)).replace(second=0, microsecond=0)
    # Align to a 5-minute boundary so all 3 points fall in the same 5-minute bucket
    base = base - timedelta(minutes=base.minute % 5)
    # 3 points within the same 5-minute bucket
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 40.0, collected_at=base),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 50.0, collected_at=base + timedelta(minutes=1)),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 60.0, collected_at=base + timedelta(minutes=2)),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)

    raw = await service.query_node_metrics(time_range="1h")
    assert len(raw["clusters"][0]["nodes"][0]["metrics"]) == 3

    aggregated = await service.query_node_metrics(time_range="24h")
    assert len(aggregated["clusters"][0]["nodes"][0]["metrics"]) == 1


async def test_query_node_metrics_aggregation(db_session: AsyncSession):
    """聚合后利用率取均值、总量取末值."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    db_session.add(c1)
    await db_session.flush()

    base = (datetime.now(UTC) - timedelta(minutes=10)).replace(second=0, microsecond=0)
    # Keep both samples in one deterministic 5-minute bucket. Without alignment,
    # a run at minute xx:x4 crosses a bucket boundary and makes this test flaky.
    base = base - timedelta(minutes=base.minute % 5)
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 40.0, npu_total=8, collected_at=base),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 60.0, npu_total=10, collected_at=base + timedelta(minutes=1)),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(time_range="24h")

    point = result["clusters"][0]["nodes"][0]["metrics"][0]
    assert point["npu_utilization"] == 50.0  # avg(40, 60)
    assert point["npu_total"] == 10  # last value
    assert point["cpu_utilization"] == 50.0  # avg(50, 50)


async def test_query_node_metrics_empty(db_session: AsyncSession):
    """无数据时返回空数组."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    db_session.add(c1)
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(time_range="24h")

    assert len(result["clusters"]) == 1
    assert result["clusters"][0]["nodes"] == []


async def test_query_node_metrics_disabled_cluster_excluded(db_session: AsyncSession):
    """未启用的集群不返回."""
    c1 = _make_cluster("A2 资源池", display_order=0, enabled=True)
    c2 = _make_cluster("A3 资源池", display_order=1, enabled=False)
    db_session.add_all([c1, c2])
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all([
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 70.0, collected_at=now),
        _make_node_metric(c2.id, "A3 资源池", "node-a3-01", 60.0, collected_at=now),
    ])
    await db_session.commit()

    service = ResourceMetricsQueryService(db_session)
    result = await service.query_node_metrics(
        time_range="1h",
        start_time=now - timedelta(minutes=1),
        end_time=now + timedelta(minutes=1),
    )

    clusters = result["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["cluster_id"] == c1.id


async def test_cleanup_old_metrics_node(db_session: AsyncSession):
    """清理任务同时删除集群级和节点级过期数据."""
    c1 = _make_cluster("A2 资源池", display_order=0)
    db_session.add(c1)
    await db_session.flush()

    now = datetime.now(UTC)
    old = now - timedelta(days=31)
    recent = now - timedelta(days=1)

    db_session.add_all([
        ResourceNpuMetrics(
            cluster_id=c1.id, cluster_name="A2 资源池",
            npu_total=8, npu_used=4, npu_available=4, npu_utilization=50.0,
            executing_pods_count=1, pr_count=0, collected_at=old,
        ),
        ResourceNpuMetrics(
            cluster_id=c1.id, cluster_name="A2 资源池",
            npu_total=8, npu_used=4, npu_available=4, npu_utilization=50.0,
            executing_pods_count=1, pr_count=0, collected_at=recent,
        ),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 50.0, collected_at=old),
        _make_node_metric(c1.id, "A2 资源池", "node-a2-01", 50.0, collected_at=recent),
    ])
    await db_session.commit()

    service = ResourceMetricsCollector(db_session)
    deleted = await service.cleanup_old_metrics()

    assert deleted == 2  # 1 cluster-level + 1 node-level

    from sqlalchemy import select

    remaining_npu = (await db_session.execute(select(ResourceNpuMetrics))).scalars().all()
    remaining_node = (await db_session.execute(select(ResourceNodeMetrics))).scalars().all()
    assert len(remaining_npu) == 1
    assert len(remaining_node) == 1
