from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from api.deps import CurrentUser, DbSession
from infrastructure.persistence.models import CIJob, CINpuJobFact, KubernetesClusterConfig
from npu_queue.replay import load_dashboard_replay
from npu_queue.service import build_dashboard
from npu_queue.snapshots import physical_queue_detail, physical_queue_trend

router = APIRouter()


@router.get("/dashboard")
async def get_npu_queue_dashboard(db: DbSession, current_user: CurrentUser):
    """Real CI queue read model; unknown mappings are excluded, never zeroed."""
    replay = load_dashboard_replay()
    if replay:
        return replay["queueDashboard"]
    return await build_dashboard(db)


@router.get("/trend")
async def get_npu_queue_trend(
    db: DbSession,
    current_user: CurrentUser,
    pool: str | None = None,
    hours: int = Query(24, ge=1, le=168),
):
    now = datetime.now(UTC)
    trend = await physical_queue_trend(db, now=now, hours=hours, pool=pool)
    points = []
    for captured_at, counts in trend:
        timestamp = (
            captured_at.replace(tzinfo=UTC)
            if captured_at.tzinfo is None
            else captured_at.astimezone(UTC)
        )
        points.append({"capturedAt": timestamp.isoformat(), **counts})
    return {
        "pool": pool,
        "hours": hours,
        "points": points,
        "latestCapturedAt": points[-1]["capturedAt"] if points else None,
    }


@router.get("/trend/detail")
async def get_npu_queue_trend_detail(
    db: DbSession,
    current_user: CurrentUser,
    captured_at: datetime = Query(...),
    pool: str | None = None,
):
    if captured_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="captured_at must include a timezone")
    detail = await physical_queue_detail(db, captured_at=captured_at, pool=pool)
    if detail is None:
        raise HTTPException(status_code=404, detail="no resource snapshot is available at this time")
    actual_time, counts, pods = detail
    return {
        "pool": pool,
        "capturedAt": actual_time.isoformat(),
        **counts,
        "pods": pods,
    }


@router.get("/diagnostics")
async def get_npu_queue_diagnostics(db: DbSession, current_user: CurrentUser):
    """Safe, authenticated source counts for validating an empty dashboard."""
    replay = load_dashboard_replay()
    if replay:
        return replay["queueDiagnostics"]
    clusters = list((await db.scalars(select(KubernetesClusterConfig).order_by(KubernetesClusterConfig.name))).all())
    fact_total = int((await db.scalar(select(func.count()).select_from(CINpuJobFact))) or 0)
    matched = int((await db.scalar(select(func.count()).select_from(CINpuJobFact).where(CINpuJobFact.mapping_status == "matched"))) or 0)
    return {
        "ci_jobs": int((await db.scalar(select(func.count()).select_from(CIJob))) or 0),
        "ci_npu_job_facts": {"total": fact_total, "matched": matched, "unmatched": fact_total - matched},
        "kubernetes_clusters": [{
            "name": cluster.name,
            "enabled": cluster.enabled,
            "kubeconfig_configured": bool(cluster.kubeconfig_encrypted),
            "npu_resource_name": cluster.npu_resource_name,
            "namespaces": cluster.namespaces,
        } for cluster in clusters],
    }
