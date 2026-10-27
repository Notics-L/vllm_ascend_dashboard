from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.deps import CurrentAdminUser, CurrentUser, DbSession
from contracts.schemas import (
    ClusterResourceSummary,
    KubernetesClusterCreate,
    KubernetesClusterResponse,
    KubernetesClusterTestResponse,
    KubernetesClusterUpdate,
    Message,
    ResourceDashboardResponse,
    PodSchedulingDiagnosticsResponse,
    ResourcePodTimelineResponse,
)
from infrastructure.clients.kubernetes_client import encrypt_kubeconfig
from infrastructure.core.config import settings
from infrastructure.persistence.models import KubernetesClusterConfig
from npu_queue.replay import load_dashboard_replay
from resource_dashboard.metrics_query import PersistedResourceMetricsService
from resource_dashboard.node_utilization import query_node_utilization_heatmap
from resource_dashboard.service import ResourceDashboardService

router = APIRouter()
_HEATMAP_CACHE_TTL_SECONDS = 60
_heatmap_cache: dict[tuple[str | None, datetime, datetime, int], tuple[float, dict]] = {}


@router.get("/node-utilization-heatmap")
async def get_node_utilization_heatmap(
    db: DbSession,
    current_user: CurrentUser,
    pool: str | None = None,
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    bucket_hours: int = Query(24, ge=1, le=168),
):
    # The default range is minute-aligned so adjacent UI requests can share a
    # short-lived cached aggregation instead of repeating a seven-day scan.
    end = end or datetime.now(UTC).replace(second=0, microsecond=0)
    if start is None:
        local_end = end.astimezone(UTC) + timedelta(hours=8)
        local_start = local_end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        start = local_start - timedelta(hours=8)
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail="start and end must include a timezone")
    if start >= end:
        raise HTTPException(status_code=422, detail="start must be earlier than end")
    if end - start > timedelta(days=31):
        raise HTTPException(status_code=422, detail="time window cannot exceed 31 days")
    cache_key = (pool, start, end, bucket_hours)
    cached = _heatmap_cache.get(cache_key)
    if cached and monotonic() - cached[0] < _HEATMAP_CACHE_TTL_SECONDS:
        return cached[1]
    result = await query_node_utilization_heatmap(
        db,
        start=start,
        end=end,
        pool=pool,
        bucket_hours=bucket_hours,
    )
    _heatmap_cache[cache_key] = (monotonic(), result)
    return result


def _cluster_response(cluster: KubernetesClusterConfig) -> KubernetesClusterResponse:
    response = KubernetesClusterResponse.model_validate(cluster)
    response.kubeconfig_configured = bool(cluster.kubeconfig_encrypted)
    return response


async def _get_cluster(db: DbSession, cluster_id: int) -> KubernetesClusterConfig:
    stmt = select(KubernetesClusterConfig).where(KubernetesClusterConfig.id == cluster_id)
    result = await db.execute(stmt)
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群配置不存在")
    return cluster


@router.get("/clusters/enabled", response_model=list[KubernetesClusterResponse])
async def list_enabled_clusters(db: DbSession, current_user: CurrentUser):
    replay = load_dashboard_replay()
    if replay:
        return [cluster for cluster in replay["clusterConfig"] if cluster.get("enabled")]
    stmt = (
        select(KubernetesClusterConfig)
        .where(KubernetesClusterConfig.enabled.is_(True))
        .order_by(KubernetesClusterConfig.display_order.asc(), KubernetesClusterConfig.name.asc())
    )
    result = await db.execute(stmt)
    return [_cluster_response(cluster) for cluster in result.scalars().all()]


