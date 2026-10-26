"""Durable raw-env store and all-or-nothing hourly synchronisation."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from typing import ClassVar

from sqlalchemy import bindparam, or_, select, text

from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal
from infrastructure.github_run_metadata import workflow_file_from_ref
from infrastructure.persistence.models import CIJob, CIResult, PullRequest, WorkflowConfig
from resource_dashboard.npu_occupancy_client import NpuOccupancyClient
from resource_dashboard.npu_occupancy_config import resolve_pool
from resource_dashboard.npu_occupancy_service import (
    OccupancyRecord,
    enrich_records_with_runs,
    normalize_records,
    parse_timestamp,
)

logger = logging.getLogger(__name__)


def is_stale_sync(updated_at: datetime | None, *, now: datetime, lease_seconds: int) -> bool:
    """Return whether a durable running marker can safely be taken over."""
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return now - updated_at.astimezone(UTC) >= timedelta(seconds=lease_seconds)


def _workflow_names_by_run_id(jobs: list[CIJob]) -> dict[int, set[str]]:
    """Return only real top-level workflow names recorded for each Run."""
    names_by_run_id: dict[int, set[str]] = {}
    for job in jobs:
        workflow_name = (job.workflow_name or "").strip()
        if workflow_name:
            names_by_run_id.setdefault(job.run_id, set()).add(workflow_name)
    return names_by_run_id


class NpuOccupancyRepository:
    # Reads are served from the same persisted snapshot until a successful sync
    # publishes a newer snapshot.  This avoids parsing ~90k raw JSON rows for
    # each concurrently requested trend, analysis, and details endpoint.
    # Keyed by snapshot + window + pool selection.  TTL keeps this cache
    # bounded even if a scheduler is unavailable and its snapshot never moves.
    _records_cache: ClassVar[dict[tuple[datetime, datetime, datetime, tuple[str, ...]], tuple[float, list[OccupancyRecord], list[OccupancyRecord] | None]]] = {}
    _records_cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _metadata_backfill_task: ClassVar[asyncio.Task[None] | None] = None

    @staticmethod
    def _indexed_env_values(env: dict, snapshot_at: datetime) -> dict[str, object]:
        """Build indexed columns from the same normalization rules as page reads."""
        records = normalize_records([env], snapshot_at)
        if records:
            record = records[0]
            return {
                "pod_name": str(env.get("name") or "").strip() or None,
                "pool": record.pool,
                "effective_start_at": record.created_at,
                "effective_end_at": record.effective_end,
            }
        cluster = str(env.get("cluster") or "")
        pool = resolve_pool(cluster)
        return {
            "pod_name": str(env.get("name") or "").strip() or None,
            "pool": pool.name if pool else f"未配置:{cluster or '未知资源池'}",
            "effective_start_at": parse_timestamp(env.get("created_at")),
            "effective_end_at": parse_timestamp(env.get("expires_at")),
        }

    async def _backfill_workflow_metadata(self, records: list[OccupancyRecord]) -> None:
        """Persist missing GitHub Run metadata for unique Run IDs in the background."""
        candidates = {
            record.workflow_run_id: (record.organization, record.repository)
            for record in records
            if record.workflow_run_id and record.organization and record.repository
        }
        if not candidates or not settings.GITHUB_TOKEN:
            return
        # Failed lookups are not a durable cache entry.  Their repository
        # identity may have been repaired since the last attempt.
        query = text("SELECT run_id FROM github_workflow_run_metadata WHERE fetch_status='success' AND run_id IN :run_ids").bindparams(
            bindparam("run_ids", expanding=True)
        )
        async with SessionLocal() as db:
            cached = set((await db.scalars(query, {"run_ids": list(candidates)})).all())
        pending = [(run_id, organization, repository) for run_id, (organization, repository) in candidates.items() if run_id not in cached]
        # Bound work by unique workflow_run_id, never by occupancy row. Direct
        # run lookups also work for older runs that are no longer in GitHub's
        # most recent list pages.
        pending.sort(key=lambda item: item[0], reverse=True)
        pending = pending[:settings.NPU_OCCUPANCY_METADATA_BACKFILL_BATCH_SIZE]
        by_repository: dict[tuple[str, str], set[int]] = {}
        for run_id, organization, repository in pending:
            by_repository.setdefault((organization, repository), set()).add(run_id)

        async def persist(run_id: int, organization: str, repository: str, run: dict | None, error: Exception | None = None) -> None:
            if run is not None:
                values = {"run_id": run_id, "organization": organization, "repository": repository,
                    "workflow_name": run.get("name"), "display_title": run.get("display_title"), "run_number": run.get("run_number"),
                    "event": run.get("event"), "head_branch": run.get("head_branch"), "status": run.get("status"),
                    "conclusion": run.get("conclusion"), "html_url": run.get("html_url")}
                statement = text("""INSERT INTO github_workflow_run_metadata
                    (run_id,organization,repository,workflow_name,display_title,run_number,event,head_branch,status,conclusion,html_url,fetch_status)
                    VALUES (:run_id,:organization,:repository,:workflow_name,:display_title,:run_number,:event,:head_branch,:status,:conclusion,:html_url,'success')
                    ON DUPLICATE KEY UPDATE organization=VALUES(organization), repository=VALUES(repository),
                      workflow_name=VALUES(workflow_name), display_title=VALUES(display_title), run_number=VALUES(run_number),
                      event=VALUES(event), head_branch=VALUES(head_branch), status=VALUES(status), conclusion=VALUES(conclusion),
                      html_url=VALUES(html_url), fetch_status='success', error_message=NULL, fetched_at=UTC_TIMESTAMP()""")
            else:
                values = {"run_id": run_id, "organization": organization, "repository": repository, "error": str(error)[:1000]}
                statement = text("""INSERT INTO github_workflow_run_metadata (run_id,organization,repository,fetch_status,error_message)
                    VALUES (:run_id,:organization,:repository,'failed',:error)
                    ON DUPLICATE KEY UPDATE organization=VALUES(organization), repository=VALUES(repository),
                      fetch_status=IF(fetch_status='success', fetch_status, 'failed'),
                      error_message=IF(fetch_status='success', error_message, VALUES(error_message)),
                      fetched_at=UTC_TIMESTAMP()""")
            async with SessionLocal() as db:
                await db.execute(statement, values)
                await db.commit()

        request_limiter = asyncio.Semaphore(settings.NPU_OCCUPANCY_METADATA_BACKFILL_CONCURRENCY)

        async def fetch_repository(organization: str, repository: str, wanted: set[int]) -> None:
            client = GitHubClient(settings.GITHUB_TOKEN, owner=organization, repo=repository)

            async def fetch_one(run_id: int) -> None:
                try:
                    async with request_limiter:
                        run = await client.get_workflow_run(run_id)
                    await persist(run_id, organization, repository, run)
                except Exception as exc:
                    logger.warning(
                        "NPU occupancy workflow metadata lookup failed for run %s in %s/%s: %s",
                        run_id, organization, repository, exc,
                    )
                    await persist(run_id, organization, repository, None, exc)

            try:
                await asyncio.gather(*(fetch_one(run_id) for run_id in wanted))
            except Exception as exc:
                logger.warning("NPU occupancy workflow metadata backfill failed for %s/%s: %s", organization, repository, exc)
            finally:
                await client.close()

        if pending:
            logger.info("Backfilling GitHub workflow metadata for %d occupancy runs across %d repositories", len(pending), len(by_repository))
            await asyncio.gather(*(fetch_repository(organization, repository, wanted) for (organization, repository), wanted in by_repository.items()))
            type(self)._records_cache.clear()

    def _schedule_workflow_metadata_backfill(self, records: list[OccupancyRecord]) -> None:
        """Schedule one de-duplicated metadata batch without delaying page reads."""
        if not settings.GITHUB_TOKEN or not any(record.workflow_run_id for record in records):
            return
        task = type(self)._metadata_backfill_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._backfill_workflow_metadata(records))
        task.add_done_callback(self._report_backfill_result)
        type(self)._metadata_backfill_task = task

    @staticmethod
    def _report_backfill_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("NPU occupancy workflow metadata background backfill crashed")

    async def sync(self, start: datetime, end: datetime) -> dict:
        task_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        async with SessionLocal() as db:
            state = (
                await db.execute(
                    text(
                        "SELECT status, active_task_id, updated_at "
                        "FROM npu_occupancy_sync_state WHERE id=1 FOR UPDATE"
                    )
                )
            ).mappings().first()
            if state and state["status"] == "running":
                if not is_stale_sync(
                    state["updated_at"],
                    now=now,
                    lease_seconds=settings.NPU_OCCUPANCY_SYNC_LEASE_SECONDS,
                ):
                    logger.info(
                        "NPU occupancy sync already running (task=%s, updated_at=%s)",
                        state["active_task_id"],
                        state["updated_at"],
                    )
                    return {"status": "running"}
                logger.warning(
                    "Taking over stale NPU occupancy sync (task=%s, updated_at=%s)",
                    state["active_task_id"],
                    state["updated_at"],
                )
                await db.execute(
                    text(
                        "UPDATE npu_occupancy_sync_state "
                        "SET status='failed', active_task_id=NULL, "
                        "error_message='previous NPU occupancy sync lease expired', "
                        "updated_at=UTC_TIMESTAMP() WHERE id=1"
                    )
                )
            await db.execute(text("UPDATE npu_occupancy_sync_state SET status='running', active_task_id=:task, error_message=NULL, updated_at=UTC_TIMESTAMP() WHERE id=1"), {"task": task_id})
            await db.commit()
        try:
            logger.info("Starting NPU occupancy sync task=%s for %s to %s", task_id, start, end)
            envs = await asyncio.wait_for(
                asyncio.to_thread(NpuOccupancyClient().fetch_window, start, end),
                timeout=settings.NPU_OCCUPANCY_SYNC_TIMEOUT_SECONDS,
            )
            snapshot_at = datetime.now(UTC)
            records = normalize_records(envs, snapshot_at)
            should_start_backfill = False
            async with SessionLocal() as db:
                for env in envs:
                    env_id = str(env.get("env_id") or "")
                    if not env_id:
                        continue
                    revision = parse_timestamp(env.get("updated_at") or env.get("expires_at") or env.get("created_at")) or snapshot_at
                    indexed = self._indexed_env_values(env, snapshot_at)
                    await db.execute(text("""
                        INSERT INTO npu_occupancy_raw_envs
                            (env_id, source_updated_at, pod_name, pool, effective_start_at, effective_end_at, payload, created_at, updated_at)
                        VALUES
                            (:env_id, :revision, :pod_name, :pool, :effective_start_at, :effective_end_at,
                             CAST(:payload AS JSON), UTC_TIMESTAMP(), UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE
                            source_updated_at=VALUES(source_updated_at),
                            pod_name=VALUES(pod_name),
                            pool=VALUES(pool),
                            effective_start_at=VALUES(effective_start_at),
                            effective_end_at=VALUES(effective_end_at),
                            payload=VALUES(payload), updated_at=UTC_TIMESTAMP()
                    """), {"env_id": env_id, "revision": revision, "payload": json.dumps(env, default=str), **indexed})
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state
                    SET status='ready', last_successful_sync=UTC_TIMESTAMP(), last_snapshot_at=:snapshot,
                        active_task_id=NULL, error_message=NULL, updated_at=UTC_TIMESTAMP()
                    WHERE id=1 AND active_task_id=:task
                """), {"snapshot": snapshot_at, "task": task_id})
                await db.commit()
                task = type(self)._metadata_backfill_task
                should_start_backfill = task is None or task.done()
                run_ids = {record.workflow_run_id for record in records if record.workflow_run_id}
                job_ids = {int(record.job_id) for record in records if record.job_id and record.job_id.isdigit()}
                pr_numbers = {record.pr_number for record in records if record.pr_number}
                workflow_files = {workflow_file_from_ref(record.workflow_ref) for record in records if workflow_file_from_ref(record.workflow_ref)}
                if run_ids or job_ids or pr_numbers or workflow_files:
                    results = list((await db.scalars(
                        select(CIResult).where(CIResult.run_id.in_(run_ids))
                    )).all()) if run_ids else []
                    metadata_rows = (await db.execute(text("""
                        SELECT run_id, workflow_name, display_title, run_number, event, html_url
                        FROM github_workflow_run_metadata WHERE fetch_status='success' AND run_id IN :run_ids
                    """).bindparams(bindparam("run_ids", expanding=True)), {"run_ids": list(run_ids)})).mappings().all() if run_ids else []
                    job_conditions = []
                    if job_ids:
                        job_conditions.append(CIJob.job_id.in_(job_ids))
                    if run_ids:
                        job_conditions.append(CIJob.run_id.in_(run_ids))
                    jobs = list((await db.scalars(
                        select(CIJob).where(or_(*job_conditions))
                    )).all()) if job_conditions else []
                    prs = list((await db.scalars(
                        select(PullRequest).where(PullRequest.pr_number.in_(pr_numbers))
                    )).all()) if pr_numbers else []
                    workflows = list((await db.scalars(
                        select(WorkflowConfig).where(WorkflowConfig.workflow_file.in_(workflow_files))
                    )).all()) if workflow_files else []
                    configured_repo = ((settings.GITHUB_OWNER or "").lower(), (settings.GITHUB_REPO or "").lower())
                    workflow_names_by_run_id = {
                        run_id: next(iter(names))
                        for run_id, names in _workflow_names_by_run_id(jobs).items()
                        if len(names) == 1
                    }
                    result_map = {result.run_id: result for result in results}
                    # The backfilled GitHub Run payload is authoritative for
                    # this exact run and must beat a collector-normalized name.
                    result_map.update({row["run_id"]: SimpleNamespace(**row) for row in metadata_rows})
                    records = enrich_records_with_runs(records, result_map, {job.job_id: job for job in jobs}, {pr.pr_number: pr.title for pr in prs}, {(configured_repo[0], configured_repo[1], workflow.workflow_file): workflow.workflow_name for workflow in workflows}, workflow_names_by_run_id)
            # A new durable snapshot invalidates every bounded read cache.
            type(self)._records_cache.clear()
            if should_start_backfill:
                self._schedule_workflow_metadata_backfill(records)
            return {"status": "ready", "count": len(envs), "snapshot_at": snapshot_at}
        except Exception as exc:
            logger.exception("NPU occupancy sync failed (task=%s)", task_id)
            async with SessionLocal() as db:
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state SET status='failed', active_task_id=NULL,
                    error_message=:error, updated_at=UTC_TIMESTAMP() WHERE id=1 AND active_task_id=:task
                """), {"error": str(exc)[:1000], "task": task_id})
                await db.commit()
            raise

    async def load_envs(
        self, *, start: datetime, end: datetime, pools: list[str] | None = None,
    ) -> tuple[list[dict], datetime | None]:
        """Use indexed time overlap filtering; no historical revision scan."""
        params: dict[str, object] = {"start": start, "end": end}
        query = """
            SELECT payload FROM npu_occupancy_raw_envs
            WHERE effective_start_at < :end AND effective_end_at > :start
        """
        if pools:
            query += " AND pool IN :pools"
            params["pools"] = pools
            statement = text(query).bindparams(bindparam("pools", expanding=True))
        else:
            statement = text(query)
        async with SessionLocal() as db:
            state = (await db.execute(text("SELECT last_snapshot_at FROM npu_occupancy_sync_state WHERE id=1"))).mappings().first()
            rows = (await db.execute(statement, params)).mappings().all()
        envs = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in rows]
        snapshot_at = state["last_snapshot_at"] if state else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=UTC)
        return envs, snapshot_at

    async def _load_records_legacy(self) -> tuple[list[OccupancyRecord], datetime | None]:
        """Load and normalize the current persisted snapshot at most once."""
        async with SessionLocal() as db:
            state = (await db.execute(text("SELECT last_snapshot_at FROM npu_occupancy_sync_state WHERE id=1"))).mappings().first()
        snapshot_at = state["last_snapshot_at"] if state else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=UTC)

        cached = type(self)._records_cache
        if snapshot_at is not None and cached is not None and cached[0] == snapshot_at:
            return cached[1], snapshot_at

        async with self._records_cache_lock:
            # Another request may have finished loading while this one waited.
            cached = type(self)._records_cache
            if snapshot_at is not None and cached is not None and cached[0] == snapshot_at:
                return cached[1], snapshot_at
            envs, loaded_snapshot_at = await self.load_envs()
            effective_snapshot_at = loaded_snapshot_at or datetime.now(UTC)
            records = normalize_records(envs, effective_snapshot_at)
            run_ids = {record.workflow_run_id for record in records if record.workflow_run_id}
            job_ids = {int(record.job_id) for record in records if record.job_id and record.job_id.isdigit()}
            pr_numbers = {record.pr_number for record in records if record.pr_number}
            workflow_files = {workflow_file_from_ref(record.workflow_ref) for record in records if workflow_file_from_ref(record.workflow_ref)}
            if run_ids or job_ids or pr_numbers or workflow_files:
                async with SessionLocal() as db:
                    results = list((await db.scalars(
                        select(CIResult).where(CIResult.run_id.in_(run_ids))
                    )).all()) if run_ids else []
                    metadata_rows = (await db.execute(text("""
                        SELECT run_id, workflow_name, display_title, run_number, event, html_url
                        FROM github_workflow_run_metadata WHERE fetch_status='success' AND run_id IN :run_ids
                    """).bindparams(bindparam("run_ids", expanding=True)), {"run_ids": list(run_ids)})).mappings().all() if run_ids else []
                    job_conditions = []
                    if job_ids:
                        job_conditions.append(CIJob.job_id.in_(job_ids))
                    if run_ids:
                        job_conditions.append(CIJob.run_id.in_(run_ids))
                    jobs = list((await db.scalars(
                        select(CIJob).where(or_(*job_conditions))
                    )).all()) if job_conditions else []
                    prs = list((await db.scalars(
                        select(PullRequest).where(PullRequest.pr_number.in_(pr_numbers))
                    )).all()) if pr_numbers else []
                    workflows = list((await db.scalars(
                        select(WorkflowConfig).where(WorkflowConfig.workflow_file.in_(workflow_files))
                    )).all()) if workflow_files else []
                configured_repo = ((settings.GITHUB_OWNER or "").lower(), (settings.GITHUB_REPO or "").lower())
                workflow_names_by_run_id = {
                    run_id: next(iter(names))
                    for run_id, names in _workflow_names_by_run_id(jobs).items()
                    if len(names) == 1
                }
                result_map = {result.run_id: result for result in results}
                result_map.update({row["run_id"]: SimpleNamespace(**row) for row in metadata_rows})
                records = enrich_records_with_runs(records, result_map, {job.job_id: job for job in jobs}, {pr.pr_number: pr.title for pr in prs}, {(configured_repo[0], configured_repo[1], workflow.workflow_file): workflow.workflow_name for workflow in workflows}, workflow_names_by_run_id)
            if loaded_snapshot_at is not None:
                type(self)._records_cache = (loaded_snapshot_at, records)
            return records, loaded_snapshot_at

    async def _enrich_records(self, records: list[OccupancyRecord]) -> list[OccupancyRecord]:
        """Batch-enrich only records already narrowed to the requested window."""
        run_ids = {record.workflow_run_id for record in records if record.workflow_run_id}
        job_ids = {int(record.job_id) for record in records if record.job_id and record.job_id.isdigit()}
        pr_numbers = {record.pr_number for record in records if record.pr_number}
        workflow_files = {workflow_file_from_ref(record.workflow_ref) for record in records if workflow_file_from_ref(record.workflow_ref)}
        if not (run_ids or job_ids or pr_numbers or workflow_files):
            return records
        async with SessionLocal() as db:
            results = list((await db.scalars(select(CIResult).where(CIResult.run_id.in_(run_ids)))).all()) if run_ids else []
            metadata_rows = (await db.execute(text("""
                SELECT run_id, workflow_name, display_title, run_number, event, html_url
                FROM github_workflow_run_metadata WHERE fetch_status='success' AND run_id IN :run_ids
            """).bindparams(bindparam("run_ids", expanding=True)), {"run_ids": list(run_ids)})).mappings().all() if run_ids else []
            job_conditions = []
            if job_ids:
                job_conditions.append(CIJob.job_id.in_(job_ids))
            if run_ids:
                job_conditions.append(CIJob.run_id.in_(run_ids))
            jobs = list((await db.scalars(select(CIJob).where(or_(*job_conditions)))).all()) if job_conditions else []
            prs = list((await db.scalars(select(PullRequest).where(PullRequest.pr_number.in_(pr_numbers)))).all()) if pr_numbers else []
            workflows = list((await db.scalars(select(WorkflowConfig).where(WorkflowConfig.workflow_file.in_(workflow_files)))).all()) if workflow_files else []
        configured_repo = ((settings.GITHUB_OWNER or "").lower(), (settings.GITHUB_REPO or "").lower())
        workflow_names_by_run_id = {run_id: next(iter(names)) for run_id, names in _workflow_names_by_run_id(jobs).items() if len(names) == 1}
        result_map = {result.run_id: result for result in results}
        result_map.update({row["run_id"]: SimpleNamespace(**row) for row in metadata_rows})
        return enrich_records_with_runs(records, result_map, {job.job_id: job for job in jobs}, {pr.pr_number: pr.title for pr in prs}, {(configured_repo[0], configured_repo[1], workflow.workflow_file): workflow.workflow_name for workflow in workflows}, workflow_names_by_run_id)

    async def load_records(
        self, *, start: datetime, end: datetime, pools: list[str] | None = None, enrich: bool = False,
    ) -> tuple[list[OccupancyRecord], datetime | None]:
        """Read a cached time window; trend reads avoid CI/PR/Workflow joins."""
        async with SessionLocal() as db:
            state = (await db.execute(text("SELECT last_snapshot_at FROM npu_occupancy_sync_state WHERE id=1"))).mappings().first()
        snapshot_at = state["last_snapshot_at"] if state else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=UTC)
        if snapshot_at is None:
            return [], None
        key = (snapshot_at, start, end, tuple(sorted(pools or [])))
        cached = type(self)._records_cache.get(key)
        if cached and monotonic() - cached[0] < settings.NPU_OCCUPANCY_RECORDS_CACHE_TTL_SECONDS:
            result = cached[2] if enrich and cached[2] is not None else cached[1]
            if enrich:
                self._schedule_workflow_metadata_backfill(result)
            return result, snapshot_at
        async with self._records_cache_lock:
            cached = type(self)._records_cache.get(key)
            if cached and monotonic() - cached[0] < settings.NPU_OCCUPANCY_RECORDS_CACHE_TTL_SECONDS:
                if enrich and cached[2] is None:
                    enriched = await self._enrich_records(cached[1])
                    type(self)._records_cache[key] = (cached[0], cached[1], enriched)
                    self._schedule_workflow_metadata_backfill(enriched)
                    return enriched, snapshot_at
                result = cached[2] if enrich and cached[2] is not None else cached[1]
                if enrich:
                    self._schedule_workflow_metadata_backfill(result)
                return result, snapshot_at
            envs, loaded_snapshot_at = await self.load_envs(start=start, end=end, pools=pools)
            records = normalize_records(envs, loaded_snapshot_at or snapshot_at)
            enriched = await self._enrich_records(records) if enrich else None
            type(self)._records_cache[key] = (monotonic(), records, enriched)
            if enrich and enriched is not None:
                self._schedule_workflow_metadata_backfill(enriched)
            return (enriched if enrich else records), loaded_snapshot_at
