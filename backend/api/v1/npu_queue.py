from fastapi import APIRouter
from sqlalchemy import func, select

from api.deps import CurrentUser, DbSession
from infrastructure.persistence.models import CIJob, CINpuJobFact, KubernetesClusterConfig
from npu_queue.replay import load_dashboard_replay
from npu_queue.service import build_dashboard

router = APIRouter()


@router.get("/dashboard")
async def get_npu_queue_dashboard(db: DbSession, current_user: CurrentUser):
    """Real CI queue read model; unknown mappings are excluded, never zeroed."""
    replay = load_dashboard_replay()
    if replay:
        return replay["queueDashboard"]
    return await build_dashboard(db)


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