@router.get("/summary", response_model=ResourceDashboardResponse)
async def get_resource_dashboard(
    db: DbSession,
    current_user: CurrentUser,
    cluster_ids: Annotated[list[int] | None, Query()] = None,
    label_selector: str | None = None,
    include_pods: bool = True,
):
    replay = load_dashboard_replay()
    if replay:
        return replay["resourceSummary"]

    stmt = select(KubernetesClusterConfig).where(KubernetesClusterConfig.enabled.is_(True))
    if cluster_ids:
        stmt = stmt.where(KubernetesClusterConfig.id.in_(cluster_ids))
    stmt = stmt.order_by(KubernetesClusterConfig.display_order.asc(), KubernetesClusterConfig.name.asc())
    result = await db.execute(stmt)
    clusters = list(result.scalars().all())

    if settings.RESOURCE_METRICS_REMOTE_URL:
        return await PersistedResourceMetricsService().build_dashboard(
            db,
            clusters,
            cluster_ids=cluster_ids,
            include_pods=include_pods,
        )

    service = ResourceDashboardService()
    dashboard = await service.build_dashboard(
        clusters,
        label_selector=label_selector,
        include_pods=include_pods,
    )
    await PersistedResourceMetricsService().enrich_pods_from_history(
        db, dashboard.executing_pods,
    )
    return dashboard


@router.get("/pod-timeline", response_model=ResourcePodTimelineResponse)
async def get_pod_timeline(
    db: DbSession,
    current_user: CurrentUser,
    cluster_ids: Annotated[list[int] | None, Query()] = None,
    hours: int = Query(24, ge=1, le=48),
):
    """Return all Pod lifecycles captured during the requested window."""
    timeline = await PersistedResourceMetricsService().build_pod_timeline(
        db, cluster_ids=cluster_ids, hours=hours,
    )
    # A development instance may not have collected its first lifecycle
    # snapshot yet. Keep it useful immediately, then automatically switch to
    # the complete historical view after the collector has run.
    if timeline.pods or settings.RESOURCE_METRICS_REMOTE_URL:
        return timeline

    if not settings.RESOURCE_METRICS_REMOTE_URL:
        clusters_stmt = select(KubernetesClusterConfig).where(KubernetesClusterConfig.enabled.is_(True))
        if cluster_ids:
            clusters_stmt = clusters_stmt.where(KubernetesClusterConfig.id.in_(cluster_ids))
        clusters = list((await db.execute(clusters_stmt)).scalars().all())
        dashboard = await ResourceDashboardService().build_dashboard(clusters, include_pods=True)
        return ResourcePodTimelineResponse(
            generated_at=datetime.now(UTC),
            window_start=datetime.now(UTC) - timedelta(hours=hours),
            pods=[*dashboard.executing_pods, *dashboard.executed_pods],
        )
    return timeline


@router.get("/cluster/{cluster_id}/summary", response_model=ClusterResourceSummary)
async def get_cluster_summary(
    db: DbSession,
    current_user: CurrentUser,
    cluster_id: int,
    label_selector: str | None = None,
    include_pods: bool = True,
):
    stmt = select(KubernetesClusterConfig).where(
        KubernetesClusterConfig.id == cluster_id,
        KubernetesClusterConfig.enabled.is_(True),
    )
    result = await db.execute(stmt)
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在或未启用")

    if settings.RESOURCE_METRICS_REMOTE_URL:
        return await PersistedResourceMetricsService().build_cluster_summary(
            db,
            cluster,
            include_pods=include_pods,
        )

    service = ResourceDashboardService()
    # Individual cards must degrade like the aggregate dashboard: a stale
    # kubeconfig (for example cluster 3 returning 401) is displayable state,
    # not an unhandled 500 that repeatedly surfaces as a page-level popup.
    summary, _, _ = await service._safe_build_cluster_summary(cluster, label_selector, include_pods)
    return summary


