"""镜像构建监控与通知服务。

基于发布件管理已采集的 release_workflows / release_build_runs 数据做规则判定，
不额外调用 GitHub API。异常按 failure / missing / stuck 三类落库并通过邮件通知。
"""
from __future__ import annotations

import html as html_lib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.core.github_credentials import global_github_configured
from infrastructure.persistence.models import (
    ImageBuildAlert,
    ImageBuildMonitorTarget,
    ReleaseBuildGroup,
    ReleaseBuildRun,
    ReleaseWorkflow,
)
from notifications.service import NotificationDispatcher

logger = logging.getLogger(__name__)

FAILED_STATUSES = {"failure", "timed_out", "startup_failure"}
CANCELLED_STATUSES = {"cancelled"}
ACTIVE_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}
SUCCESS_STATUSES = {"success", "completed"}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _parse_gh_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _normalize_emails(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").replace("\n", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


def _run_url(workflow: ReleaseWorkflow, run: ReleaseBuildRun | None) -> str | None:
    if run is None:
        return None
    return f"https://github.com/{workflow.repo_owner}/{workflow.repo_name}/actions/runs/{run.github_run_id}"


def _previous_fire_time(cron: str, tz_name: str, now: datetime) -> datetime | None:
    """返回严格早于 now 的上一次 cron 触发时间（UTC）。"""
    if not cron:
        return None
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=ZoneInfo(tz_name or "Asia/Shanghai"))
    except Exception as exc:
        logger.warning("Invalid schedule_cron '%s': %s", cron, exc)
        return None
    try:
        for window_days in (2, 40):
            cursor = trigger.get_next_fire_time(None, now - timedelta(days=window_days))
            previous = None
            for _ in range(3000):
                if cursor is None:
                    break
                cursor_utc = _as_utc(cursor)
                if cursor_utc is None or cursor_utc > now:
                    break
                previous = cursor_utc
                cursor = trigger.get_next_fire_time(cursor, cursor + timedelta(seconds=1))
            if previous is not None:
                return previous
        return None
    except Exception as exc:
        logger.warning("Failed to compute previous fire time for cron '%s': %s", cron, exc)
        return None


def _parse_hhmm(value: str | None, default: str) -> int:
    """将 HH:MM 解析为当天分钟数，非法时返回默认值。"""
    raw = (value or default).strip()
    try:
        hour, minute = raw.split(":", 1)
        h, m = int(hour), int(minute)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except (ValueError, TypeError):
        pass
    h, m = default.split(":", 1)
    return int(h) * 60 + int(m)


def _in_monitor_window(value: datetime | None, target: Any) -> bool:
    """判断时间是否落在监控时间窗口内（支持跨天，如 20:00-08:00）。"""
    if value is None:
        return False
    enabled = getattr(target, "notify_window_enabled", True)
    if enabled is not None and not bool(enabled):
        return True
    tz_name = getattr(target, "timezone", None) or "Asia/Shanghai"
    try:
        local = _as_utc(value).astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        local = _as_utc(value)
    minutes = local.hour * 60 + local.minute
    start = _parse_hhmm(getattr(target, "notify_window_start", None), "20:00")
    end = _parse_hhmm(getattr(target, "notify_window_end", None), "08:00")
    if start == end:
        return True
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def _window_label(target: Any) -> str:
    enabled = getattr(target, "notify_window_enabled", True)
    if enabled is not None and not bool(enabled):
        return "全天"
    start = getattr(target, "notify_window_start", None) or "20:00"
    end = getattr(target, "notify_window_end", None) or "08:00"
    tz_name = getattr(target, "timezone", None) or "Asia/Shanghai"
    return f"{start}-{end} ({tz_name})"


class ImageBuildMonitorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Target CRUD helpers
    # ------------------------------------------------------------------

    async def list_targets(self) -> list[dict[str, Any]]:
        rows = (
            await self.db.execute(
                select(ImageBuildMonitorTarget, ReleaseWorkflow)
                .join(ReleaseWorkflow, ReleaseWorkflow.id == ImageBuildMonitorTarget.release_workflow_id)
                .order_by(ReleaseWorkflow.repo_name, ReleaseWorkflow.workflow_name)
            )
        ).all()
        target_ids = [t.id for t, _ in rows]
        open_counts: dict[int, int] = {}
        if target_ids:
            count_rows = (
                await self.db.execute(
                    select(ImageBuildAlert.target_id, func.count(ImageBuildAlert.id))
                    .where(
                        ImageBuildAlert.target_id.in_(target_ids),
                        ImageBuildAlert.status != "resolved",
                    )
                    .group_by(ImageBuildAlert.target_id)
                )
            ).all()
            open_counts = {int(tid): int(cnt) for tid, cnt in count_rows}

        result = []
        for target, wf in rows:
            latest_run = (
                await self.db.execute(
                    select(ReleaseBuildRun)
                    .where(ReleaseBuildRun.workflow_id == wf.id)
                    .order_by(ReleaseBuildRun.created_at_remote.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            result.append(
                self._serialize_target(target, wf, open_counts.get(target.id, 0), latest_run)
            )
        return result

    async def list_candidates(self) -> list[dict[str, Any]]:
        workflows = (
            await self.db.execute(
                select(ReleaseWorkflow).order_by(ReleaseWorkflow.repo_name, ReleaseWorkflow.workflow_name)
            )
        ).scalars().all()
        linked = {
            int(row[0])
            for row in (
                await self.db.execute(select(ImageBuildMonitorTarget.release_workflow_id))
            ).all()
        }
        return [
            {
                "id": wf.id,
                "repo_owner": wf.repo_owner,
                "repo_name": wf.repo_name,
                "workflow_id": wf.workflow_id,
                "workflow_name": wf.workflow_name,
                "workflow_path": wf.workflow_path,
                "workflow_type": wf.workflow_type,
                "enabled": wf.enabled,
                "already_monitored": int(wf.id) in linked,
            }
            for wf in workflows
        ]

    @staticmethod
    def _serialize_target(
        target: ImageBuildMonitorTarget,
        wf: ReleaseWorkflow,
        open_alert_count: int,
        latest_run: ReleaseBuildRun | None,
    ) -> dict[str, Any]:
        return {
            "id": target.id,
            "release_workflow_id": target.release_workflow_id,
            "repo_owner": wf.repo_owner,
            "repo_name": wf.repo_name,
            "workflow_name": wf.workflow_name,
            "workflow_path": wf.workflow_path,
            "workflow_type": wf.workflow_type,
            "enabled": target.enabled,
            "schedule_cron": target.schedule_cron,
            "timezone": target.timezone,
            "grace_minutes": target.grace_minutes,
            "lookback_days": target.lookback_days,
            "notify_emails": _normalize_emails(target.notify_emails),
            "notify_on_failure": target.notify_on_failure,
            "notify_on_missing": target.notify_on_missing,
            "notify_on_stuck": target.notify_on_stuck,
            "notify_window_enabled": bool(
                getattr(target, "notify_window_enabled", True) is not False
            ),
            "notify_window_start": getattr(target, "notify_window_start", None) or "20:00",
            "notify_window_end": getattr(target, "notify_window_end", None) or "08:00",
            "stuck_threshold_minutes": target.stuck_threshold_minutes,
            "cooldown_minutes": target.cooldown_minutes,
            "last_checked_at": _iso(target.last_checked_at),
            "last_check_success_at": _iso(
                getattr(target, "last_check_success_at", None)
            ),
            "last_check_error": getattr(target, "last_check_error", None),
            "open_alert_count": open_alert_count,
            "latest_run": (
                {
                    "github_run_id": latest_run.github_run_id,
                    "status": latest_run.status,
                    "event": latest_run.event,
                    "branch": latest_run.branch,
                    "created_at": _iso(latest_run.created_at_remote),
                    "wall_duration": latest_run.wall_duration,
                    "url": _run_url(wf, latest_run),
                }
                if latest_run
                else None
            ),
        }

    async def list_target_runs(
        self,
        target_id: int,
        *,
        branch: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        target = await self.db.get(ImageBuildMonitorTarget, target_id)
        if not target:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "branches": []}
        wf = await self.db.get(ReleaseWorkflow, target.release_workflow_id)
        if not wf:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "branches": []}

        branches = [
            str(value)
            for value in (
                await self.db.execute(
                    select(ReleaseBuildRun.branch)
                    .where(
                        ReleaseBuildRun.workflow_id == wf.id,
                        ReleaseBuildRun.branch.is_not(None),
                        ReleaseBuildRun.branch != "",
                    )
                    .distinct()
                    .order_by(ReleaseBuildRun.branch)
                )
            ).scalars().all()
        ]
        conditions = [ReleaseBuildRun.workflow_id == wf.id]
        if branch:
            conditions.append(ReleaseBuildRun.branch == branch)
        total = int(
            (
                await self.db.execute(
                    select(func.count(ReleaseBuildRun.id)).where(*conditions)
                )
            ).scalar_one()
        )
        runs = (
            await self.db.execute(
                select(ReleaseBuildRun)
                .where(*conditions)
                .order_by(ReleaseBuildRun.created_at_remote.desc())
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return {
            "items": [self._serialize_target_run(wf, run) for run in runs],
            "total": total,
            "page": page,
            "page_size": page_size,
            "branches": branches,
        }

    async def get_target_runs(self, target_id: int, limit: int = 20) -> list[dict[str, Any]]:
        result = await self.list_target_runs(target_id, page=1, page_size=limit)
        return result["items"]

    @staticmethod
    def _serialize_target_run(wf: ReleaseWorkflow, run: ReleaseBuildRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "github_run_id": run.github_run_id,
            "status": run.status,
            "event": run.event,
            "branch": run.branch,
            "commit_sha": run.commit_sha,
            "created_at": _iso(run.created_at_remote),
            "completed_at": _iso(run.completed_at_remote),
            "wall_duration": run.wall_duration,
            "num_jobs": run.num_jobs,
            "job_summary": ImageBuildMonitorService._parse_job_summary(
                run.conclusion_summary
            ),
            "url": _run_url(wf, run),
        }

    async def refresh_target_runs(self, target_id: int) -> int | None:
        """实时从 GitHub 拉取该目标最近运行并写入本地，返回同步条数。"""
        target = await self.db.get(ImageBuildMonitorTarget, target_id)
        if not target:
            return None
        wf = await self.db.get(ReleaseWorkflow, target.release_workflow_id)
        if not wf:
            return None
        synced = await self._sync_recent_runs(wf)
        target.last_checked_at = datetime.now(UTC)
        target.last_check_success_at = datetime.now(UTC)
        target.last_check_error = None
        await self.db.commit()
        return synced

    @staticmethod
    def _parse_job_summary(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # ------------------------------------------------------------------
    # Alert queries
    # ------------------------------------------------------------------

    async def list_alerts(
        self,
        *,
        status: str | None = None,
        alert_type: str | None = None,
        release_workflow_id: int | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        conditions = []
        if status:
            if status == "unresolved":
                conditions.append(ImageBuildAlert.status != "resolved")
            else:
                conditions.append(ImageBuildAlert.status == status)
        if alert_type:
            conditions.append(ImageBuildAlert.alert_type == alert_type)
        if release_workflow_id:
            conditions.append(ImageBuildAlert.release_workflow_id == release_workflow_id)
        start_dt = _parse_gh_dt(start_time)
        if start_dt:
            conditions.append(ImageBuildAlert.first_detected_at >= start_dt)
        end_dt = _parse_gh_dt(end_time)
        if end_dt:
            conditions.append(ImageBuildAlert.first_detected_at <= end_dt)

        total = int(
            (
                await self.db.execute(
                    select(func.count(ImageBuildAlert.id)).where(*conditions)
                )
            ).scalar_one()
        )
        rows = (
            await self.db.execute(
                select(ImageBuildAlert, ReleaseWorkflow)
                .join(ReleaseWorkflow, ReleaseWorkflow.id == ImageBuildAlert.release_workflow_id)
                .where(*conditions)
                .order_by(ImageBuildAlert.first_detected_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [self._serialize_alert(alert, wf) for alert, wf in rows],
        }

    @staticmethod
    def _serialize_alert(alert: ImageBuildAlert, wf: ReleaseWorkflow) -> dict[str, Any]:
        return {
            "id": alert.id,
            "target_id": alert.target_id,
            "release_workflow_id": alert.release_workflow_id,
            "repo_owner": wf.repo_owner,
            "repo_name": wf.repo_name,
            "workflow_name": wf.workflow_name,
            "github_run_id": alert.github_run_id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "status": alert.status,
            "title": alert.title,
            "message": alert.message,
            "run_url": alert.run_url,
            "branch": alert.branch,
            "event": alert.event,
            "first_detected_at": _iso(alert.first_detected_at),
            "last_detected_at": _iso(alert.last_detected_at),
            "resolved_at": _iso(alert.resolved_at),
            "resolved_by": alert.resolved_by,
            "notified_at": _iso(alert.notified_at),
            "last_notified_at": _iso(alert.last_notified_at),
            "notification_success": alert.notification_success,
            "notification_error": alert.notification_error,
            "details": alert.details,
        }

    async def _run_for_alert(self, alert: ImageBuildAlert) -> ReleaseBuildRun | None:
        if not alert.github_run_id:
            return None
        return (
            await self.db.execute(
                select(ReleaseBuildRun).where(
                    ReleaseBuildRun.workflow_id == alert.release_workflow_id,
                    ReleaseBuildRun.github_run_id == alert.github_run_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _failed_steps(raw_steps: Any) -> list[dict[str, Any]]:
        if isinstance(raw_steps, str):
            try:
                raw_steps = json.loads(raw_steps or "[]")
            except (TypeError, ValueError):
                raw_steps = []
        if not isinstance(raw_steps, list):
            return []
        result = []
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            status = str(step.get("conclusion") or step.get("status") or "").lower()
            if status in FAILED_STATUSES:
                result.append(
                    {
                        "name": step.get("name") or "",
                        "number": step.get("number"),
                        "status": status,
                        "duration": step.get("duration") or 0,
                    }
                )
        return result

    @classmethod
    def _group_to_failed_job(cls, group: ReleaseBuildGroup) -> dict[str, Any]:
        return {
            "name": group.group_name or group.sub_job_name or "未命名 Job",
            "target": group.target,
            "os": group.os_name,
            "phase": group.phase,
            "architecture": group.architecture,
            "status": group.status,
            "url": None,
            "failed_steps": cls._failed_steps(group.steps_json),
        }

    async def _failed_jobs_for_run(
        self,
        wf: ReleaseWorkflow,
        run: ReleaseBuildRun | None,
        *,
        allow_remote: bool = True,
    ) -> list[dict[str, Any]]:
        if run is None:
            return []
        groups = (
            await self.db.execute(
                select(ReleaseBuildGroup).where(ReleaseBuildGroup.run_id == run.id)
            )
        ).scalars().all()
        failed = [
            self._group_to_failed_job(group)
            for group in groups
            if str(group.status or "").lower() in FAILED_STATUSES
        ]
        if failed or not allow_remote or not run.github_run_id or not global_github_configured():
            return failed

        client = GitHubClient(
            owner=wf.repo_owner,
            repo=wf.repo_name,
        )
        try:
            jobs = await client.get_job_list(int(run.github_run_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch jobs for run %s: %s", run.github_run_id, exc)
            return []
        finally:
            await client.close()

        return [
            {
                "name": job.get("name") or "未命名 Job",
                "target": None,
                "os": None,
                "phase": None,
                "architecture": None,
                "status": str(job.get("conclusion") or job.get("status") or "").lower(),
                "url": job.get("html_url"),
                "failed_steps": self._failed_steps(job.get("steps") or []),
            }
            for job in jobs
            if str(job.get("conclusion") or job.get("status") or "").lower() in FAILED_STATUSES
        ]

    async def get_alert_detail(self, alert_id: int) -> dict[str, Any] | None:
        alert = await self.db.get(ImageBuildAlert, alert_id)
        if not alert:
            return None
        wf = await self.db.get(ReleaseWorkflow, alert.release_workflow_id)
        if not wf:
            return None
        run = await self._run_for_alert(alert)
        job_summary: dict[str, Any] = {}
        failed_jobs: list[dict[str, Any]] = []
        if alert.alert_type == "failure" and run is not None:
            try:
                job_summary = json.loads(run.conclusion_summary or "{}")
            except (TypeError, ValueError):
                job_summary = {}
            failed_jobs = await self._failed_jobs_for_run(wf, run)
        return {
            **self._serialize_alert(alert, wf),
            "job_summary": job_summary,
            "failed_jobs": failed_jobs,
            "run": (
                {
                    "github_run_id": run.github_run_id,
                    "status": run.status,
                    "event": run.event,
                    "branch": run.branch,
                    "commit_sha": run.commit_sha,
                    "created_at": _iso(run.created_at_remote),
                    "completed_at": _iso(run.completed_at_remote),
                    "wall_duration": run.wall_duration,
                    "num_jobs": run.num_jobs,
                    "url": _run_url(wf, run),
                }
                if run is not None
                else None
            ),
        }

    async def ack_alert(self, alert_id: int, operator: str) -> dict[str, Any] | None:
        alert = await self.db.get(ImageBuildAlert, alert_id)
        if not alert:
            return None
        if alert.status == "open":
            alert.status = "acknowledged"
        await self.db.commit()
        return await self._alert_with_workflow(alert)

    async def resolve_alert(self, alert_id: int, operator: str) -> dict[str, Any] | None:
        alert = await self.db.get(ImageBuildAlert, alert_id)
        if not alert:
            return None
        alert.status = "resolved"
        alert.resolved_at = datetime.now(UTC)
        alert.resolved_by = operator
        await self.db.commit()
        return await self._alert_with_workflow(alert)

    async def renotify_alert(self, alert_id: int) -> dict[str, Any] | None:
        alert = await self.db.get(ImageBuildAlert, alert_id)
        if not alert:
            return None
        target = await self.db.get(ImageBuildMonitorTarget, alert.target_id)
        wf = await self.db.get(ReleaseWorkflow, alert.release_workflow_id)
        if not target or not wf:
            return None
        await self._send_alerts_email(target, wf, [alert])
        await self.db.commit()
        return await self._alert_with_workflow(alert)

    async def send_test_email(
        self, recipients: list[str], target_id: int | None = None
    ) -> dict[str, Any]:
        """发送一封真实的测试告警邮件，用于本地校验邮件内容。"""
        target = await self.db.get(ImageBuildMonitorTarget, target_id) if target_id else None
        wf = (
            await self.db.get(ReleaseWorkflow, target.release_workflow_id)
            if target
            else None
        )
        if wf is None:
            wf = (
                await self.db.execute(
                    select(ReleaseWorkflow)
                    .where(
                        (ReleaseWorkflow.workflow_name.ilike("%image%"))
                        | (ReleaseWorkflow.workflow_path.ilike("%image%"))
                    )
                    .order_by(ReleaseWorkflow.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if wf is None:
            wf = (await self.db.execute(select(ReleaseWorkflow).limit(1))).scalar_one_or_none()
        if wf is None:
            wf = ReleaseWorkflow(
                repo_owner=settings.GITHUB_OWNER,
                repo_name=settings.GITHUB_REPO,
                workflow_name="Image Build Monitor Test",
                workflow_path=".github/workflows/schedule_image_build_and_push.yaml",
                workflow_id=0,
            )
        if target is None:
            target = ImageBuildMonitorTarget(
                release_workflow_id=wf.id or 0,
                notify_emails=recipients,
                cooldown_minutes=60,
            )
        else:
            target.notify_emails = recipients

        now = datetime.now(UTC)
        sample = ImageBuildAlert(
            target_id=target.id or 0,
            release_workflow_id=wf.id or 0,
            github_run_id=None,
            alert_type="failure",
            severity="critical",
            status="open",
            title=f"【测试】镜像构建失败：{wf.workflow_name}",
            message=(
                "这是一封测试邮件，用于验证镜像构建监控告警的邮件格式与收件配置。"
                "真实告警会包含失败 run、分支、Job 汇总及 GitHub 链接。"
            ),
            run_url=f"https://github.com/{wf.repo_owner}/{wf.repo_name}/actions",
            branch="main",
            event="schedule",
            dedupe_key=f"test:{now.isoformat()}",
            first_detected_at=now,
            last_detected_at=now,
        )
        await self._send_alerts_email(target, wf, [sample])
        return {
            "success": bool(sample.notification_success),
            "recipients": recipients,
            "error": sample.notification_error,
            "subject": (
                f"[vLLM Ascend 镜像构建告警] {wf.repo_name}/{wf.workflow_name} - 构建失败"
            ),
        }

    async def _alert_with_workflow(self, alert: ImageBuildAlert) -> dict[str, Any]:
        wf = await self.db.get(ReleaseWorkflow, alert.release_workflow_id)
        assert wf is not None
        return self._serialize_alert(alert, wf)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    async def get_overview(self, days_back: int = 7) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days_back)
        targets = (
            await self.db.execute(
                select(ImageBuildMonitorTarget, ReleaseWorkflow)
                .join(ReleaseWorkflow, ReleaseWorkflow.id == ImageBuildMonitorTarget.release_workflow_id)
                .order_by(ReleaseWorkflow.repo_name, ReleaseWorkflow.workflow_name)
            )
        ).all()
        wf_ids = [wf.id for _, wf in targets]

        run_count = 0
        failed_count = 0
        if wf_ids:
            run_count = int(
                (
                    await self.db.execute(
                        select(func.count(ReleaseBuildRun.id)).where(
                            ReleaseBuildRun.workflow_id.in_(wf_ids),
                            ReleaseBuildRun.created_at_remote >= since,
                        )
                    )
                ).scalar_one()
            )
            failed_count = int(
                (
                    await self.db.execute(
                        select(func.count(ReleaseBuildRun.id)).where(
                            ReleaseBuildRun.workflow_id.in_(wf_ids),
                            ReleaseBuildRun.created_at_remote >= since,
                            ReleaseBuildRun.status.in_(FAILED_STATUSES),
                        )
                    )
                ).scalar_one()
            )

        alert_rows = (
            await self.db.execute(
                select(ImageBuildAlert).where(ImageBuildAlert.first_detected_at >= since)
            )
        ).scalars().all()
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for alert in alert_rows:
            by_type[alert.alert_type] = by_type.get(alert.alert_type, 0) + 1
            by_status[alert.status] = by_status.get(alert.status, 0) + 1

        total_open = int(
            (
                await self.db.execute(
                    select(func.count(ImageBuildAlert.id)).where(
                        ImageBuildAlert.status == "open"
                    )
                )
            ).scalar_one()
        )
        total_unresolved = int(
            (
                await self.db.execute(
                    select(func.count(ImageBuildAlert.id)).where(
                        ImageBuildAlert.status != "resolved"
                    )
                )
            ).scalar_one()
        )

        target_health = []
        for target, wf in targets:
            latest_run = (
                await self.db.execute(
                    select(ReleaseBuildRun)
                    .where(ReleaseBuildRun.workflow_id == wf.id)
                    .order_by(ReleaseBuildRun.created_at_remote.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            open_count = int(
                (
                    await self.db.execute(
                        select(func.count(ImageBuildAlert.id)).where(
                            ImageBuildAlert.target_id == target.id,
                            ImageBuildAlert.status != "resolved",
                        )
                    )
                ).scalar_one()
            )
            target_health.append(
                self._serialize_target(target, wf, open_count, latest_run)
            )

        recent_alerts = (
            await self.db.execute(
                select(ImageBuildAlert, ReleaseWorkflow)
                .join(ReleaseWorkflow, ReleaseWorkflow.id == ImageBuildAlert.release_workflow_id)
                .order_by(ImageBuildAlert.first_detected_at.desc())
                .limit(10)
            )
        ).all()

        return {
            "days_back": days_back,
            "targets_total": len(targets),
            "targets_enabled": sum(1 for target, _ in targets if target.enabled),
            "runs_total": run_count,
            "failed_runs": failed_count,
            "alerts_total": len(alert_rows),
            "open_alerts": total_open,
            "unresolved_alerts": total_unresolved,
            "by_type": by_type,
            "by_status": by_status,
            "monitor_health": {
                "last_checked_at": _iso(
                    max(
                        (_as_utc(t.last_checked_at) for t, _ in targets if t.last_checked_at),
                        default=None,
                    )
                ),
                "last_success_at": _iso(
                    max(
                        (
                            _as_utc(t.last_check_success_at)
                            for t, _ in targets
                            if getattr(t, "last_check_success_at", None)
                        ),
                        default=None,
                    )
                ),
                "targets_with_error": sum(
                    1 for t, _ in targets if getattr(t, "last_check_error", None)
                ),
            },
            "target_health": target_health,
            "recent_alerts": [self._serialize_alert(a, wf) for a, wf in recent_alerts],
        }

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate_all(self, sync_remote: bool = False) -> dict[str, Any]:
        targets = (
            await self.db.execute(
                select(ImageBuildMonitorTarget, ReleaseWorkflow)
                .join(ReleaseWorkflow, ReleaseWorkflow.id == ImageBuildMonitorTarget.release_workflow_id)
                .where(ImageBuildMonitorTarget.enabled.is_(True), ReleaseWorkflow.enabled.is_(True))
            )
        ).all()
        summary = {
            "targets": len(targets),
            "synced_runs": 0,
            "created": 0,
            "notified": 0,
            "resolved": 0,
            "errors": [],
        }
        health_errors: dict[int, str | None] = {}
        for target, wf in targets:
            target_id = target.id
            target_error: str | None = None
            try:
                if sync_remote:
                    try:
                        summary["synced_runs"] += await self._sync_recent_runs(wf)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to refresh GitHub runs for workflow %s: %s",
                            wf.id,
                            exc,
                        )
                        target_error = f"GitHub 同步失败: {exc}"
                        summary["errors"].append(f"{wf.workflow_name} 同步 GitHub 失败: {exc}")
                stats = await self._evaluate_target(target, wf)
                summary["created"] += stats["created"]
                summary["notified"] += stats["notified"]
                summary["resolved"] += stats["resolved"]
            except Exception as exc:  # noqa: BLE001
                target_error = str(exc)
                logger.error(
                    "Image build monitor evaluation failed for workflow %s: %s",
                    wf.id,
                    exc,
                    exc_info=True,
                )
                summary["errors"].append(f"{wf.workflow_name}: {exc}")
                await self.db.rollback()
            health_errors[target_id] = target_error

        now = datetime.now(UTC)
        for target_id, error in health_errors.items():
            row = await self.db.get(ImageBuildMonitorTarget, target_id)
            if not row:
                continue
            row.last_checked_at = now
            if error:
                row.last_check_error = error
            else:
                row.last_check_success_at = now
                row.last_check_error = None
        await self.db.commit()
        return summary

    async def _sync_recent_runs(self, wf: ReleaseWorkflow, days_back: int = 2) -> int:
        """通过真实 GitHub API 拉取目标 workflow 最近运行并落库。"""
        client = GitHubClient(
            owner=wf.repo_owner,
            repo=wf.repo_name,
        )
        try:
            runs = await client.get_workflow_runs(
                str(wf.workflow_id),
                per_page=50,
                days_back=days_back,
            )
        finally:
            await client.close()

        synced = 0
        for run in runs:
            github_run_id = run.get("id")
            if not github_run_id:
                continue
            record = (
                await self.db.execute(
                    select(ReleaseBuildRun).where(
                        ReleaseBuildRun.workflow_id == wf.id,
                        ReleaseBuildRun.github_run_id == github_run_id,
                    )
                )
            ).scalar_one_or_none()
            created_at = _parse_gh_dt(run.get("created_at"))
            started_at = _parse_gh_dt(run.get("run_started_at") or run.get("created_at"))
            completed_at = _parse_gh_dt(run.get("updated_at"))
            status = run.get("conclusion") or run.get("status") or ""
            wall_duration = (
                max(0.0, (completed_at - created_at).total_seconds())
                if completed_at and created_at
                else 0.0
            )
            if record is None:
                record = ReleaseBuildRun(
                    workflow_id=wf.id,
                    github_run_id=github_run_id,
                )
                self.db.add(record)
            record.status = status
            record.event = run.get("event") or ""
            record.branch = run.get("head_branch") or run.get("head_tag") or ""
            record.commit_sha = run.get("head_sha") or ""
            record.created_at_remote = created_at
            record.started_at_remote = started_at
            record.completed_at_remote = completed_at
            record.wall_duration = wall_duration
            record.num_jobs = record.num_jobs or 0
            record.conclusion_summary = record.conclusion_summary or "{}"
            synced += 1
        wf.last_sync_at = datetime.now(UTC)
        await self.db.commit()
        return synced

    async def _evaluate_target(
        self, target: ImageBuildMonitorTarget, wf: ReleaseWorkflow
    ) -> dict[str, int]:
        now = datetime.now(UTC)
        lookback = max(int(target.lookback_days or 2), 1)
        window_start = now - timedelta(days=lookback)

        # 首次启用只回看最近 24h 的失败，避免一次性刷出历史告警。
        failure_since = window_start
        last_checked = _as_utc(target.last_checked_at)
        if last_checked:
            failure_since = max(window_start, last_checked - timedelta(minutes=10))
        else:
            failure_since = max(window_start, now - timedelta(hours=24))

        runs = (
            await self.db.execute(
                select(ReleaseBuildRun)
                .where(
                    ReleaseBuildRun.workflow_id == wf.id,
                    ReleaseBuildRun.created_at_remote >= window_start,
                )
                .order_by(ReleaseBuildRun.created_at_remote.desc())
            )
        ).scalars().all()

        open_alerts = (
            await self.db.execute(
                select(ImageBuildAlert).where(
                    ImageBuildAlert.target_id == target.id,
                    ImageBuildAlert.status != "resolved",
                )
            )
        ).scalars().all()
        open_by_key = {alert.dedupe_key: alert for alert in open_alerts}

        all_keys = set((
                await self.db.execute(
                    select(ImageBuildAlert.dedupe_key).where(
                        ImageBuildAlert.target_id == target.id
                    )
                )
            ).scalars().all())

        created = 0
        to_notify: list[ImageBuildAlert] = []

        # ---- failure ----
        if target.notify_on_failure:
            for run in runs:
                status = (run.status or "").lower()
                created_at = _as_utc(run.created_at_remote)
                if status not in FAILED_STATUSES or not created_at or created_at < failure_since:
                    continue
                if not _in_monitor_window(created_at, target):
                    continue
                key = f"failure:{wf.id}:{run.github_run_id}"
                existing = open_by_key.get(key)
                if existing:
                    existing.last_detected_at = now
                    self._maybe_collect_notification(existing, target, now, to_notify)
                    continue
                if key in all_keys:
                    continue
                alert = ImageBuildAlert(
                    target_id=target.id,
                    release_workflow_id=wf.id,
                    github_run_id=run.github_run_id,
                    alert_type="failure",
                    severity="critical",
                    status="open",
                    title=f"镜像构建失败：{wf.workflow_name}",
                    message=self._failure_message(wf, run),
                    run_url=_run_url(wf, run),
                    branch=run.branch,
                    event=run.event,
                    dedupe_key=key,
                    first_detected_at=now,
                    last_detected_at=now,
                    details={
                        "status": run.status,
                        "commit_sha": run.commit_sha,
                        "conclusion_summary": run.conclusion_summary,
                    },
                )
                self.db.add(alert)
                await self.db.flush()
                all_keys.add(key)
                open_by_key[key] = alert
                to_notify.append(alert)
                created += 1

        # ---- stuck ----
        if target.notify_on_stuck:
            stuck_before = now - timedelta(minutes=max(int(target.stuck_threshold_minutes or 180), 5))
            for run in runs:
                status = (run.status or "").lower()
                created_at = _as_utc(run.created_at_remote)
                if status not in ACTIVE_STATUSES or not created_at or created_at > stuck_before:
                    continue
                if not _in_monitor_window(created_at, target):
                    continue
                key = f"stuck:{wf.id}:{run.github_run_id}"
                existing = open_by_key.get(key)
                if existing:
                    existing.last_detected_at = now
                    self._maybe_collect_notification(existing, target, now, to_notify)
                    continue
                if key in all_keys:
                    continue
                minutes = int((now - created_at).total_seconds() // 60)
                alert = ImageBuildAlert(
                    target_id=target.id,
                    release_workflow_id=wf.id,
                    github_run_id=run.github_run_id,
                    alert_type="stuck",
                    severity="warning",
                    status="open",
                    title=f"镜像构建长时间未完成：{wf.workflow_name}",
                    message=f"run #{run.github_run_id} 已持续 {minutes} 分钟（状态 {run.status}），超过阈值 {target.stuck_threshold_minutes} 分钟。",
                    run_url=_run_url(wf, run),
                    branch=run.branch,
                    event=run.event,
                    dedupe_key=key,
                    first_detected_at=now,
                    last_detected_at=now,
                    details={"status": run.status, "running_minutes": minutes},
                )
                self.db.add(alert)
                await self.db.flush()
                all_keys.add(key)
                open_by_key[key] = alert
                to_notify.append(alert)
                created += 1

        # ---- missing schedule ----
        prev_fire = None
        has_scheduled_run = False
        if target.notify_on_missing and target.schedule_cron:
            prev_fire = _previous_fire_time(target.schedule_cron, target.timezone, now)
            if prev_fire and _in_monitor_window(prev_fire, target):
                grace_end = prev_fire + timedelta(minutes=max(int(target.grace_minutes or 30), 0))
                has_scheduled_run = self._schedule_fulfilled(runs, prev_fire)
                if now >= grace_end and not has_scheduled_run:
                    key = f"missing:{wf.id}:{prev_fire.isoformat()}"
                    existing = open_by_key.get(key)
                    if existing is None:
                        # 同一 workflow 已存在未恢复的 missing 告警时复用，
                        # 避免每天同一个未触发问题重复生成新告警。
                        existing = next(
                            (
                                a
                                for a in open_by_key.values()
                                if a.alert_type == "missing"
                                and a.release_workflow_id == wf.id
                                and a.status != "resolved"
                            ),
                            None,
                        )
                    if existing:
                        existing.last_detected_at = now
                        existing.message = (
                            f"期望触发时间 {prev_fire.isoformat()}（{target.timezone}），"
                            f"已超过 {target.grace_minutes} 分钟容忍窗口，未发现 event=schedule 的运行记录。"
                        )
                        existing.details = {
                            "schedule_cron": target.schedule_cron,
                            "expected_fire_at": prev_fire.isoformat(),
                            "grace_minutes": target.grace_minutes,
                        }
                        self._maybe_collect_notification(existing, target, now, to_notify)
                    elif key not in all_keys:
                        alert = ImageBuildAlert(
                            target_id=target.id,
                            release_workflow_id=wf.id,
                            github_run_id=None,
                            alert_type="missing",
                            severity="critical",
                            status="open",
                            title=f"定时镜像构建未启动：{wf.workflow_name}",
                            message=(
                                f"期望触发时间 {prev_fire.isoformat()}（{target.timezone}），"
                                f"已超过 {target.grace_minutes} 分钟容忍窗口，未发现 event=schedule 的运行记录。"
                            ),
                            run_url=None,
                            dedupe_key=key,
                            first_detected_at=now,
                            last_detected_at=now,
                            details={
                                "schedule_cron": target.schedule_cron,
                                "expected_fire_at": prev_fire.isoformat(),
                                "grace_minutes": target.grace_minutes,
                            },
                        )
                        self.db.add(alert)
                        await self.db.flush()
                        all_keys.add(key)
                        open_by_key[key] = alert
                        to_notify.append(alert)
                        created += 1

        # ---- resolve ----
        resolved = 0
        for alert in open_alerts:
            if alert.status == "resolved":
                continue
            if alert.alert_type == "failure":
                alert_first = _as_utc(alert.first_detected_at)
                failure_run = next(
                    (r for r in runs if r.github_run_id == alert.github_run_id), None
                )
                baseline = (
                    _as_utc(failure_run.created_at_remote)
                    if failure_run is not None
                    else alert_first
                )
                alert_branch = alert.branch or (
                    failure_run.branch if failure_run is not None else None
                )
                recovered = any(
                    (run.status or "").lower() in SUCCESS_STATUSES
                    and _as_utc(run.created_at_remote) is not None
                    and baseline is not None
                    and _as_utc(run.created_at_remote) > baseline
                    and (not alert_branch or run.branch == alert_branch)
                    for run in runs
                )
                if recovered:
                    self._resolve(alert, now)
                    resolved += 1
            elif alert.alert_type == "stuck":
                run = next(
                    (r for r in runs if r.github_run_id == alert.github_run_id), None
                )
                if run is not None and (run.status or "").lower() not in ACTIVE_STATUSES:
                    self._resolve(alert, now)
                    resolved += 1
            elif alert.alert_type == "missing":
                details = alert.details or {}
                expected = details.get("expected_fire_at")
                if expected:
                    try:
                        expected_dt = _as_utc(datetime.fromisoformat(expected))
                    except ValueError:
                        expected_dt = None
                    if expected_dt and self._schedule_fulfilled(runs, expected_dt):
                        self._resolve(alert, now)
                        resolved += 1

        # 对仍未恢复且超过冷却时间的告警做重复提醒（即使对应 run 已超出扫描窗口）。
        notified_ids = {alert.id for alert in to_notify if alert.id}
        for alert in open_alerts:
            if alert.status == "resolved" or alert.id in notified_ids:
                continue
            before = len(to_notify)
            self._maybe_collect_notification(alert, target, now, to_notify)
            if len(to_notify) > before:
                notified_ids.add(alert.id)

        target.last_checked_at = now
        await self.db.flush()

        notified = 0
        if to_notify:
            notified = await self._send_alerts_email(target, wf, to_notify)

        await self.db.commit()
        return {"created": created, "notified": notified, "resolved": resolved}

    @staticmethod
    def _maybe_collect_notification(
        alert: ImageBuildAlert,
        target: ImageBuildMonitorTarget,
        now: datetime,
        bucket: list[ImageBuildAlert],
    ) -> None:
        if alert.status != "open":
            return
        last = _as_utc(alert.last_notified_at)
        cooldown = timedelta(minutes=max(int(target.cooldown_minutes or 60), 5))
        if alert.notified_at is None or last is None or now - last >= cooldown:
            bucket.append(alert)

    @staticmethod
    def _schedule_fulfilled(runs: list[ReleaseBuildRun], expected: datetime) -> bool:
        """定时任务是否已完成。

        被取消的定时运行不算完成；但如果取消后有人工重新触发的新运行，
        则视为调度已被后续运行接管，避免误报“定时未触发”。
        """
        scheduled = [
            run
            for run in runs
            if (run.event or "").lower() == "schedule"
            and _as_utc(run.created_at_remote) is not None
            and _as_utc(run.created_at_remote) >= expected - timedelta(minutes=5)
        ]
        if not scheduled:
            return False
        if any((run.status or "").lower() not in CANCELLED_STATUSES for run in scheduled):
            return True
        latest = max(scheduled, key=lambda run: _as_utc(run.created_at_remote))
        latest_at = _as_utc(latest.created_at_remote)
        return any(
            (run.status or "").lower() not in CANCELLED_STATUSES
            and _as_utc(run.created_at_remote) is not None
            and latest_at is not None
            and _as_utc(run.created_at_remote) > latest_at
            for run in runs
        )

    @staticmethod
    def _resolve(alert: ImageBuildAlert, now: datetime) -> None:
        alert.status = "resolved"
        alert.resolved_at = now

    @staticmethod
    def _failure_message(wf: ReleaseWorkflow, run: ReleaseBuildRun) -> str:
        summary = run.conclusion_summary
        return (
            f"run #{run.github_run_id} 构建失败（状态 {run.status}，分支 {run.branch or '-'}，"
            f"event {run.event or '-'}）。Job 汇总：{summary or '-'}"
        )

    async def build_alert_email(
        self,
        target: ImageBuildMonitorTarget,
        wf: ReleaseWorkflow,
        alerts: list[ImageBuildAlert],
    ) -> tuple[str, str]:
        type_zh = {"failure": "构建失败", "missing": "定时未触发", "stuck": "构建卡住"}
        subject = (
            f"[vLLM Ascend 镜像构建告警] {wf.repo_name}/{wf.workflow_name} - "
            + "/".join(sorted({type_zh.get(a.alert_type, a.alert_type) for a in alerts}))
        )
        type_color = {"failure": "#cf1322", "missing": "#c41d7f", "stuck": "#d46b08"}
        window_label = _window_label(target)

        rows_data: list[dict[str, Any]] = []
        aggregate = {"total": 0, "success": 0, "failure": 0, "skipped": 0, "other": 0}
        for index, alert in enumerate(alerts):
            run = await self._run_for_alert(alert) if alert.github_run_id else None
            summary = self._parse_job_summary(run.conclusion_summary) if run else {}
            failed_jobs: list[dict[str, Any]] = []
            if alert.alert_type == "failure" and run is not None:
                failed_jobs = await self._failed_jobs_for_run(
                    wf, run, allow_remote=index < 3
                )
            if run is not None and alert.alert_type == "failure":
                for key in aggregate:
                    try:
                        aggregate[key] += int(summary.get(key) or 0)
                    except (TypeError, ValueError):
                        continue

            if alert.alert_type == "failure":
                if failed_jobs:
                    error_parts = []
                    for job in failed_jobs[:8]:
                        steps = "、".join(
                            str(step.get("name") or "-")
                            for step in (job.get("failed_steps") or [])
                        )
                        part = f"{job.get('name') or '未命名 Job'}[{job.get('status') or '-'}]"
                        if steps:
                            part += f" 失败步骤：{steps}"
                        error_parts.append(part)
                    if len(failed_jobs) > 8:
                        error_parts.append(f"…其余 {len(failed_jobs) - 8} 个失败 Job")
                    error_text = "；".join(error_parts)
                else:
                    error_text = alert.message or alert.title
            else:
                error_text = alert.message or alert.title

            sort_dt = (
                _as_utc(run.created_at_remote) if run is not None else None
            ) or _as_utc(alert.first_detected_at)
            if run is None and alert.alert_type == "missing":
                expected_raw = (alert.details or {}).get("expected_fire_at")
                try:
                    expected_dt = _as_utc(datetime.fromisoformat(expected_raw))
                except (TypeError, ValueError):
                    expected_dt = None
                if expected_dt:
                    sort_dt = expected_dt
            rows_data.append(
                {
                    "alert": alert,
                    "run": run,
                    "summary": summary,
                    "error_text": error_text,
                    "sort_dt": sort_dt,
                    "started_at": sort_dt,
                }
            )

        rows_data.sort(
            key=lambda item: item["sort_dt"]
            or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

        type_counts = {
            key: sum(1 for alert in alerts if alert.alert_type == key)
            for key in ("failure", "missing", "stuck")
        }
        html = await self._render_alert_email(
            target, wf, rows_data, type_counts, aggregate, window_label
        )
        return subject, html

    async def _render_alert_email(
        self,
        target: ImageBuildMonitorTarget,
        wf: ReleaseWorkflow,
        rows_data: list[dict[str, Any]],
        type_counts: dict[str, int],
        aggregate: dict[str, int],
        window_label: str,
    ) -> str:
        type_zh = {"failure": "构建失败", "missing": "定时未触发", "stuck": "构建卡住"}
        type_color = {"failure": "#cf1322", "missing": "#c41d7f", "stuck": "#d46b08"}

        def stat_badge(summary: dict[str, Any], icon: str, label: str, key: str, color: str) -> str:
            try:
                value = int(summary.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            return (
                f'<span title="{label}" style="display:inline-block;white-space:nowrap;'
                f'margin-right:7px;color:{color};font-weight:600;">{icon} {value}</span>'
            )

        table_rows: list[str] = []
        for item in rows_data:
            alert = item["alert"]
            summary = item["summary"]
            run = item["run"]
            type_label = type_zh.get(alert.alert_type, alert.alert_type)
            color = type_color.get(alert.alert_type, "#666")
            started = html_lib.escape(
                item["started_at"]
                .astimezone(ZoneInfo(target.timezone or "Asia/Shanghai"))
                .strftime("%Y-%m-%d %H:%M")
                if item["started_at"]
                else "-"
            )
            branch = html_lib.escape(alert.branch or (run.branch if run else None) or "-")
            event = html_lib.escape(alert.event or (run.event if run else None) or "-")
            error_html = html_lib.escape(item["error_text"] or "-")
            run_url = html_lib.escape(alert.run_url or "")
            link_html = (
                f'<a href="{run_url}" style="color:#1677ff;white-space:nowrap;">查看 Run</a>'
                if run_url
                else '<span style="color:#999;">-</span>'
            )
            job_html = (
                stat_badge(summary, "📦", "总 Job", "total", "#595959")
                + stat_badge(summary, "✅", "成功", "success", "#389e0d")
                + stat_badge(summary, "❌", "失败", "failure", "#cf1322")
                + stat_badge(summary, "⏭", "跳过", "skipped", "#8c8c8c")
                + stat_badge(summary, "➖", "其他", "other", "#d46b08")
            )
            table_rows.append(
                f'<tr style="border-top:1px solid #f0f0f0;">'
                f'<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;">{started}</td>'
                f'<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;"><span style="color:{color};font-weight:600;">{type_label}</span></td>'
                f'<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;">{branch}<br><span style="color:#999;font-size:11px;">{event}</span></td>'
                f'<td style="padding:8px 10px;vertical-align:top;color:#555;max-width:320px;word-break:break-word;">{error_html}</td>'
                f'<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;font-size:12px;">{job_html}</td>'
                f'<td style="padding:8px 10px;white-space:nowrap;vertical-align:top;">{link_html}</td>'
                f"</tr>"
            )

        aggregate_html = (
            f'<span title="总 Job" style="margin-right:10px;">📦 总 {aggregate["total"]}</span>'
            f'<span title="成功" style="margin-right:10px;color:#389e0d;">✅ {aggregate["success"]}</span>'
            f'<span title="失败" style="margin-right:10px;color:#cf1322;">❌ {aggregate["failure"]}</span>'
            f'<span title="跳过" style="margin-right:10px;color:#8c8c8c;">⏭ {aggregate["skipped"]}</span>'
            f'<span title="其他" style="color:#d46b08;">➖ {aggregate["other"]}</span>'
        )
        alert_total = sum(type_counts.values())

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;padding:20px;color:#222;background:#fafafa;">
<div style="max-width:980px;margin:0 auto;">
<h2 style="color:#cf1322;margin-bottom:6px;">⚠ 镜像构建异常告警</h2>
<p style="margin:0 0 10px;color:#555;">仓库：<strong>{html_lib.escape(wf.repo_owner)}/{html_lib.escape(wf.repo_name)}</strong>　Workflow：<strong>{html_lib.escape(wf.workflow_name)}</strong>　统计区间：<strong>{html_lib.escape(window_label)}</strong></p>
<div style="background:#fff1f0;border:1px solid #ffccc7;border-radius:6px;padding:10px 14px;margin-bottom:14px;font-size:13px;color:#555;">
  共 <strong style="color:#cf1322;">{alert_total}</strong> 条异常：
  构建失败 <strong>{type_counts["failure"]}</strong>，
  定时未触发 <strong>{type_counts["missing"]}</strong>，
  构建卡住 <strong>{type_counts["stuck"]}</strong>。
  <div style="margin-top:6px;color:#888;">失败运行 Job 合计：{aggregate_html}</div>
</div>
<table style="width:100%;border-collapse:collapse;background:#fff;font-size:12px;border:1px solid #f0f0f0;">
  <thead>
    <tr style="background:#fafafa;color:#888;text-align:left;">
      <th style="padding:8px 10px;font-weight:600;">启动时间（倒序）</th>
      <th style="padding:8px 10px;font-weight:600;">类型</th>
      <th style="padding:8px 10px;font-weight:600;">分支 / Event</th>
      <th style="padding:8px 10px;font-weight:600;">错误概况</th>
      <th style="padding:8px 10px;font-weight:600;">Job 统计</th>
      <th style="padding:8px 10px;font-weight:600;">链接</th>
    </tr>
  </thead>
  <tbody>
    {''.join(table_rows)}
  </tbody>
</table>
<p style="color:#999;font-size:12px;margin-top:14px;">按启动时间倒序排列；同一次运行仅汇总为一行，可在 GitHub Run 中查看完整日志。vLLM Ascend Dashboard 镜像构建监控自动发送</p>
</div>
</body></html>"""
        return html

    async def _send_alerts_email(
        self,
        target: ImageBuildMonitorTarget,
        wf: ReleaseWorkflow,
        alerts: list[ImageBuildAlert],
    ) -> int:
        if not alerts:
            return 0
        recipients = _normalize_emails(target.notify_emails)
        now = datetime.now(UTC)
        if not recipients:
            for alert in alerts:
                alert.last_notified_at = now
                alert.notification_success = False
                alert.notification_error = "未配置收件人邮箱"
            return 0

        dispatcher = NotificationDispatcher(self.db)
        smtp_config = await dispatcher.get_email_config()
        if not smtp_config or not smtp_config.get("smtp_host"):
            for alert in alerts:
                alert.last_notified_at = now
                alert.notification_success = False
                alert.notification_error = (
                    "SMTP 未配置：请先在「管理 → 通知服务」填写 SMTP 服务器、"
                    "端口、账号和发件人邮箱后再测试"
                )
            return 0

        subject, html = await self.build_alert_email(target, wf, alerts)

        severity = "critical" if any(a.severity == "critical" for a in alerts) else "warning"
        sms_text = (
            f"【镜像构建告警】{wf.workflow_name} 共 {len(alerts)} 条："
            + "；".join(a.title for a in alerts[:2])
        )[:180]
        try:
            result = await dispatcher.dispatch(
                email_subject=subject,
                email_html=html,
                email_recipients=recipients,
                config=smtp_config,
                sms_text=sms_text,
                channels=await dispatcher.resolve_channels(severity),
                source="image_build_monitor",
                event_type="/".join(sorted({a.alert_type for a in alerts})),
                severity=severity,
                title=f"{wf.repo_name}/{wf.workflow_name} 镜像构建告警",
                summary="；".join(a.title for a in alerts),
                payload={
                    "workflow_id": wf.id,
                    "alert_ids": [a.id for a in alerts],
                    "run_ids": [a.github_run_id for a in alerts if a.github_run_id],
                },
                dedupe_key=(
                    f"image_build_monitor:{wf.id}:"
                    + ",".join(str(a.id) for a in sorted(alerts, key=lambda item: item.id or 0))
                ),
            )
            success = bool(result.get("success"))
            error = result.get("error")
        except Exception as exc:  # noqa: BLE001
            success = False
            error = str(exc)

        for alert in alerts:
            alert.notified_at = alert.notified_at or now
            alert.last_notified_at = now
            alert.notification_success = success
            alert.notification_error = None if success else error
        if success:
            return len(alerts)
        logger.warning("Image build alert email failed for workflow %s: %s", wf.id, error)
        return 0

    async def preview_test_email(self, target_id: int | None = None) -> dict[str, Any]:
        """构建一封示例告警邮件 HTML，不发送、不落库，用于前端预览样式。"""
        target = await self.db.get(ImageBuildMonitorTarget, target_id) if target_id else None
        wf = (
            await self.db.get(ReleaseWorkflow, target.release_workflow_id)
            if target
            else None
        )
        if wf is None:
            wf = (
                await self.db.execute(
                    select(ReleaseWorkflow)
                    .where(
                        (ReleaseWorkflow.workflow_name.ilike("%image%"))
                        | (ReleaseWorkflow.workflow_path.ilike("%image%"))
                    )
                    .order_by(ReleaseWorkflow.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if wf is None:
            wf = (await self.db.execute(select(ReleaseWorkflow).limit(1))).scalar_one_or_none()
        if wf is None:
            wf = ReleaseWorkflow(
                repo_owner=settings.GITHUB_OWNER,
                repo_name=settings.GITHUB_REPO,
                workflow_name="Image Build Monitor Preview",
                workflow_path=".github/workflows/schedule_image_build_and_push.yaml",
                workflow_id=0,
            )
        if target is None:
            target = ImageBuildMonitorTarget(
                release_workflow_id=wf.id or 0,
                notify_emails=[],
                cooldown_minutes=60,
                timezone="Asia/Shanghai",
                notify_window_enabled=True,
                notify_window_start="20:00",
                notify_window_end="08:00",
            )

        now = datetime.now(UTC)

        def make_alert(
            alert_type: str,
            title: str,
            message: str,
            run_id: int | None,
            branch: str,
            event: str,
            severity: str = "critical",
            details: dict[str, Any] | None = None,
        ) -> ImageBuildAlert:
            return ImageBuildAlert(
                target_id=target.id or 0,
                release_workflow_id=wf.id or 0,
                github_run_id=run_id,
                alert_type=alert_type,
                severity=severity,
                status="open",
                title=title,
                message=message,
                run_url=(
                    f"https://github.com/{wf.repo_owner}/{wf.repo_name}/actions/runs/{run_id}"
                    if run_id
                    else None
                ),
                branch=branch,
                event=event,
                dedupe_key=f"preview:{alert_type}:{run_id or alert_type}",
                first_detected_at=now,
                last_detected_at=now,
                details=details or {},
            )

        failure = make_alert(
            "failure",
            f"镜像构建失败：{wf.workflow_name}",
            "run #45732960642 构建失败（分支 main，event workflow_dispatch）。",
            45732960642,
            "main",
            "workflow_dispatch",
        )
        missing = make_alert(
            "missing",
            f"定时镜像构建未启动：{wf.workflow_name}",
            "期望触发时间已超过容忍窗口，未发现 event=schedule 的运行记录。",
            None,
            "",
            "schedule",
            details={"expected_fire_at": now.isoformat(), "grace_minutes": 30},
        )
        stuck = make_alert(
            "stuck",
            f"镜像构建长时间未完成：{wf.workflow_name}",
            "run #45728000001 已持续 210 分钟（状态 in_progress），超过阈值 180 分钟。",
            45728000001,
            "releases/v0.32.0rc",
            "schedule",
            severity="warning",
        )
        failure_summary = {"total": 40, "success": 31, "failure": 7, "skipped": 0, "other": 2}
        rows_data = [
            {
                "alert": failure,
                "run": None,
                "summary": failure_summary,
                "error_text": (
                    "a2-openeuler[failure] 失败步骤：Build and push；"
                    "a5-openeuler[failure] 失败步骤：Build and push；"
                    "collect-build-info[failure] 失败步骤：Assemble info"
                ),
                "sort_dt": now,
                "started_at": now,
            },
            {
                "alert": stuck,
                "run": None,
                "summary": {},
                "error_text": stuck.message,
                "sort_dt": now - timedelta(hours=2),
                "started_at": now - timedelta(hours=2),
            },
            {
                "alert": missing,
                "run": None,
                "summary": {},
                "error_text": missing.message,
                "sort_dt": now - timedelta(hours=5),
                "started_at": now - timedelta(hours=5),
            },
        ]
        type_counts = {"failure": 1, "missing": 1, "stuck": 1}
        aggregate = dict(failure_summary)
        html = await self._render_alert_email(
            target, wf, rows_data, type_counts, aggregate, _window_label(target)
        )
        subject = (
            f"[vLLM Ascend 镜像构建告警] {wf.repo_name}/{wf.workflow_name} - "
            "构建失败/定时未触发/构建卡住（预览）"
        )
        return {"subject": subject, "html": html, "window": _window_label(target)}
