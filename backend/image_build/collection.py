"""Collect dashboard-triggered builds and request failed-job reruns."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from image_build import collection_source as source
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import ImageBuildCollectionRun as Collection
from infrastructure.persistence.models import ImageBuildTriggerRecord as Trigger
from workflow_actions.credentials import get_github_token

logger = logging.getLogger(__name__)
_SCAN_INTERVAL_SECONDS = 10
_BATCH_SIZE = 16
_NETWORK_TIMEOUT_SECONDS = 35
_POLL_INTERVAL = timedelta(seconds=20)
_MIN_REFRESH_INTERVAL = timedelta(seconds=5)
_RETRY_INTERVAL = timedelta(seconds=45)
_RERUN_ACK_WINDOW = timedelta(minutes=2)
_PUBLICATION_GRACE = timedelta(minutes=3)


class RerunConflict(RuntimeError):
    pass


class RerunUnavailable(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _db_time(value: Any) -> datetime | None:
    parsed = source.utc_time(value)
    return parsed.replace(tzinfo=None) if parsed else None


def _runs():
    return select(Collection, Trigger).join(Trigger, Trigger.id == Collection.trigger_record_id).options(
        defer(Trigger.dispatch_snapshot),
    )


def _can_rerun(row: Collection) -> bool:
    return (
        row.pending_attempt is None and row.github_status == "completed"
        and row.conclusion in {"failure", "timed_out"} and row.ended_at is not None and row.run_attempt > 0
    )


def run_view(row: Collection, trigger: Trigger, *, detail: bool = False) -> dict[str, Any]:
    waiting_for_rerun = row.pending_attempt is not None
    key = "queued" if waiting_for_rerun else source.result_key(row.github_status, row.conclusion)
    github_url = f"https://github.com/{trigger.repository}/actions/runs/{row.run_id}"
    data = {
        "runId": row.run_id,
        "workflow": row.workflow_name or source.WORKFLOW_NAME,
        "workflowBranch": trigger.workflow_branch,
        "workflowSha": row.workflow_sha,
        "actor": trigger.username,
        "result": source.RESULT_LABELS[key],
        "startedAt": None if waiting_for_rerun else source.iso_time(row.started_at),
        "endedAt": None if waiting_for_rerun else source.iso_time(row.ended_at),
        "githubUrl": github_url,
        "runAttempt": row.pending_attempt or row.run_attempt or None,
        "canRerun": _can_rerun(row),
        "collecting": row.next_collect_at is not None,
        "collectionError": row.last_error,
        "chips": list(dict.fromkeys(item["chip"] for item in row.matrix_json)),
        "publicationStatus": "pending" if waiting_for_rerun else row.publication_status,
    }
    if detail:
        publication = row.publication_json if data["publicationStatus"] == "published" else None
        data.update(matrix=row.matrix_json, publication=publication)
    return data


async def list_runs(db: AsyncSession, page: int, page_size: int) -> dict[str, Any]:
    total = await db.scalar(select(func.count()).select_from(Collection))
    rows = await db.execute(
        _runs().options(defer(Collection.publication_json)).order_by(Collection.run_id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return {"items": [run_view(row, trigger) for row, trigger in rows], "total": total or 0}


async def get_detail(db: AsyncSession, run_id: int) -> dict[str, Any] | None:
    row = (await db.execute(_runs().where(Collection.run_id == run_id))).one_or_none()
    return run_view(row[0], row[1], detail=True) if row else None


async def _discover_runs(db: AsyncSession) -> None:
    result = await db.execute(
        select(Trigger.id, Trigger.run_id).outerjoin(Collection, Collection.run_id == Trigger.run_id)
        .where(Trigger.status == "submitted", Trigger.run_id.is_not(None), Collection.run_id.is_(None))
        .order_by(Trigger.id).limit(_BATCH_SIZE)
    )
    records = result.all()
    if records:
        statement = insert(Collection).values([
            {"trigger_record_id": record_id, "run_id": run_id, "next_collect_at": _now()}
            for record_id, run_id in records
        ])
        await db.execute(statement.on_duplicate_key_update(run_id=Collection.run_id))


async def _update_snapshot(db: AsyncSession, run_id: int, revision: int, values: dict[str, Any]) -> bool:
    result = await db.execute(
        update(Collection).where(Collection.run_id == run_id, Collection.revision == revision)
        .values(**values, revision=Collection.revision + 1)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1  # type: ignore[attr-defined]


def _error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub 采集请求失败（HTTP {exc.response.status_code}）"
    if isinstance(exc, ValueError):
        return "构建采集数据无效，请查看 GitHub Run 或检查 Actions Token"
    return "暂时无法连接 GitHub，采集会重试"


async def _read_snapshot(
    row: Collection, token: str, repository: str, now: datetime,
) -> dict[str, Any]:
    run_url = f"https://github.com/{repository}/actions/runs/{row.run_id}"
    async with source.github_client(token) as client:
        run = await source.get_run(client, repository, row.run_id)
        attempt = run["run_attempt"]
        if attempt < row.run_attempt:
            return {"next_collect_at": now + _POLL_INTERVAL}
        awaiting_attempt = row.pending_attempt is not None and attempt < row.pending_attempt
        if awaiting_attempt and (
            row.rerun_acknowledged
            or (row.rerun_requested_at is not None and now - row.rerun_requested_at < _RERUN_ACK_WINDOW)
        ):
            return {"next_collect_at": now + _POLL_INTERVAL}

        jobs = await source.list_jobs(client, repository, row.run_id)
        completed = run["status"] == "completed"
        ended_at = _db_time(run.get("updated_at")) if completed else None
        publication_status = "pending"
        publication = None
        next_collect_at = now + _POLL_INTERVAL
        error = "GitHub 未确认此次重跑，请检查原 Run" if awaiting_attempt else None
        if completed:
            if source.publication_skipped(jobs, attempt):
                publication_status, next_collect_at = "skipped", None
            else:
                within_grace = ended_at is None or now - ended_at < _PUBLICATION_GRACE
                try:
                    publication = await source.read_publication(client, repository, row.run_id, attempt, run_url)
                except (httpx.HTTPError, ValueError) as exc:
                    error = _error_message(exc)
                    if isinstance(exc, httpx.HTTPError) or within_grace:
                        next_collect_at = now + _RETRY_INTERVAL
                    else:
                        publication_status, next_collect_at = "unavailable", None
                else:
                    if publication is not None:
                        publication_status, next_collect_at = "published", None
                    elif not within_grace:
                        publication_status, next_collect_at = "unavailable", None

        return {
            "workflow_name": (run.get("name") or source.WORKFLOW_NAME)[:255],
            "workflow_sha": run.get("head_sha"),
            "github_status": run["status"], "conclusion": run.get("conclusion"),
            "run_attempt": attempt, "pending_attempt": None,
            "rerun_requested_at": None, "rerun_acknowledged": False,
            "started_at": _db_time(run.get("run_started_at") or run.get("created_at")), "ended_at": ended_at,
            "collected_at": now, "next_collect_at": next_collect_at, "last_error": error,
            "matrix_json": source.build_matrix(jobs, run_url, attempt),
            "publication_status": publication_status, "publication_json": publication,
        }


async def collect_run(run_id: int, *, force: bool = False) -> None:
    async with SessionLocal() as db:
        row_pair = (await db.execute(_runs().where(Collection.run_id == run_id))).one_or_none()
        if row_pair is None:
            return
        row, trigger = row_pair
        now = _now()
        if not force and (row.next_collect_at is None or row.next_collect_at > now):
            return
        if force and row.collected_at is not None and now - row.collected_at < _MIN_REFRESH_INTERVAL:
            return
        # Release the read transaction and its connection before accessing GitHub.
        # Use a separate session to apply the snapshot only if the row is still current.
        revision, repository = row.revision, trigger.repository
        try:
            token = await get_github_token(db)
        except ValueError:
            await _update_snapshot(db, run_id, revision, {
                "last_error": "请先在 Actions 操作中配置 GitHub Token", "next_collect_at": now + _RETRY_INTERVAL,
            })
            return
        db.expunge(row)
        await db.rollback()

    try:
        async with asyncio.timeout(_NETWORK_TIMEOUT_SECONDS):
            values = await _read_snapshot(row, token, repository, now)
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
        values = {"last_error": _error_message(exc), "next_collect_at": now + _RETRY_INTERVAL}
        logger.warning("Image build collection failed run_id=%s error_type=%s", run_id, type(exc).__name__)

    async with SessionLocal() as db:
        await _update_snapshot(db, run_id, revision, values)


async def collect_runs_forever() -> None:
    while True:
        try:
            async with SessionLocal.begin() as db:
                await _discover_runs(db)
                due = list(await db.scalars(
                    select(Collection.run_id).where(Collection.next_collect_at <= _now())
                    .order_by(Collection.next_collect_at, Collection.run_id).limit(_BATCH_SIZE)
                ))
            for run_id in due:
                await collect_run(run_id)
        except Exception:
            logger.exception("Image build collection worker failed")
        await asyncio.sleep(_SCAN_INTERVAL_SECONDS)


async def _record_rerun_response(
    db: AsyncSession, run_id: int, pending_attempt: int, requested_at: datetime, *, accepted: bool,
) -> None:
    await db.execute(
        update(Collection).where(
            Collection.run_id == run_id, Collection.pending_attempt == pending_attempt,
            Collection.rerun_requested_at == requested_at,
        ).values(
            pending_attempt=pending_attempt if accepted else None,
            rerun_requested_at=requested_at if accepted else None,
            rerun_acknowledged=accepted,
            revision=Collection.revision + 1, next_collect_at=_now(),
        ).execution_options(synchronize_session=False)
    )
    await db.commit()


async def rerun_failed_jobs(
    db: AsyncSession, run_id: int, username: str,
) -> Literal["submitted", "unknown"]:
    pair = (await db.execute(_runs().where(Collection.run_id == run_id))).one_or_none()
    if pair is None:
        raise LookupError("构建采集记录不存在")
    row, trigger = pair
    if not _can_rerun(row):
        raise RerunConflict("只有已结束且失败的构建可以重跑")
    repository, revision, observed_attempt = trigger.repository, row.revision, row.run_attempt
    try:
        token = await get_github_token(db)
    except ValueError as exc:
        raise RerunUnavailable("请先在 Actions 操作中配置 GitHub Token") from exc
    await db.rollback()

    async with source.github_client(token) as client:
        try:
            remote = await source.get_run(client, repository, run_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise RerunUnavailable(_error_message(exc)) from exc
        if (
            remote["status"] != "completed" or remote.get("conclusion") not in {"failure", "timed_out"}
            or remote["run_attempt"] != observed_attempt
        ):
            await _update_snapshot(db, run_id, revision, {"next_collect_at": _now()})
            raise RerunConflict("GitHub 上的构建状态已变化，请刷新后重试")

        requested_at = _now()
        pending_attempt = observed_attempt + 1
        if not await _update_snapshot(db, run_id, revision, {
            "pending_attempt": pending_attempt, "rerun_requested_at": requested_at,
            "rerun_acknowledged": False, "next_collect_at": requested_at, "last_error": None,
        }):
            raise RerunConflict("这条构建正在更新，请刷新后重试")

        logger.info("Image build failed-job rerun requested run_id=%s user=%s attempt=%s", run_id, username, pending_attempt)
        try:
            response = await client.post(f"/repos/{repository}/actions/runs/{run_id}/rerun-failed-jobs")
        except httpx.RequestError:
            # Do not submit a second POST if GitHub might have received the first one.
            return "unknown"
        if response.status_code == 201:
            await _record_rerun_response(db, run_id, pending_attempt, requested_at, accepted=True)
            return "submitted"
        if response.status_code >= 500:
            return "unknown"
        if response.status_code >= 400:
            await _record_rerun_response(db, run_id, pending_attempt, requested_at, accepted=False)
            raise RerunUnavailable(f"GitHub 拒绝了失败 Job 重跑（HTTP {response.status_code}）")
        return "unknown"
