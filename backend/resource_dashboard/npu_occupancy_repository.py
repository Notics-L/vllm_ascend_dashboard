"""Durable raw-env store and all-or-nothing hourly synchronisation."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

from sqlalchemy import bindparam, or_, select, text

from infrastructure.core.config import settings
from infrastructure.clients.github_client import GitHubClient
from infrastructure.db.base import SessionLocal
from infrastructure.github_run_metadata import workflow_file_from_ref
from infrastructure.persistence.models import CIJob, CIResult, PullRequest, WorkflowConfig
from resource_dashboard.npu_occupancy_client import NpuOccupancyClient
from resource_dashboard.npu_occupancy_service import (
    OccupancyRecord,
    enrich_records_with_runs,
    normalize_records,
    parse_timestamp,
)

logger = logging.getLogger(__name__)


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
    _records_cache: ClassVar[tuple[datetime, list[OccupancyRecord]] | None] = None
    _records_cache_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _metadata_backfill_task: ClassVar[asyncio.Task[None] | None] = None

    async def _backfill_workflow_metadata(self, records: list[OccupancyRecord]) -> None:
        """Persist missing GitHub Run metadata during sync, never during page reads."""
        candidates = {
            record.workflow_run_id: (record.organization, record.repository)
            for record in records
            if record.workflow_run_id and record.organization and record.repository
        }
        if not candidates or not settings.GITHUB_TOKEN:
            return
        query = text("SELECT run_id FROM github_workflow_run_metadata WHERE run_id IN :run_ids").bindparams(
            bindparam("run_ids", expanding=True)
        )
        async with SessionLocal() as db:
            cached = set((await db.scalars(query, {"run_ids": list(candidates)})).all())
        pending = [(run_id, organization, repository) for run_id, (organization, repository) in candidates.items() if run_id not in cached]
        # GitHub returns runs newest-first.  Work through our candidates in the
        # same order so the next bounded batch advances instead of repeatedly
        # scanning for old runs that are outside the configured page window.
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
                    VALUES (:run_id,:organization,:repository,:workflow_name,:display_title,:run_number,:event,:head_branch,:status,:conclusion,:html_url,'success')""")
            else:
                values = {"run_id": run_id, "organization": organization, "repository": repository, "error": str(error)[:1000]}
                statement = text("""INSERT INTO github_workflow_run_metadata (run_id,organization,repository,fetch_status,error_message)
                    VALUES (:run_id,:organization,:repository,'failed',:error)""")
            async with SessionLocal() as db:
                await db.execute(statement, values)
                await db.commit()

        request_limiter = asyncio.Semaphore(settings.NPU_OCCUPANCY_METADATA_BACKFILL_CONCURRENCY)

        async def fetch_repository(organization: str, repository: str, wanted: set[int]) -> None:
            client = GitHubClient(settings.GITHUB_TOKEN, owner=organization, repo=repository)
            try:
                for page in range(1, settings.NPU_OCCUPANCY_METADATA_BACKFILL_MAX_PAGES_PER_REPOSITORY + 1):
                    async with request_limiter:
                        runs = await client.get_repository_workflow_runs(page=page)
                    matched = [run for run in runs if int(run.get("id") or 0) in wanted]
                    for run in matched:
                        run_id = int(run["id"])
                        await persist(run_id, organization, repository, run)
                        wanted.discard(run_id)
                    if not wanted or len(runs) < 100:
                        break
                # Cache misses as well: without this, runs outside the bounded
                # page window would trigger the same expensive scan on every
                # scheduled sync.
                if wanted:
                    error = LookupError(
                        f"workflow run was not found in the first "
                        f"{settings.NPU_OCCUPANCY_METADATA_BACKFILL_MAX_PAGES_PER_REPOSITORY} pages"
                    )
                    logger.info(
                        "NPU occupancy workflow metadata not found for %d runs in %s/%s",
                        len(wanted), organization, repository,
                    )
                    for run_id in wanted:
                        await persist(run_id, organization, repository, None, error)
            except Exception as exc:
                logger.warning("NPU occupancy workflow metadata backfill failed for %s/%s: %s", organization, repository, exc)
                for run_id in wanted:
                    await persist(run_id, organization, repository, None, exc)
            finally:
                await client.close()

        if pending:
            logger.info("Backfilling GitHub workflow metadata for %d occupancy runs across %d repositories", len(pending), len(by_repository))
            await asyncio.gather(*(fetch_repository(organization, repository, wanted) for (organization, repository), wanted in by_repository.items()))
            type(self)._records_cache = None

    @staticmethod
    def _report_backfill_result(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("NPU occupancy workflow metadata background backfill crashed")

    async def sync(self, start: datetime, end: datetime) -> dict:
        task_id = uuid.uuid4().hex
        async with SessionLocal() as db:
            state = await db.execute(text("SELECT status FROM npu_occupancy_sync_state WHERE id=1 FOR UPDATE"))
            if state.scalar_one_or_none() == "running":
                return {"status": "running"}
            await db.execute(text("UPDATE npu_occupancy_sync_state SET status='running', active_task_id=:task, error_message=NULL, updated_at=UTC_TIMESTAMP() WHERE id=1"), {"task": task_id})
            await db.commit()
        try:
            envs = await asyncio.to_thread(NpuOccupancyClient().fetch_window, start, end)
            snapshot_at = datetime.now(UTC)
            records = normalize_records(envs, snapshot_at)
            should_start_backfill = False
            async with SessionLocal() as db:
                for env in envs:
                    env_id = str(env.get("env_id") or "")
                    if not env_id:
                        continue
                    revision = parse_timestamp(env.get("updated_at") or env.get("expires_at") or env.get("created_at")) or snapshot_at
                    await db.execute(text("""
                        INSERT INTO npu_occupancy_raw_envs (env_id, source_updated_at, payload, created_at, updated_at)
                        VALUES (:env_id, :revision, CAST(:payload AS JSON), UTC_TIMESTAMP(), UTC_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=UTC_TIMESTAMP()
                    """), {"env_id": env_id, "revision": revision, "payload": json.dumps(env, default=str)})
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state
                    SET status='ready', last_successful_sync=UTC_TIMESTAMP(), last_snapshot_at=:snapshot,
                        active_task_id=NULL, error_message=NULL, updated_at=UTC_TIMESTAMP()
                    WHERE id=1 AND active_task_id=:task
                """), {"snapshot": snapshot_at, "task": task_id})
                await db.commit()
                # Include the entire persisted current snapshot, not only the
                # upstream lookback window, so old occupancy rows are also
                # backfilled in bounded batches.
                persisted_envs, _ = await self.load_envs()
                records = normalize_records(persisted_envs, snapshot_at)
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
            # Keep the just-persisted snapshot hot for normal page reads.
            type(self)._records_cache = (snapshot_at, records)
            if should_start_backfill:
                task = asyncio.create_task(self._backfill_workflow_metadata(records))
                task.add_done_callback(self._report_backfill_result)
                type(self)._metadata_backfill_task = task
            return {"status": "ready", "count": len(envs), "snapshot_at": snapshot_at}
        except Exception as exc:
            async with SessionLocal() as db:
                await db.execute(text("""
                    UPDATE npu_occupancy_sync_state SET status='failed', active_task_id=NULL,
                    error_message=:error, updated_at=UTC_TIMESTAMP() WHERE id=1 AND active_task_id=:task
                """), {"error": str(exc)[:1000], "task": task_id})
                await db.commit()
            raise

    async def load_envs(self) -> tuple[list[dict], datetime | None]:
        async with SessionLocal() as db:
            state = (await db.execute(text("SELECT last_snapshot_at FROM npu_occupancy_sync_state WHERE id=1"))).mappings().first()
            rows = (await db.execute(text("""
                SELECT current.payload
                FROM npu_occupancy_raw_envs AS current
                INNER JOIN (
                    SELECT env_id, MAX(source_updated_at) AS source_updated_at
                    FROM npu_occupancy_raw_envs
                    GROUP BY env_id
                ) AS latest
                    ON latest.env_id = current.env_id
                   AND latest.source_updated_at = current.source_updated_at
            """))).mappings().all()
        envs = [json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"] for row in rows]
        snapshot_at = state["last_snapshot_at"] if state else None
        if snapshot_at is not None and snapshot_at.tzinfo is None:
            snapshot_at = snapshot_at.replace(tzinfo=UTC)
        return envs, snapshot_at

    async def load_records(self) -> tuple[list[OccupancyRecord], datetime | None]:
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
