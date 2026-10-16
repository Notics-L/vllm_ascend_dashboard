"""评分权重版本管理服务"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, func

from contributor_board.scoring_engine import calculate_contribution_score
from contributor_board.quadrant_engine import determine_quadrant
from contributor_board.scoring_version_schemas import (
    ScoreSnapshotResponse,
    ScoringVersionCreateRequest,
    ScoringVersionResponse,
    VersionCompareResponse,
    ScoreUpdateAuditResponse,
    CalculateAutoWeightsResponse,
)
from infrastructure.persistence.models import (
    Contributor,
    CommitClassification,
    ContributorMetrics,
    ContributorScoringVersion,
    ContributorScoreSnapshot,
    ContributorScoreUpdateAudit,
)

logger = logging.getLogger(__name__)


class ScoringVersionService:
    @staticmethod
    async def list_versions(
        db,
        repo_name: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        stmt = select(ContributorScoringVersion)
        if repo_name:
            stmt = stmt.where(ContributorScoringVersion.repo_name == repo_name)
        if status:
            stmt = stmt.where(ContributorScoringVersion.status == status)
        stmt = stmt.order_by(ContributorScoringVersion.created_at.desc())
        stmt = stmt.offset(skip).limit(limit)

        result = await db.execute(stmt)
        versions = result.scalars().all()

        count_stmt = select(func.count(ContributorScoringVersion.id))
        if repo_name:
            count_stmt = count_stmt.where(ContributorScoringVersion.repo_name == repo_name)
        if status:
            count_stmt = count_stmt.where(ContributorScoringVersion.status == status)
        total = (await db.execute(count_stmt)).scalar_one()

        return {
            "versions": [ScoringVersionResponse.model_validate(v) for v in versions],
            "total": total,
        }

    @staticmethod
    async def get_active_version(db, repo_name: str | None = None) -> ScoringVersionResponse | None:
        stmt = select(ContributorScoringVersion).where(
            ContributorScoringVersion.status == "active"
        ).order_by(ContributorScoringVersion.created_at.desc())
        if repo_name:
            stmt = stmt.where(ContributorScoringVersion.repo_name == repo_name)
        result = await db.execute(stmt)
        v = result.scalars().first()
        if v:
            return ScoringVersionResponse.model_validate(v)
        return None

    @staticmethod
    async def get_version(db, version_id: int) -> ScoringVersionResponse | None:
        v = await db.get(ContributorScoringVersion, version_id)
        if v:
            return ScoringVersionResponse.model_validate(v)
        return None

    @staticmethod
    async def create_version(
        db,
        request: ScoringVersionCreateRequest,
        created_by: str | None = None,
    ) -> ScoringVersionResponse:
        repo_name = request.repo_name

        next_version = await ScoringVersionService._next_version_number(db, repo_name)

        version = ContributorScoringVersion(
            version=next_version,
            repo_name=repo_name,
            weights=request.weights,
            method=request.method,
            status="active" if request.apply_immediately else "draft",
            data_summary=request.data_summary,
            confidence_score=request.confidence_score,
            ai_prompt=request.ai_prompt,
            ai_response=request.ai_response,
            ai_model=request.ai_model,
            description=request.description,
            created_by=created_by,
        )
        db.add(version)

        active_stmt = select(ContributorScoringVersion).where(
            ContributorScoringVersion.status == "active",
        )
        if repo_name:
            active_stmt = active_stmt.where(ContributorScoringVersion.repo_name == repo_name)
        else:
            active_stmt = active_stmt.where(ContributorScoringVersion.repo_name.is_(None))
        active = await db.execute(active_stmt)
        currents = active.scalars().all()

        for current in currents:
            current.status = "archived"

        if not currents and not request.apply_immediately:
            logger.warning("No active version exists — auto-setting first version to active")
            version.status = "active"

        if version.status == "active":
            version.activated_at = datetime.now(UTC)
            version.activated_by = created_by

        await db.flush()

        if request.apply_immediately or version.status == "active":
            snapshot_id = await ScoringVersionService._create_snapshot_and_recalculate(
                db, version, repo_name, created_by
            )
        else:
            snapshot_id = None

        ScoringVersionService._create_audit(
            db, "create_version", version.id, snapshot_id, repo_name,
            {"method": request.method, "description": request.description},
            f"Create {version.version} ({request.method})", created_by,
        )

        await db.commit()
        return ScoringVersionResponse.model_validate(version)

    @staticmethod
    async def apply_version(
        db,
        version_id: int,
        created_by: str | None = None,
    ) -> ScoringVersionResponse:
        version = await db.get(ContributorScoringVersion, version_id)
        if not version:
            raise ValueError(f"Scoring version {version_id} not found")

        active_stmt = select(ContributorScoringVersion).where(
            ContributorScoringVersion.status == "active",
        )
        if version.repo_name:
            active_stmt = active_stmt.where(ContributorScoringVersion.repo_name == version.repo_name)
        else:
            active_stmt = active_stmt.where(ContributorScoringVersion.repo_name.is_(None))
        currents = await db.execute(active_stmt)
        currents = currents.scalars().all()

        # Weight diff against the latest active version
        latest_current = currents[-1] if currents else None
        weight_diff = ScoringVersionService._compute_weight_diff(latest_current.weights if latest_current else {}, version.weights)
        max_shift = ScoringVersionService._max_weight_shift(weight_diff)

        if max_shift > 30:
            logger.warning(
                "Weight shift warning for version %s: max shift %.1f%% > 30%% threshold",
                version.version, max_shift,
            )

        for cur in currents:
            cur.status = "archived"

        version.status = "active"
        version.activated_at = datetime.now(UTC)
        version.activated_by = created_by

        snapshot_id = await ScoringVersionService._create_snapshot_and_recalculate(
            db, version, version.repo_name, created_by
        )

        ScoringVersionService._create_audit(
            db, "apply_version", version.id, snapshot_id, version.repo_name,
            {"weight_diff": weight_diff, "max_weight_shift": max_shift},
            f"Apply {version.version}, max weight shift: {max_shift:.1f}%", created_by,
            weight_diff, max_shift,
        )

        await db.commit()
        return ScoringVersionResponse.model_validate(version)

    @staticmethod
    async def rollback_to_version(
        db,
        version_id: int,
        created_by: str | None = None,
    ) -> ScoringVersionResponse:
        return await ScoringVersionService.apply_version(db, version_id, created_by)

    @staticmethod
    async def compare_versions(
        db,
        version_id_a: int,
        version_id_b: int,
    ) -> VersionCompareResponse:
        va = await db.get(ContributorScoringVersion, version_id_a)
        vb = await db.get(ContributorScoringVersion, version_id_b)
        if not va or not vb:
            raise ValueError("One or both versions not found")

        weight_diff = ScoringVersionService._compute_weight_diff(va.weights, vb.weights)

        score_impact = None
        result = await db.execute(select(ContributorMetrics).where(
            ContributorMetrics.repo_name == va.repo_name
        ))
        metrics_list = result.scalars().all()
        if metrics_list:
            quadrant_changes = []
            for m in metrics_list:
                old_score = calculate_contribution_score(
                    {k: getattr(m, k, 0) for k in va.weights}, va.weights
                )
                new_score = calculate_contribution_score(
                    {k: getattr(m, k, 0) for k in vb.weights}, vb.weights
                )
                old_q = determine_quadrant(old_score, m.error_score or 0, 30, 2)
                new_q = determine_quadrant(new_score, m.error_score or 0, 30, 2)
                if old_q != new_q:
                    quadrant_changes.append({
                        "login": m.contributor_login,
                        "from": old_q,
                        "to": new_q,
                        "old_score": old_score,
                        "new_score": new_score,
                    })
            score_impact = {
                "contributor_count": len(metrics_list),
                "quadrant_changes": quadrant_changes,
            }

        return VersionCompareResponse(
            version_a=ScoringVersionResponse.model_validate(va),
            version_b=ScoringVersionResponse.model_validate(vb),
            weight_diff=weight_diff,
            score_impact=score_impact,
        )

    @staticmethod
    async def list_snapshots(
        db,
        repo_name: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        stmt = select(ContributorScoreSnapshot).order_by(ContributorScoreSnapshot.snapshot_date.desc())
        if repo_name:
            stmt = stmt.where(ContributorScoreSnapshot.repo_name == repo_name)
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        snapshots = result.scalars().all()

        count_stmt = select(func.count(ContributorScoreSnapshot.id))
        if repo_name:
            count_stmt = count_stmt.where(ContributorScoreSnapshot.repo_name == repo_name)
        total = (await db.execute(count_stmt)).scalar_one()

        return {
            "snapshots": [ScoreSnapshotResponse.model_validate(s) for s in snapshots],
            "total": total,
        }

    @staticmethod
    async def get_snapshot(db, snapshot_id: int) -> ScoreSnapshotResponse | None:
        s = await db.get(ContributorScoreSnapshot, snapshot_id)
        if s:
            return ScoreSnapshotResponse.model_validate(s)
        return None

    @staticmethod
    async def generate_snapshot(
        db,
        version_id: int,
        created_by: str | None = None,
    ) -> ScoreSnapshotResponse | None:
        """Generate a new score snapshot for the given version."""
        version = await db.get(ContributorScoringVersion, version_id)
        if not version:
            return None
        snapshot_id = await ScoringVersionService._create_snapshot_and_recalculate(
            db, version, version.repo_name, created_by
        )
        ScoringVersionService._create_audit(
            db, "generate_snapshot", version_id, snapshot_id, version.repo_name,
            {}, f"Generate score snapshot for {version.version}", created_by,
        )
        await db.commit()
        if snapshot_id:
            return await ScoringVersionService.get_snapshot(db, snapshot_id)
        return None

    @staticmethod
    async def list_audit_logs(
        db,
        repo_name: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        stmt = select(ContributorScoreUpdateAudit).order_by(ContributorScoreUpdateAudit.created_at.desc())
        if repo_name:
            stmt = stmt.where(ContributorScoreUpdateAudit.repo_name == repo_name)
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        audits = result.scalars().all()

        count_stmt = select(func.count(ContributorScoreUpdateAudit.id))
        if repo_name:
            count_stmt = count_stmt.where(ContributorScoreUpdateAudit.repo_name == repo_name)
        total = (await db.execute(count_stmt)).scalar_one()

        return {
            "audits": [ScoreUpdateAuditResponse.model_validate(a) for a in audits],
            "total": total,
        }

    # ---- Private helpers ----

    @staticmethod
    async def _next_version_number(db, repo_name: str | None) -> str:
        stmt = select(ContributorScoringVersion)
        if repo_name:
            stmt = stmt.where(ContributorScoringVersion.repo_name == repo_name)
        stmt = stmt.order_by(ContributorScoringVersion.created_at.desc()).limit(1)
        result = await db.execute(stmt)
        last = result.scalar_one_or_none()

        if not last:
            return "v1.0.0"

        try:
            parts = last.version.lstrip("v").split(".")
            major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            return f"v{major}.{minor + 1}.0"
        except (ValueError, IndexError):
            return "v1.1.0"

    @staticmethod
    async def _create_snapshot_and_recalculate(
        db,
        version: ContributorScoringVersion,
        repo_name: str | None,
        created_by: str | None,
    ) -> int | None:
        from contributor_board.scoring_engine import recalculate_all_scores

        weights = version.weights
        # Always recalculate across all repos for a complete snapshot
        count = await recalculate_all_scores(db, weights, None)

        # Read ALL metrics (across all repos) after recalculation
        stmt = select(ContributorMetrics)
        result = await db.execute(stmt)
        metrics_list = result.scalars().all()

        # Aggregate scores by contributor_login across all repos
        aggregated: dict[str, dict[str, Any]] = {}
        for m in metrics_list:
            login = m.contributor_login
            if login not in aggregated:
                aggregated[login] = {
                    "login": login,
                    "score": 0.0,
                    "error_score": 0,
                    "quadrant": m.quadrant or "unknown",
                }
                continue
            aggregated[login]["score"] += m.contribution_score or 0
            aggregated[login]["error_score"] += m.error_score or 0
            # Keep the highest quadrant (INCENTIVE > IMPROVE > ENCOURAGE > REPLACE)
            q = m.quadrant or "unknown"
            q_rank = {"INCENTIVE": 4, "IMPROVE": 3, "ENCOURAGE": 2, "REPLACE": 1, "unknown": 0}.get(q, 0)
            cur_rank = {"INCENTIVE": 4, "IMPROVE": 3, "ENCOURAGE": 2, "REPLACE": 1, "unknown": 0}.get(aggregated[login]["quadrant"], 0)
            if q_rank > cur_rank:
                aggregated[login]["quadrant"] = q

        scores = list(aggregated.values())
        quadrant_dist = {}
        total_score = 0.0
        for s in scores:
            total_score += s["score"]
            q = s["quadrant"]
            quadrant_dist[q] = quadrant_dist.get(q, 0) + 1

        mean_score = round(total_score / len(scores), 2) if scores else 0
        variance = sum((s["score"] - mean_score) ** 2 for s in scores) / len(scores) if scores else 0
        import math
        std_score = round(math.sqrt(variance), 2)

        statistics = {
            "score_mean": mean_score,
            "score_std": std_score,
            "quadrant_distribution": quadrant_dist,
        }

        snapshot = ContributorScoreSnapshot(
            version_id=version.id,
            repo_name=repo_name,
            snapshot_date=date.today(),
            contributor_count=len(scores),
            statistics=statistics,
            scores=scores,
            created_by=created_by,
            description=f"重算完成，更新 {len(scores)} 位贡献者分数",
        )
        db.add(snapshot)
        await db.flush()
        return snapshot.id

    @staticmethod
    def _compute_weight_diff(old_w: dict, new_w: dict) -> dict:
        all_keys = set(list(old_w.keys()) + list(new_w.keys()))
        diff = {}
        for k in all_keys:
            o = float(old_w.get(k, 0))
            n = float(new_w.get(k, 0))
            change = round(n - o, 4)
            change_pct = round(change / o * 100, 2) if o != 0 else 0
            diff[k] = {"from": o, "to": n, "change": change, "change_pct": change_pct}
        return diff

    @staticmethod
    def _max_weight_shift(weight_diff: dict) -> float:
        if not weight_diff:
            return 0.0
        return max(abs(d["change_pct"]) for d in weight_diff.values())

    @staticmethod
    def _create_audit(
        db,
        action: str,
        version_id: int,
        snapshot_id: int | None,
        repo_name: str | None,
        details: dict,
        summary: str,
        created_by: str | None,
        weight_diff: dict | None = None,
        max_weight_shift: float | None = None,
    ):
        audit = ContributorScoreUpdateAudit(
            action=action,
            version_id=version_id,
            snapshot_id=snapshot_id,
            repo_name=repo_name,
            details=details,
            summary=summary,
            weight_diff=weight_diff,
            max_weight_shift=max_weight_shift,
            created_by=created_by,
        )
        db.add(audit)
