"""
分析器 - 构建数据的聚合统计与趋势分析
"""
import json
import logging
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import (
    ReleaseBuildGroup,
    ReleaseBuildRun,
    ReleaseWorkflow,
)

logger = logging.getLogger(__name__)


class ReleaseAnalyzer:
    """发布件构建分析器"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview(
        self, workflow_ids: list[int], days_back: int = 30
    ) -> dict[str, Any]:
        """获取看板概览数据"""
        since = datetime.now(UTC) - timedelta(days=days_back)

        stmt = (
            select(ReleaseBuildRun)
            .where(
                ReleaseBuildRun.workflow_id.in_(workflow_ids),
                ReleaseBuildRun.created_at_remote >= since,
            )
            .order_by(ReleaseBuildRun.created_at_remote.desc())
        )
        result = await self.db.execute(stmt)
        runs = list(result.scalars().all())

        if not runs:
            return self._empty_overview()

        stats = self._compute_stats(runs)

        groups_stmt = (
            select(ReleaseBuildGroup)
            .join(ReleaseBuildRun, ReleaseBuildGroup.run_id == ReleaseBuildRun.id)
            .where(ReleaseBuildRun.workflow_id.in_(workflow_ids))
        )
        groups_result = await self.db.execute(groups_stmt)
        groups = list(groups_result.scalars().all())

        return {
            "stats": stats,
            "timeline": self._build_timeline(runs),
            "phase_data": self._build_phase_data(groups),
            "target_data": self._build_target_data(groups),
            "arch_data": self._build_arch_data(groups),
            "step_data": self._build_step_data(groups),
            "run_breakdown": self._build_run_breakdown(runs),
        }

    async def get_runs(
        self,
        workflow_ids: list[int],
        status: str | None = None,
        branch: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """获取运行记录列表"""
        conditions = [ReleaseBuildRun.workflow_id.in_(workflow_ids)]
        if status:
            conditions.append(ReleaseBuildRun.status == status)
        if branch:
            conditions.append(ReleaseBuildRun.branch == branch)

        count_stmt = select(func.count(ReleaseBuildRun.id)).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(ReleaseBuildRun)
            .where(*conditions)
            .order_by(ReleaseBuildRun.created_at_remote.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        runs = list(result.scalars().all())

        return {
            "runs": [self._run_to_dict(r) for r in runs],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_run_detail(self, run_id: int) -> dict[str, Any] | None:
        """获取单次运行详情"""
        run = (await self.db.execute(
            select(ReleaseBuildRun).where(ReleaseBuildRun.id == run_id)
        )).scalar_one_or_none()
        if not run:
            return None

        groups = (await self.db.execute(
            select(ReleaseBuildGroup)
            .where(ReleaseBuildGroup.run_id == run_id)
            .order_by(ReleaseBuildGroup.total_duration.desc())
        )).scalars().all()

        return {
            "run": self._run_to_dict(run),
            "groups": [self._group_to_dict(g) for g in groups],
        }

    # --- Internal computation ---

    def _compute_stats(self, runs: list) -> dict[str, Any]:
        total = len(runs)
        successful = sum(1 for r in runs if r.status == "success")
        failed = sum(1 for r in runs if r.status == "failure")
        cancelled = sum(1 for r in runs if r.status == "cancelled")

        exec_durs = [r.exec_duration for r in runs if r.exec_duration > 0]
        avg_exec = sum(exec_durs) / len(exec_durs) if exec_durs else 0
        min_exec = min(exec_durs) if exec_durs else 0
        max_exec = max(exec_durs) if exec_durs else 0
        cv_exec = (
            (statistics.stdev(exec_durs) / statistics.mean(exec_durs) * 100)
            if len(exec_durs) > 2 else 0
        )

        wait_durs = [r.wait_duration for r in runs]
        avg_wait = sum(wait_durs) / len(wait_durs) if wait_durs else 0
        max_wait = max(wait_durs) if wait_durs else 0

        wall_durs = [r.wall_duration for r in runs if r.wall_duration > 0]
        avg_wall = sum(wall_durs) / len(wall_durs) if wall_durs else 0
        max_wall = max(wall_durs) if wall_durs else 0

        success_rate = successful / max(total, 1) * 100

        abnormal_count = sum(
            1 for r in runs
            if r.exec_duration > avg_exec * 1.5 and r.status != "cancelled"
        )
        long_wait_count = sum(1 for r in runs if r.wait_duration > 30 * 60)

        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "cancelled": cancelled,
            "success_rate": round(success_rate, 1),
            "avg_exec": round(avg_exec, 2),
            "min_exec": round(min_exec, 2),
            "max_exec": round(max_exec, 2),
            "cv_exec": round(cv_exec, 1),
            "avg_wait": round(avg_wait, 2),
            "max_wait": round(max_wait, 2),
            "avg_wall": round(avg_wall, 2),
            "max_wall": round(max_wall, 2),
            "abnormal_count": abnormal_count,
            "long_wait_count": long_wait_count,
        }

    def _build_timeline(self, runs: list) -> list[dict]:
        sorted_runs = sorted(runs, key=lambda x: x.created_at_remote or datetime.min.replace(tzinfo=UTC))
        return [
            {
                "date": r.created_at_remote.strftime("%Y-%m-%d") if r.created_at_remote else "",
                "date_full": r.created_at_remote.strftime("%Y-%m-%d %H:%M:%S") if r.created_at_remote else "",
                "created": (r.created_at_remote or datetime.min).isoformat(),
                "run_id": str(r.github_run_id) if r.github_run_id else "",
                "status": r.status,
                "wait": float(r.wait_duration or 0),
                "exec": float(r.exec_duration or 0),
                "wall": float(r.wall_duration or 0),
                "jobs": int(r.num_jobs or 0),
            }
            for r in sorted_runs
        ]

    def _build_phase_data(self, groups: list) -> list[dict]:
        phase_map: dict[str, dict] = {}
        for g in groups:
            p = g.phase or "other"
            if p not in phase_map:
                phase_map[p] = {"name": p, "count": 0, "total": 0, "max": 0}
            phase_map[p]["count"] += 1
            phase_map[p]["total"] += g.total_duration
            phase_map[p]["max"] = max(phase_map[p]["max"], g.total_duration)

        return [
            {**v, "avg": round(v["total"] / max(v["count"], 1), 2)}
            for v in sorted(phase_map.values(), key=lambda x: x["total"], reverse=True)
        ]

    def _build_target_data(self, groups: list) -> list[dict]:
        target_durs: dict[str, list[float]] = {}
        for g in groups:
            t = g.target or "unknown"
            if t not in target_durs:
                target_durs[t] = []
            target_durs[t].append(g.total_duration)

        return [
            {
                "name": t,
                "count": len(durs),
                "avg": round(sum(durs) / len(durs), 2),
                "min": round(min(durs), 2),
                "max": round(max(durs), 2),
                "total": round(sum(durs), 2),
            }
            for t, durs in sorted(
                target_durs.items(),
                key=lambda x: sum(x[1]) / max(len(x[1]), 1),
                reverse=True,
            )
        ]

    def _build_arch_data(self, groups: list) -> list[dict]:
        arch_map: dict[str, dict] = {}
        for g in groups:
            a = g.architecture or "unknown"
            if a not in arch_map:
                arch_map[a] = {"name": a, "count": 0, "total": 0}
            arch_map[a]["count"] += 1
            arch_map[a]["total"] += g.total_duration

        return [
            {**v, "avg": round(v["total"] / max(v["count"], 1), 2)}
            for v in sorted(arch_map.values(), key=lambda x: x["total"], reverse=True)
        ]

    def _build_step_data(self, groups: list) -> list[dict]:
        step_map: dict[str, dict] = {}
        for g in groups:
            if not g.steps_json:
                continue
            try:
                steps = json.loads(g.steps_json)
            except (json.JSONDecodeError, TypeError):
                continue
            for step in steps:
                sn = step.get("name", "")
                if sn not in step_map:
                    step_map[sn] = {"name": sn, "count": 0, "total": 0, "max": 0}
                step_map[sn]["count"] += 1
                step_map[sn]["total"] += step.get("duration", 0)
                step_map[sn]["max"] = max(step_map[sn]["max"], step.get("duration", 0))

        sorted_steps = sorted(step_map.values(), key=lambda x: x["total"], reverse=True)
        return [
            {**v, "avg": round(v["total"] / max(v["count"], 1), 2)}
            for v in sorted_steps[:20]
        ]

    def _build_run_breakdown(self, runs: list) -> list[dict]:
        sorted_runs = sorted(runs, key=lambda x: x.wall_duration, reverse=True)[:15]
        return [
            {
                "run_id": r.github_run_id,
                "date": r.created_at_remote.strftime("%Y-%m-%d") if r.created_at_remote else "",
                "status": r.status,
                "wait": r.wait_duration,
                "exec": r.exec_duration,
                "wall": r.wall_duration,
                "jobs": r.num_jobs,
            }
            for r in sorted_runs
        ]

    def _empty_overview(self) -> dict[str, Any]:
        return {
            "stats": {
                "total": 0, "successful": 0, "failed": 0, "cancelled": 0,
                "success_rate": 0, "avg_exec": 0, "min_exec": 0, "max_exec": 0,
                "cv_exec": 0, "avg_wait": 0, "max_wait": 0, "avg_wall": 0, "max_wall": 0,
                "abnormal_count": 0, "long_wait_count": 0,
            },
            "timeline": [], "phase_data": [], "target_data": [],
            "arch_data": [], "step_data": [], "run_breakdown": [],
        }

    @staticmethod
    def _run_to_dict(r) -> dict[str, Any]:
        return {
            "id": r.id,
            "workflow_id": r.workflow_id,
            "github_run_id": r.github_run_id,
            "status": r.status,
            "event": r.event,
            "branch": r.branch,
            "commit_sha": r.commit_sha,
            "created_at": r.created_at_remote.isoformat() if r.created_at_remote else "",
            "started_at": r.started_at_remote.isoformat() if r.started_at_remote else "",
            "completed_at": r.completed_at_remote.isoformat() if r.completed_at_remote else "",
            "wait_duration": r.wait_duration,
            "exec_duration": r.exec_duration,
            "wall_duration": r.wall_duration,
            "num_jobs": r.num_jobs,
            "conclusion_summary": json.loads(r.conclusion_summary) if r.conclusion_summary else {},
        }

    @staticmethod
    def _group_to_dict(g) -> dict[str, Any]:
        return {
            "id": g.id,
            "run_id": g.run_id,
            "group_name": g.group_name,
            "target": g.target,
            "os_name": g.os_name,
            "phase": g.phase,
            "status": g.status,
            "total_duration": g.total_duration,
            "runner_name": g.runner_name,
            "architecture": g.architecture,
            "sub_job_name": g.sub_job_name,
            "steps": json.loads(g.steps_json) if g.steps_json else [],
        }