@router.get(
    "/cluster/{cluster_id}/pods/{namespace}/{name}/scheduling",
    response_model=PodSchedulingDiagnosticsResponse,
)
async def get_pod_scheduling_diagnostics(
    db: DbSession,
    current_user: CurrentUser,
    cluster_id: int,
    namespace: str,
    name: str,
):
    """Expose a sanitized, read-only live Pod scheduling view to authenticated users."""
    cluster = await db.scalar(select(KubernetesClusterConfig).where(
        KubernetesClusterConfig.id == cluster_id,
        KubernetesClusterConfig.enabled.is_(True),
    ))
    if not cluster:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="集群不存在或未启用")
    if settings.RESOURCE_METRICS_REMOTE_URL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="实时 Kubernetes 凭据由远端资源采集服务持有；请在该服务部署同一接口。",
        )
    try:
        return await ResourceDashboardService().pod_scheduling_diagnostics(
            cluster, namespace=namespace, name=name,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc


@router.get("/clusters", response_model=list[KubernetesClusterResponse])
async def list_clusters(db: DbSession, current_user: CurrentAdminUser):
    replay = load_dashboard_replay()
    if replay:
        return replay["clusterConfig"]
    stmt = select(KubernetesClusterConfig).order_by(
        KubernetesClusterConfig.display_order.asc(),
        KubernetesClusterConfig.name.asc(),
    )
    result = await db.execute(stmt)
    return [_cluster_response(cluster) for cluster in result.scalars().all()]


@router.post("/clusters", response_model=KubernetesClusterResponse, status_code=status.HTTP_201_CREATED)
async def create_cluster(
    db: DbSession,
    cluster_data: KubernetesClusterCreate,
    current_user: CurrentAdminUser,
):
    existing = await db.execute(
        select(KubernetesClusterConfig).where(KubernetesClusterConfig.name == cluster_data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="集群名称已存在")

    data = cluster_data.model_dump(exclude={"kubeconfig"})
    cluster = KubernetesClusterConfig(
        **data,
        kubeconfig_encrypted=encrypt_kubeconfig(cluster_data.kubeconfig),
        created_by=current_user.id,
    )
    db.add(cluster)
    await db.commit()
    await db.refresh(cluster)
    return _cluster_response(cluster)


@router.get("/clusters/{cluster_id}", response_model=KubernetesClusterResponse)
async def get_cluster(db: DbSession, cluster_id: int, current_user: CurrentAdminUser):
    cluster = await _get_cluster(db, cluster_id)
    return _cluster_response(cluster)


@router.put("/clusters/{cluster_id}", response_model=KubernetesClusterResponse)
async def update_cluster(
    db: DbSession,
    cluster_id: int,
    cluster_data: KubernetesClusterUpdate,
    current_user: CurrentAdminUser,
):
    cluster = await _get_cluster(db, cluster_id)
    update_data = cluster_data.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] != cluster.name:
        existing = await db.execute(
            select(KubernetesClusterConfig).where(KubernetesClusterConfig.name == update_data["name"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="集群名称已存在")

    kubeconfig = update_data.pop("kubeconfig", None)
    if kubeconfig:
        cluster.kubeconfig_encrypted = encrypt_kubeconfig(kubeconfig)

    for field, value in update_data.items():
        setattr(cluster, field, value)

    await db.commit()
    await db.refresh(cluster)
    return _cluster_response(cluster)


@router.delete("/clusters/{cluster_id}", response_model=Message)
async def delete_cluster(db: DbSession, cluster_id: int, current_user: CurrentAdminUser):
    cluster = await _get_cluster(db, cluster_id)
    await db.delete(cluster)
    await db.commit()
    return {"message": "集群配置已删除"}


@router.post("/clusters/{cluster_id}/test", response_model=KubernetesClusterTestResponse)
async def test_cluster(db: DbSession, cluster_id: int, current_user: CurrentAdminUser):
    cluster = await _get_cluster(db, cluster_id)
    service = ResourceDashboardService()
    try:
        node_count, pod_count = await service.test_cluster(cluster)
    except Exception as exc:
        return KubernetesClusterTestResponse(success=False, message=f"连接失败：{exc}")
    return KubernetesClusterTestResponse(
        success=True,
        message="连接成功",
        node_count=node_count,
        pod_count=pod_count,
    )
