"""评分权重版本管理服务"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, func

from contributor_board.scoring_engine import calculate_score
from contributor_board.quadrant_engine import determine_quadrant
from contributor_board.scoring_version_schemas import (
    ScoreSnapshotResponse,
    ScoringVersionCreateRequest,
    ScoringVersionResponse,
    VersionCompareResponse,
    ScoreUpdateAuditResponse,
    CalculateAutoWeightsResponse,
)
from contributor_board.constants import QUADRANT_RANK
from infrastructure.persistence.models import (
    ContributorProfile,
    ContributorScoringVersion,
    ContributorScoreSnapshot,
    ContributorScoreUpdateAudit,
)

logger = logging.getLogger(__name__)


def _version_filter(repo_name: str | None = None, status: str | None = None) -> list:
    conditions = []
    if repo_name:
        conditions.append(ContributorScoringVersion.repo_name == repo_name)
    if status:
        conditions.append(ContributorScoringVersion.status == status)
    return conditions


def _snapshot_filter(repo_name: str | None = None) -> list:
    if repo_name:
        return [ContributorScoreSnapshot.repo_name == repo_name]
    return []


def _audit_filter(repo_name: str | None = None) -> list:
    if repo_name:
        return [ContributorScoreUpdateAudit.repo_name == repo_name]
    return []


async def _paginated_list(
    db,
    conditions: list,
    model_class: Any,
    order_col: Any,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list, int]:
    """通用分页查询：返回 (items, total)"""
    stmt = select(model_class)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(order_col.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    items = result.scalars().all()

    count_stmt = select(func.count(model_class.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    return items, total


class ScoringVersionService:
    @staticmethod
    async def list_versions(
        db,
        repo_name: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        conditions = _version_filter(repo_name, status)
        versions, total = await _paginated_list(
            db, conditions, ContributorScoringVersion,
            ContributorScoringVersion.created_at, skip, limit,
        )
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
        snapshot_id: int | None = None

        if request.apply_immediately:
            await ScoringVersionService._archive_active_versions(db, repo_name)
            ScoringVersionService._set_version_active(version, created_by)
            await db.flush()
            snapshot_id = await ScoringVersionService._create_snapshot_and_recalculate(
                db, version, repo_name, created_by
            )
        else:
            currents = await ScoringVersionService._get_active_versions(db, repo_name)
            if not currents:
                logger.warning("No active version exists — auto-setting first version to active")
                ScoringVersionService._set_version_active(version, created_by)
                await db.flush()
                snapshot_id = await ScoringVersionService._create_snapshot_and_recalculate(
                    db, version, repo_name, created_by
                )

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

        # Weight diff against the latest active version
        currents = await ScoringVersionService._get_active_versions(db, version.repo_name)
        latest_current = currents[-1] if currents else None
        weight_diff = ScoringVersionService._compute_weight_diff(
            latest_current.weights if latest_current else {}, version.weights,
        )
        max_shift = ScoringVersionService._max_weight_shift(weight_diff)

        if max_shift > 30:
            logger.warning(
                "Weight shift warning for version %s: max shift %.1f%% > 30%% threshold",
                version.version, max_shift,
            )

        await ScoringVersionService._archive_active_versions(db, version.repo_name)
        ScoringVersionService._set_version_active(version, created_by)

        await ScoringVersionService._sync_weights_to_settings(db, version.weights)

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
        result = await db.execute(select(ContributorProfile).where(
            ContributorProfile.repo_name == va.repo_name
        ))
        profiles = result.scalars().all()
        if profiles:
            def _metrics(p):
                return {
                    "commit_count": p.commit_count or 0,
                    "code_files": p.code_files or 0,
                    "doc_files": p.doc_files or 0,
                    "pr_merged": p.pr_merged or 0,
                    "pr_open": p.pr_open or 0,
                    "pr_closed": p.pr_closed or 0,
                    "active_days_90": p.active_days_90 or 0,
                    "span_days": p.span_days or 0,
                }
            from contributor_board.scoring_engine import assign_tier
            quadrant_changes = []
            for p in profiles:
                m = _metrics(p)
                old_score = calculate_score(m, va.weights if isinstance(va.weights, dict) else None)
                new_score = calculate_score(m, vb.weights if isinstance(vb.weights, dict) else None)
                old_q = assign_tier(old_score, p.active_days_90 or 0)
                new_q = assign_tier(new_score, p.active_days_90 or 0)
                if old_q != new_q:
                    quadrant_changes.append({
                        "login": p.login,
                        "from": old_q,
                        "to": new_q,
                        "old_score": old_score,
                        "new_score": new_score,
                    })
            score_impact = {
                "contributor_count": len(profiles),
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
        conditions = _snapshot_filter(repo_name)
        snapshots, total = await _paginated_list(
            db, conditions, ContributorScoreSnapshot,
            ContributorScoreSnapshot.snapshot_date, skip, limit,
        )
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
        conditions = _audit_filter(repo_name)
        audits, total = await _paginated_list(
            db, conditions, ContributorScoreUpdateAudit,
            ContributorScoreUpdateAudit.created_at, skip, limit,
        )
        return {
            "audits": [ScoreUpdateAuditResponse.model_validate(a) for a in audits],
            "total": total,
        }

    # ---- Private helpers ----

    @staticmethod
    async def _get_active_versions(db, repo_name: str | None) -> list:
        """Query all active scoring versions for a given repo (or global)."""
        stmt = select(ContributorScoringVersion).where(
            ContributorScoringVersion.status == "active",
        )
        if repo_name:
            stmt = stmt.where(ContributorScoringVersion.repo_name == repo_name)
        else:
            stmt = stmt.where(ContributorScoringVersion.repo_name.is_(None))
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def _archive_active_versions(
        db, repo_name: str | None,
    ) -> list:
        """Find and archive all active versions. Returns the archived list."""
        currents = await ScoringVersionService._get_active_versions(db, repo_name)
        for c in currents:
            c.status = "archived"
        return currents

    @staticmethod
    def _set_version_active(version, created_by: str | None) -> None:
        """Mark a version as active with timestamp."""
        version.status = "active"
        version.activated_at = datetime.now(UTC)
        version.activated_by = created_by

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
    async def _sync_weights_to_settings(db, weights: dict | None):
        """将版本权重同步到 ContributorBoardSettings.scoring_weights，
        排行榜算分时才读到新权重——此前只改版本表导致排行榜不生效。"""
        if not weights or not isinstance(weights, dict):
            return
        from sqlalchemy import select
        from infrastructure.persistence.models import ContributorBoardSettings

        from contributor_board.scoring_engine import DEFAULT_WEIGHTS
        # 规范化：只保留 7 维 key 并对齐默认权重
        weights = {k: float(weights.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS}

        row = (await db.execute(
            select(ContributorBoardSettings).where(
                ContributorBoardSettings.setting_key == "scoring_weights"
            )
        )).scalars().first()
        if row:
            cur = row.setting_value if isinstance(row.setting_value, dict) else {}
            cur.update(weights)
            row.setting_value = {k: float(cur.get(k, DEFAULT_WEIGHTS[k])) for k in DEFAULT_WEIGHTS}
        else:
            db.add(ContributorBoardSettings(setting_key="scoring_weights", setting_value=dict(weights)))

        logger.info("Scoring weights synced to ContributorBoardSettings from applied version")

    @staticmethod
    async def _create_snapshot_and_recalculate(
        db,
        version: ContributorScoringVersion,
        repo_name: str | None,
        created_by: str | None,
    ) -> int | None:
        from contributor_board.scoring_engine import (
            assign_tier,
            calculate_score,
            get_default_weights,
            infer_role,
        )

        # 1) 从宽表聚合各贡献者 7 维原始指标
        stmt = select(ContributorProfile)
        if repo_name:
            stmt = stmt.where(ContributorProfile.repo_name == repo_name)
        profiles = (await db.execute(stmt)).scalars().all()

        # 应用版本权重
        weights: dict[str, float] = dict(get_default_weights())
        if version.weights and isinstance(version.weights, dict):
            weights.update(version.weights)

        metrics_by_login: dict[str, dict[str, float]] = {}
        # 同时建立 login -> profile 映射，用于回写宽表
        profile_by_login: dict[str, Any] = {}
        for p in profiles:
            profile_by_login[p.login] = p
            metrics_by_login[p.login] = {
                "commit_count": p.commit_count or 0,
                "code_files": p.code_files or 0,
                "doc_files": p.doc_files or 0,
                "pr_merged": p.pr_merged or 0,
                "pr_open": p.pr_open or 0,
                "pr_closed": p.pr_closed or 0,
                "active_days_90": p.active_days_90 or 0,
                "span_days": p.span_days or 0,
                "review_count": p.review_count or 0,
                "approval_count": p.approval_count or 0,
            }

        # 2) 计算分数与分层 —— 同时回写宽表 total_score/tier/role，
        #    让排行榜（读宽表 total_score）与四象限图、AI 报告都能读到最新分数
        scores = []
        for login, m in metrics_by_login.items():
            score = calculate_score(m, weights)
            active_days = m.get("active_days_90", 0)
            tier = assign_tier(score, active_days)
            role = infer_role(m)
            profile = profile_by_login.get(login)
            if profile is not None:
                profile.total_score = score
                profile.tier = tier
                profile.role = role
            scores.append({
                "login": login,
                "score": score,
                "error_score": 0,
                "quadrant": tier,
            })

        # 4) Aggregate statistics
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
            description=f"基于 PR 流水线数据重算，{len(scores)} 位贡献者",
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
            # 避免除以接近0的值产生天文数字百分比；使用绝对差值作为主指标
            if abs(o) > 0.001:
                change_pct = round(change / o * 100, 2)
            elif abs(n) > 0:
                change_pct = round(n * 100, 2)  # 从0到有，用新值*100表示变化
            else:
                change_pct = 0.0
            # 百分比封顶（避免单个维度变化被过度放大）
            change_pct = max(-100.0, min(100.0, change_pct))
            diff[k] = {"from": o, "to": n, "change": change, "change_pct": change_pct}
        return diff

    @staticmethod
    def _max_weight_shift(weight_diff: dict) -> float:
        """最大权重变动幅度：使用绝对差值*100（百分比点），而非相对百分比"""
        if not weight_diff:
            return 0.0
        # 取绝对差值的最大值，更稳定反映实际变化
        return round(max(abs(d["change"]) for d in weight_diff.values()) * 100, 2)

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
