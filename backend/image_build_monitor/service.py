"""镜像构建监控与通知服务。

基于发布件管理已采集的 release_workflows / release_build_runs 数据做规则判定，
不额外调用 GitHub API。异常按 failure / missing / stuck 三类落库并通过邮件通知。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.core.email import get_smtp_config, send_email
from infrastructure.persistence.models import (
    ImageBuildAlert,
    ImageBuildMonitorTarget,
    ReleaseBuildRun,
    ReleaseWorkflow,
)

logger = logging.getLogger(__name__)

FAILED_STATUSES = {"failure", "timed_out", "startup_failure", "cancelled"}
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
                        ImageBuildAlert.status == "open",
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
            "stuck_threshold_minutes": target.stuck_threshold_minutes,
            "cooldown_minutes": target.cooldown_minutes,
            "last_checked_at": _iso(target.last_checked_at),
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

    async def get_target_runs(self, target_id: int, limit: int = 20) -> list[dict[str, Any]]:
        target = await self.db.get(ImageBuildMonitorTarget, target_id)
        if not target:
            return []
        wf = await self.db.get(ReleaseWorkflow, target.release_workflow_id)
        if not wf:
            return []
        runs = (
            await self.db.execute(
                select(ReleaseBuildRun)
                .where(ReleaseBuildRun.workflow_id == wf.id)
                .order_by(ReleaseBuildRun.created_at_remote.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
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
                "url": _run_url(wf, run),
            }
            for run in runs
        ]

    # ------------------------------------------------------------------
    # Alert queries
    # ------------------------------------------------------------------

    async def list_alerts(
        self,
        *,
        status: str | None = None,
        alert_type: str | None = None,
        release_workflow_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        conditions = []
        if status:
            conditions.append(ImageBuildAlert.status == status)
        if alert_type:
            conditions.append(ImageBuildAlert.alert_type == alert_type)
        if release_workflow_id:
            conditions.append(ImageBuildAlert.release_workflow_id == release_workflow_id)

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
                            ImageBuildAlert.status == "open",
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
            "open_alerts": by_status.get("open", 0),
            "by_type": by_type,
            "by_status": by_status,
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
        for target, wf in targets:
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
                        summary["errors"].append(f"{wf.workflow_name} 同步 GitHub 失败: {exc}")
                stats = await self._evaluate_target(target, wf)
                summary["created"] += stats["created"]
                summary["notified"] += stats["notified"]
                summary["resolved"] += stats["resolved"]
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Image build monitor evaluation failed for workflow %s: %s",
                    wf.id,
                    exc,
                    exc_info=True,
                )
                summary["errors"].append(f"{wf.workflow_name}: {exc}")
                await self.db.rollback()
        return summary

    async def _sync_recent_runs(self, wf: ReleaseWorkflow, days_back: int = 2) -> int:
        """通过真实 GitHub API 拉取目标 workflow 最近运行并落库。"""
        client = GitHubClient(
            token=settings.GITHUB_TOKEN,
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

        all_keys = {
            key
            for key in (
                await self.db.execute(
                    select(ImageBuildAlert.dedupe_key).where(
                        ImageBuildAlert.target_id == target.id
                    )
                )
            ).scalars().all()
        }

        created = 0
        to_notify: list[ImageBuildAlert] = []

        # ---- failure ----
        if target.notify_on_failure:
            for run in runs:
                status = (run.status or "").lower()
                created_at = _as_utc(run.created_at_remote)
                if status not in FAILED_STATUSES or not created_at or created_at < failure_since:
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
            if prev_fire:
                grace_end = prev_fire + timedelta(minutes=max(int(target.grace_minutes or 30), 0))
                has_scheduled_run = any(
                    (run.event or "").lower() == "schedule"
                    and _as_utc(run.created_at_remote) is not None
                    and _as_utc(run.created_at_remote) >= prev_fire - timedelta(minutes=5)
                    for run in runs
                )
                if now >= grace_end and not has_scheduled_run:
                    key = f"missing:{wf.id}:{prev_fire.isoformat()}"
                    existing = open_by_key.get(key)
                    if existing:
                        existing.last_detected_at = now
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
                recovered = any(
                    (run.status or "").lower() in SUCCESS_STATUSES
                    and _as_utc(run.created_at_remote) is not None
                    and alert_first is not None
                    and _as_utc(run.created_at_remote) > alert_first
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
                    if expected_dt and any(
                        (run.event or "").lower() == "schedule"
                        and _as_utc(run.created_at_remote) is not None
                        and _as_utc(run.created_at_remote) >= expected_dt
                        for run in runs
                    ):
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
        last = _as_utc(alert.last_notified_at)
        cooldown = timedelta(minutes=max(int(target.cooldown_minutes or 60), 5))
        if alert.notified_at is None or last is None or now - last >= cooldown:
            bucket.append(alert)

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

        smtp_config = await get_smtp_config(self.db)
        if not smtp_config or not smtp_config.get("smtp_host"):
            for alert in alerts:
                alert.last_notified_at = now
                alert.notification_success = False
                alert.notification_error = (
                    "SMTP 未配置：请先在「管理 → 邮件服务」填写 SMTP 服务器、"
                    "端口、账号和发件人邮箱后再测试"
                )
            return 0

        type_zh = {"failure": "构建失败", "missing": "定时未触发", "stuck": "构建卡住"}
        subject = (
            f"[vLLM Ascend 镜像构建告警] {wf.repo_name}/{wf.workflow_name} - "
            + "/".join(sorted({type_zh.get(a.alert_type, a.alert_type) for a in alerts}))
        )
        rows = []
        for alert in alerts:
            run_link = (
                f'<a href="{alert.run_url}">{alert.run_url}</a>' if alert.run_url else "-"
            )
            rows.append(
                f"""
                <tr>
                  <td style="padding:8px;border:1px solid #eee;">{type_zh.get(alert.alert_type, alert.alert_type)}</td>
                  <td style="padding:8px;border:1px solid #eee;">{alert.title}</td>
                  <td style="padding:8px;border:1px solid #eee;">{alert.message or '-'}</td>
                  <td style="padding:8px;border:1px solid #eee;">{alert.branch or '-'}</td>
                  <td style="padding:8px;border:1px solid #eee;">{run_link}</td>
                  <td style="padding:8px;border:1px solid #eee;">{_iso(alert.first_detected_at) or '-'}</td>
                </tr>
                """
            )

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;padding:20px;">
<h2 style="color:#cf1322;">⚠ 镜像构建异常告警</h2>
<p>仓库：<strong>{wf.repo_owner}/{wf.repo_name}</strong>　Workflow：<strong>{wf.workflow_name}</strong></p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<thead><tr style="background:#fafafa;">
<th style="padding:8px;border:1px solid #eee;">类型</th>
<th style="padding:8px;border:1px solid #eee;">标题</th>
<th style="padding:8px;border:1px solid #eee;">详情</th>
<th style="padding:8px;border:1px solid #eee;">分支</th>
<th style="padding:8px;border:1px solid #eee;">Run</th>
<th style="padding:8px;border:1px solid #eee;">首次发现</th>
</tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<p style="color:#888;font-size:12px;">vLLM Ascend Dashboard 镜像构建监控自动发送</p>
</body></html>"""

        try:
            result = await send_email(
                subject=subject,
                html_content=html,
                recipients=recipients,
                smtp_host=smtp_config.get("smtp_host", ""),
                smtp_port=smtp_config.get("smtp_port", 587),
                smtp_username=smtp_config.get("smtp_username", ""),
                smtp_password=smtp_config.get("smtp_password", ""),
                smtp_use_tls=smtp_config.get("smtp_use_tls", True),
                from_email=smtp_config.get("from_email") or smtp_config.get("report_from_email", ""),
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
