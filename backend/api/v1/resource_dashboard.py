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
)
from infrastructure.clients.kubernetes_client import encrypt_kubeconfig
from infrastructure.core.config import settings
from infrastructure.persistence.models import KubernetesClusterConfig
from npu_queue.replay import load_dashboard_replay
from resource_dashboard.metrics_query import PersistedResourceMetricsService
from resource_dashboard.service import ResourceDashboardService

router = APIRouter()


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
    return await service.build_dashboard(
        clusters,
        label_selector=label_selector,
        include_pods=include_pods,
    )


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
    summary, _, _ = await service.build_cluster_summary(cluster, label_selector, include_pods)
    return summary


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
