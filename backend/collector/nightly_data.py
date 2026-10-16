"""Collect and materialize Nightly test data.

The Scheduler only creates a durable task.  Repository access and all writes
to ``nightly_test_cases``/``daily_failure_records`` belong to the Collector so
that the same workflow is valid in local, Linux, and horizontally scaled
deployments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from collector.ci import CICollector
from infrastructure.clients.github_cache import get_github_cache_for_repo
from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    CIJob,
    CIResult,
    DailyFailureRecord,
    JobFailureAnalysis,
    NightlyTestCase,
)
from infrastructure.persistence.run_attempts import (
    extract_run_attempt,
    is_current_run_attempt,
)
from tooling.model_fo_mapping import (
    load_model_fo_mappings,
    lookup_model_fo,
    seed_missing_model_fo_mappings,
)
from tooling.parsers.nightly_config_parser import (
    CONFIG_PATH,
    NightlyConfigParser,
    load_model_fo_map,
)

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
_SYNC_LOCK_NAME = "vllm_dashboard:nightly_data_sync"
_MATERIALIZATION_LOCK_RETRIES = 3
_MATERIALIZATION_LOCK_RETRY_SECONDS = 0.25


class NightlyDataCollector:
    """Snapshot Nightly YAML and materialize failed Nightly jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db
        # Job IDs materialized during the most recent sync. The Collector
        # uses this narrow set to enqueue automatic failure analysis without
        # replaying the entire historical failure table on every CI sync.
        self.last_materialized_job_ids: set[int] = set()

    def _get_cache(self):
        """Return the bare mirror facade, bootstrapping a clean deployment."""
        cache = get_github_cache_for_repo(settings.GITHUB_OWNER, settings.GITHUB_REPO)
        if not cache._is_repo_cloned() and not cache.clone():
            raise RuntimeError(
                "vllm-ascend repository cache is unavailable; "
                f"expected {cache.cache_dir}"
            )
        return cache

    async def sync(self) -> dict[str, int]:
        """Run both stages once globally, even when CI and scheduler overlap."""

        # GET_LOCK is connection-scoped. Hold it on a dedicated connection so
        # commits performed by the working session cannot accidentally release
        # it before daily_failure_records materialization has finished.
        engine = self.db.bind
        if engine is None:
            raise RuntimeError("Nightly data synchronization requires a bound database engine")
        async with engine.connect() as lock_connection:
            acquired = (await lock_connection.execute(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": _SYNC_LOCK_NAME},
            )).scalar_one()
            if acquired != 1:
                logger.info("Nightly data synchronization skipped: another worker holds the lock")
                return {"nightly_test_cases": 0, "daily_failure_records": 0}
            try:
                for attempt in range(_MATERIALIZATION_LOCK_RETRIES):
                    try:
                        snapshot_count = await self.snapshot_configs()
                        failure_count = await self.populate_daily_failure_records()
                        return {
                            "nightly_test_cases": snapshot_count,
                            "daily_failure_records": failure_count,
                        }
                    except OperationalError as exc:
                        if not self._is_transient_lock_error(exc):
                            raise
                        await self.db.rollback()
                        if attempt + 1 >= _MATERIALIZATION_LOCK_RETRIES:
                            logger.error(
                                "Nightly data materialization exhausted %d lock retries",
                                _MATERIALIZATION_LOCK_RETRIES,
                            )
                            raise
                        delay = _MATERIALIZATION_LOCK_RETRY_SECONDS * (attempt + 1)
                        logger.warning(
                            "Nightly data materialization hit a transient database lock; "
                            "retrying in %.2fs (%d/%d)",
                            delay,
                            attempt + 1,
                            _MATERIALIZATION_LOCK_RETRIES,
                        )
                        await asyncio.sleep(delay)
            finally:
                await lock_connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": _SYNC_LOCK_NAME},
                )

    @staticmethod
    def _is_transient_lock_error(exc: OperationalError) -> bool:
        """Return whether MySQL can safely retry this transaction-level conflict."""
        original = getattr(exc, "orig", None)
        errno = getattr(original, "errno", None)
        if errno is None and getattr(original, "args", None):
            errno = original.args[0]
        return errno in (1205, 1213)

    async def snapshot_configs(self) -> int:
        """Snapshot active branches from ``nightly_config.yaml``."""

        cache = self._get_cache()
        seed_count = await seed_missing_model_fo_mappings(self.db, load_model_fo_map())
        fo_map = await load_model_fo_mappings(self.db)
        today = datetime.now(BEIJING_TZ).date()
        branches = cache.get_remote_branches("releases/")
        target_branches = ["main", *(b for b in branches if b.startswith("releases/"))]

        total = 0
        for branch in target_branches:
            content = cache.get_file_content(CONFIG_PATH, branch)
            if content is None:
                logger.warning("Unable to read Nightly config at ref %s", branch)
                continue
            cases = NightlyConfigParser.parse_content(
                content, report_date=today.isoformat(), source_branch=branch
            )
            for case in cases:
                result = await self.db.execute(
                    select(NightlyTestCase).where(
                        NightlyTestCase.report_date == today,
                        NightlyTestCase.source_branch == branch,
                        NightlyTestCase.workflow_name == case.workflow,
                        NightlyTestCase.job_name == case.name,
                    )
                )
                existing = result.scalar_one_or_none()
                model_fo = lookup_model_fo(fo_map, case.model_path)
                if existing is None:
                    self.db.add(
                        NightlyTestCase(
                            report_date=today,
                            source_branch=branch,
                            workflow_name=case.workflow,
                            job_name=case.name,
                            display_name=case.name,
                            test_model=case.model_path,
                            model_fo=model_fo,
                            deployment_type=case.deployment,
                        )
                    )
                else:
                    existing.display_name = case.name
                    existing.test_model = case.model_path
                    existing.deployment_type = case.deployment
                total += 1

        if total or seed_count:
            await self.db.commit()
        logger.info(
            "Nightly config snapshot completed: %d entries across %d branches",
            total,
            len(target_branches),
        )
        return total

    async def populate_daily_failure_records(self) -> int:
        """Materialize failed or cancelled Nightly jobs matched to YAML snapshots.

        Cancellation is not a test failure and is excluded from test-board
        execution results, but it is still an operational state that must be
        retained in DailyFailureTracking for follow-up.
        """

        self.last_materialized_job_ids.clear()

        cutoff = datetime.now(UTC) - timedelta(days=14)

        result = await self.db.execute(
            select(CIJob).where(
                or_(
                    CIJob.started_at >= cutoff,
                    CIJob.completed_at >= cutoff,
                ),
                CIJob.conclusion.in_(
                    ["failure", "timed_out", "startup_failure", "cancelled"]
                ),
            )
        )
        tracked_jobs = result.scalars().all()

        # A Nightly workflow is one reporting batch.  Prefer its final
        # workflow timestamp so every job in a run crossing midnight lands
        # on the same reporting day. Only jobs belonging to a persisted
        # Workflow run can enter the daily failure tracker.
        workflow_completed_at: dict[int, datetime | None] = {}
        workflow_names: dict[int, str] = {}
        workflow_attempt: dict[int, int | None] = {}
        workflow_branch: dict[int, str | None] = {}
        workflow_event: dict[int, str | None] = {}
        run_ids = {job.run_id for job in tracked_jobs if job.run_id is not None}
        if run_ids:
            workflow_result = await self.db.execute(
                select(
                    CIResult.run_id,
                    CIResult.workflow_name,
                    CIResult.completed_at,
                    CIResult.branch,
                    CIResult.data,
                    CIResult.event,
                ).where(CIResult.run_id.in_(run_ids))
            )
            for run_id, workflow_name, completed_at, branch, run_data, event in workflow_result.all():
                workflow_names[run_id] = workflow_name
                workflow_completed_at[run_id] = completed_at
                workflow_branch[run_id] = branch
                workflow_attempt[run_id] = extract_run_attempt(run_data)
                workflow_event[run_id] = event

        tracked_jobs = [
            job for job in tracked_jobs
            if workflow_names.get(job.run_id) == job.workflow_name
        ]

        # A GitHub re-run keeps the same workflow run ID but creates a new set
        # of jobs. Only jobs from the final attempt are valid for the daily
        # tracker; otherwise cancelled jobs from an interrupted attempt are
        # incorrectly materialized as failures.
        tracked_jobs = [
            job
            for job in tracked_jobs
            if is_current_run_attempt(job.data, workflow_attempt.get(job.run_id))
        ]

        # Older collector versions persisted ``/nightly pr`` dispatches before
        # the PR-only step detector was introduced.  Re-check persisted step
        # data here so those runs cannot be re-materialized indefinitely.
        excluded_run_ids = {
            job.run_id
            for job in tracked_jobs
            if self._is_pr_nightly_job(job, workflow_event.get(job.run_id))
        }
        purged_count = 0
        if excluded_run_ids:
            purge_result = await self.db.execute(
                delete(DailyFailureRecord).where(
                    DailyFailureRecord.run_id.in_(excluded_run_ids)
                )
            )
            purged_count = int(getattr(purge_result, "rowcount", 0) or 0)
            tracked_jobs = [
                job for job in tracked_jobs if job.run_id not in excluded_run_ids
            ]

        snapshot_result = await self.db.execute(select(NightlyTestCase))
        snapshots = snapshot_result.scalars().all()
        snapshot_map = {
            (
                str(case.report_date),
                case.source_branch,
                case.workflow_name,
                case.job_name,
            ): case
            for case in snapshots
        }
        # A fresh deployment only has today's snapshot, while the CI window
        # can contain failures from earlier dates. Exact-date snapshots win;
        # the fallback lets the first run backfill historical jobs.
        fallback_snapshots: dict[tuple[str, str], list[NightlyTestCase]] = {}
        for snapshot in snapshots:
            fallback_snapshots.setdefault(
                (snapshot.source_branch, snapshot.workflow_name),
                [],
            ).append(snapshot)
        for values in fallback_snapshots.values():
            values.sort(key=lambda item: str(item.report_date), reverse=True)

        existing_result = await self.db.execute(select(DailyFailureRecord))
        existing_records = existing_result.scalars().all()
        existing_keys = {
            (
                str(record.report_date),
                record.source_branch,
                record.workflow_name,
                record.job_name,
            )
            for record in existing_records
        }
        existing_by_job_id = {
            record.job_id: record
            for record in existing_records
            if record.job_id is not None
        }
        existing_by_run_job = {
            (record.run_id, record.workflow_name, record.job_name): record
            for record in existing_records
        }
        existing_by_key = {
            (
                str(record.report_date),
                record.source_branch,
                record.workflow_name,
                record.job_name,
            ): record
            for record in existing_records
        }

        # Failure analysis and daily materialization can finish in either
        # order. Load completed categories once to avoid an N+1 query while
        # enriching newly-created and existing daily records.
        tracked_job_ids = {job.job_id for job in tracked_jobs if job.job_id is not None}
        analyses_by_job_id: dict[int, JobFailureAnalysis] = {}
        analyses_by_run_job: dict[tuple[int, str, str], JobFailureAnalysis] = {}
        if tracked_job_ids:
            analysis_result = await self.db.execute(
                select(JobFailureAnalysis).where(
                    JobFailureAnalysis.job_id.in_(tracked_job_ids),
                    JobFailureAnalysis.analysis_status.in_(["completed", "reused"]),
                )
            )
            for analysis in analysis_result.scalars().all():
                if not analysis.problem_category:
                    continue
                analyses_by_job_id[analysis.job_id] = analysis
                analyses_by_run_job[
                    (analysis.run_id, analysis.workflow_name, analysis.job_name)
                ] = analysis

        new_count = 0
        corrected_count = 0
        category_sync_count = 0
        for job in tracked_jobs:
            report_date = self._report_date_for_job(
                job,
                workflow_completed_at.get(job.run_id),
            )
            if report_date is None:
                continue
            branch = self._source_branch_for_job(
                job,
                workflow_branch.get(job.run_id),
            )
            key = (report_date, branch, job.workflow_name, job.job_name)

            snapshot = self._match_snapshot(
                snapshot_map,
                fallback_snapshots,
                report_date=report_date,
                source_branch=branch,
                workflow_name=job.workflow_name,
                job_name=job.job_name or "",
            )
            if snapshot is None:
                # This intentionally filters infrastructure jobs such as
                # ``Build image`` and ``Remove node taints``.
                continue

            # The first sync may have used the job start date.  When the job
            # later gets its completion timestamp, find that materialized
            # row by its stable GitHub job identity and move only its date.
            # This preserves all fields entered by the user in the tracker.
            existing = existing_by_key.get(key)
            if existing is None:
                existing = existing_by_job_id.get(job.job_id)
            if existing is None:
                existing = existing_by_run_job.get(
                    (job.run_id, job.workflow_name, job.job_name)
                )

            analysis = analyses_by_job_id.get(job.job_id)
            if analysis is None:
                analysis = analyses_by_run_job.get(
                    (job.run_id, job.workflow_name, job.job_name)
                )
            problem_category = analysis.problem_category if analysis else None
            github_url = (
                f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
                f"/actions/runs/{job.run_id}/job/{job.job_id}"
                if job.job_id
                else None
            )

            if existing is not None:
                # Keep one daily row per configured test, but a completed test
                # failure is more actionable than an earlier cancellation of
                # the same test on the same reporting day.
                if job.conclusion == "failure" and getattr(existing, "conclusion", None) != "failure":
                    previous_job_id = existing.job_id
                    existing.run_id = job.run_id
                    existing.job_id = job.job_id
                    existing.conclusion = job.conclusion
                    existing.started_at = job.started_at
                    existing.completed_at = job.completed_at
                    existing.duration_seconds = job.duration_seconds
                    existing.hardware = job.hardware
                    existing.github_job_url = github_url
                    if previous_job_id is not None:
                        existing_by_job_id.pop(previous_job_id, None)
                    if job.job_id is not None:
                        existing_by_job_id[job.job_id] = existing
                if str(existing.report_date) != report_date:
                    existing.report_date = date.fromisoformat(report_date)
                    corrected_count += 1
                if problem_category and existing.problem_category != problem_category:
                    existing.problem_category = problem_category
                    category_sync_count += 1
                existing_keys.add(key)
                # A forced refresh must be eligible for automatic analysis as
                # well, but only when this job is the record currently bound
                # to the daily test case.
                if job.job_id is not None and existing.job_id == job.job_id:
                    self.last_materialized_job_ids.add(job.job_id)
                continue

            record = DailyFailureRecord(
                report_date=report_date,
                source_branch=branch,
                workflow_name=job.workflow_name,
                job_name=job.job_name,
                run_id=job.run_id,
                job_id=job.job_id,
                conclusion=job.conclusion,
                started_at=job.started_at,
                completed_at=job.completed_at,
                duration_seconds=job.duration_seconds,
                hardware=job.hardware,
                display_name=snapshot.display_name,
                test_model=snapshot.test_model,
                model_fo=snapshot.model_fo,
                owner=snapshot.owner,
                deployment_type=snapshot.deployment_type,
                processing_status="未处理",
                problem_category=problem_category,
                github_job_url=github_url,
            )
            self.db.add(record)
            # Keep the in-memory indexes in sync with pending inserts.  A
            # single collection batch can contain the same logical job key
            # more than once (for example, duplicate GitHub job payloads
            # from overlapping sync windows).  Without this assignment the
            # second row is not found until commit, and the database unique
            # constraint rejects the whole batch.
            existing_by_key[key] = record
            existing_keys.add(key)
            if job.job_id is not None:
                existing_by_job_id[job.job_id] = record
                self.last_materialized_job_ids.add(job.job_id)
            existing_by_run_job[(job.run_id, job.workflow_name, job.job_name)] = record
            new_count += 1

        if new_count or corrected_count or category_sync_count or purged_count:
            await self.db.commit()
        logger.info(
            "Daily failure materialization completed: %d new, %d corrected, "
            "%d categories synced, %d stale /nightly rows removed from %d tracked jobs",
            new_count,
            corrected_count,
            category_sync_count,
            purged_count,
            len(tracked_jobs),
        )
        return new_count

    @staticmethod
    def _is_pr_nightly_job(job: CIJob, workflow_event: str | None) -> bool:
        """Identify a persisted test job that executed PR-only Nightly steps."""
        steps = getattr(job, "steps_data", None)
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (TypeError, ValueError):
                steps = []
        if not isinstance(steps, list):
            steps = []
        return CICollector._is_pr_nightly_dispatch(
            {"event": workflow_event},
            [
                {
                    "name": job.job_name,
                    "conclusion": job.conclusion,
                    "steps": steps,
                }
            ],
        )

    @staticmethod
    def _report_date_for_job(
        job: CIJob,
        workflow_completed_at: datetime | None = None,
    ) -> str | None:
        """Return the Nightly reporting day in Beijing time.

        A Nightly workflow triggered before midnight can finish and produce
        its result after midnight.  The whole workflow batch belongs to the
        latter day, so workflow completion time is authoritative.  Job
        completion and then job start time are fallbacks for incomplete API
        data.
        Naive database timestamps are treated as UTC, matching the project's
        storage convention.
        """

        event_time = workflow_completed_at or job.completed_at or job.started_at
        if event_time is None:
            return None
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        return event_time.astimezone(BEIJING_TZ).date().isoformat()

    @staticmethod
    def _source_branch_for_job(
        job: CIJob,
        workflow_branch: str | None = None,
    ) -> str:
        """Return the authoritative Git branch for a collected Nightly job.

        GitHub's workflow ``head_branch`` is persisted as ``CIResult.branch``
        and in the raw job payload. Job names are display text and may replace
        ``/`` with ``-`` (for example ``releases/v0.26.0rc`` becomes
        ``releases-v0.26.0rc``), so parsing the name is only a legacy fallback.
        """

        if workflow_branch and workflow_branch.strip():
            return workflow_branch.strip()

        payload = job.data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = None
        if isinstance(payload, dict):
            head_branch = payload.get("head_branch")
            if isinstance(head_branch, str) and head_branch.strip():
                return head_branch.strip()

        branch_match = re.match(r"^\S+\s+\(([^,]+),", job.job_name or "")
        return branch_match.group(1).strip() if branch_match else "main"

    @staticmethod
    def _match_snapshot(
        snapshot_map: dict[tuple[str, str, str, str], NightlyTestCase],
        fallback_snapshots: dict[tuple[str, str], list[NightlyTestCase]],
        *,
        report_date: str,
        source_branch: str,
        workflow_name: str,
        job_name: str,
    ) -> NightlyTestCase | None:
        """Match a failed job to an exact or historical YAML snapshot."""

        for (snapshot_date, branch, workflow, snapshot_job), value in snapshot_map.items():
            if snapshot_date != report_date or branch != source_branch or workflow != workflow_name:
                continue
            if (value.test_model and value.test_model in job_name) or snapshot_job in job_name:
                return value

        for candidate in fallback_snapshots.get((source_branch, workflow_name), []):
            if (candidate.test_model and candidate.test_model in job_name) or candidate.job_name in job_name:
                return candidate
        return None
