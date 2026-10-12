"""
CollectorRunner：桥接 CollectorWorker 与具体采集逻辑。

从 collection_tasks 领取任务，根据 task_type 分发执行。
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from collector.ci import CICollector
from collector.pr_pipeline import PRPipelineCollector
from infrastructure.clients.github_client import GitHubClient
from infrastructure.core.config import settings
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import (
    CIJob,
    CIResult,
    DailyFailureRecord,
    JobFailureAnalysis,
    WorkflowConfig,
)
from infrastructure.tasks.sync_progress import SyncProgress

from .worker import CollectorWorker, TaskContext

logger = logging.getLogger(__name__)

class CollectorRunner:
    """将具体采集逻辑绑定到 CollectorWorker。"""

    def __init__(self, worker: CollectorWorker):
        self.worker = worker

    async def run(self):
        """启动 Collector，使用 run_with_executor 绑定具体逻辑。"""
        await self.worker.run_with_executor(self._execute_task)

    async def _execute_task(self, ctx: TaskContext, renew_fn):
        """根据 task_type 执行具体采集，同时后台续约。"""
        # 从 DB 读取任务详情
        async with SessionLocal() as db:
            result = await db.execute(
                text("SELECT task_type, task_params, dedupe_key FROM collection_tasks WHERE id = :id"),
                {"id": ctx.task_id},
            )
            row = result.fetchone()
            if not row:
                logger.error("Task %d not found", ctx.task_id)
                return
            task_type, task_params, dedupe_key = row
            if isinstance(task_params, str):
                task_params = json.loads(task_params)
            task_params = task_params or {}
            # Tasks created by older Scheduler versions only contain
            # ``days_back``.  Their stable dedupe key still identifies them
            # as scheduled work, so upgrade them in memory to the incremental
            # execution path without rewriting queued task rows.
            if task_type == "pr_sync" and str(dedupe_key or "").startswith("pr_sync:scheduled:"):
                newer_result = await db.execute(
                    text(
                        """
                        SELECT 1
                        FROM collection_tasks
                        WHERE task_type = 'pr_sync'
                          AND dedupe_key LIKE 'pr_sync:scheduled:%'
                          AND id > :task_id
                          AND status IN ('pending', 'running')
                        LIMIT 1
                        """
                    ),
                    {"task_id": ctx.task_id},
                )
                if newer_result.scalar_one_or_none() is not None:
                    logger.info(
                        "Skipping stale scheduled PR task %d because a newer scheduled task exists",
                        ctx.task_id,
                    )
                    return
                task_params = {
                    **task_params,
                    "incremental": True,
                    "max_items": int(getattr(settings, "PR_PIPELINE_MAX_ITEMS_PER_SYNC", 50)),
                    "lookback_minutes": int(
                        getattr(settings, "PR_PIPELINE_INCREMENTAL_LOOKBACK_MINUTES", 15)
                    ),
                }

        from infrastructure.tasks.scheduler_config import load_scheduler_runtime_config

        await load_scheduler_runtime_config()

        if task_type in {
            "ci_sync",
            "nightly_data_sync",
            "pr_sync",
            "pr_historical_sync",
            "model_sync",
            "test_board_sync",
            "coverage_sync",
            "code_heatmap_sync",
            "repo_cache_refresh",
        }:
            from infrastructure.core.github_config import load_github_runtime_config

            await load_github_runtime_config()

        logger.info("Executing task %d type=%s generation=%d", ctx.task_id, task_type, ctx.lease_generation)

        # 后台续约
        executing_task = asyncio.current_task()
        renew_task = asyncio.create_task(
            self._renew_loop(ctx.task_id, ctx.lease_token, renew_fn, executing_task)
        )

        try:
            if task_type == "ci_sync":
                await self._run_ci_sync(ctx, task_params)
            elif task_type == "nightly_data_sync":
                await self._run_nightly_data_sync(ctx, task_params)
            elif task_type == "pr_sync":
                await self._run_pr_sync(ctx, task_params)
            elif task_type == "pr_historical_sync":
                await self._run_pr_historical_sync(ctx, task_params)
            elif task_type == "failure_analysis":
                await self._run_failure_analysis(ctx, task_params)
            elif task_type == "issues_derivation":
                await self._run_issues_derivation(ctx)
            elif task_type == "test_board_sync":
                await self._run_test_board_sync(ctx, task_params)
            elif task_type == "coverage_sync":
                await self._run_coverage_sync(ctx, task_params)
            elif task_type == "support_matrix_sync":
                await self._run_support_matrix_sync(ctx, task_params)
            elif task_type == "model_sync":
                await self._run_model_sync(ctx, task_params)
            elif task_type == "code_metrics_collect":
                await self._run_code_metrics_collect(ctx, task_params)
            elif task_type == "code_heatmap_sync":
                await self._run_code_heatmap_sync(ctx, task_params)
            elif task_type == "resource_metrics_collect":
                await self._run_resource_metrics_collect(ctx)
            elif task_type == "resource_metrics_cleanup":
                await self._run_resource_metrics_cleanup(ctx)
            elif task_type == "npu_queue_snapshot":
                await self._run_npu_queue_snapshot(ctx)
            elif task_type == "npu_queue_snapshot_cleanup":
                await self._run_npu_queue_snapshot_cleanup(ctx)
            elif task_type == "repo_cache_refresh":
                await self._run_repo_cache_refresh(ctx, task_params)
            else:
                raise ValueError(f"Unsupported collection task type: {task_type}")
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass

    async def _renew_loop(self, task_id: int, token: str, renew_fn, executing_task):
        """后台续约协程。"""
        while True:
            await asyncio.sleep(self.worker._renew_interval)
            try:
                ok = await renew_fn(task_id, token)
            except Exception:
                logger.exception("Task %d lease renewal raised an error", task_id)
                ok = False
            if not ok:
                logger.warning("Task %d lease renewal failed", task_id)
                if executing_task is not None and not executing_task.done():
                    executing_task.cancel()
                return

    async def _run_ci_sync(self, ctx: TaskContext, task_params: dict):
        """CI 数据同步。"""
        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async def persist_progress(progress: dict):
                await self.worker._write_checkpoint(
                    ctx.task_id,
                    ctx.lease_token,
                    progress,
                )

            async with SessionLocal() as db:
                collector = CICollector(
                    github,
                    db,
                    progress_callback=persist_progress,
                    sync_progress=SyncProgress(),
                )
                collected = await collector.collect_workflow_runs(
                    days_back=int(task_params.get("days_back", settings.CI_SYNC_DAYS_BACK)),
                    max_runs_per_workflow=int(task_params.get("max_runs", settings.CI_SYNC_MAX_RUNS_PER_WORKFLOW)),
                    force_full_refresh=bool(task_params.get("force_full_refresh", False)),
                )
                # CI collection must remain successful even when the optional
                # repository cache is temporarily unavailable.  The durable
                # nightly_data_sync task retries the materialization later.
                try:
                    from collector.nightly_data import NightlyDataCollector

                    nightly_data = NightlyDataCollector(db)
                    materialized = await nightly_data.sync()
                    auto_analysis = await self._enqueue_auto_failure_analysis(
                        db,
                        nightly_data.last_materialized_job_ids,
                    )
                    await db.commit()
                    logger.info(
                        "CI task %d completed: collected=%d nightly=%s auto_analysis=%s",
                        ctx.task_id,
                        collected,
                        materialized,
                        auto_analysis,
                    )
                except Exception:
                    logger.exception(
                        "CI task %d collected %d rows but Nightly materialization failed; "
                        "nightly_data_sync will retry",
                        ctx.task_id,
                        collected,
                    )
        finally:
            await github.close()

    async def _run_nightly_data_sync(self, ctx: TaskContext, task_params: dict):
        """Snapshot Nightly YAML and materialize daily failure records."""
        from collector.nightly_data import NightlyDataCollector

        async with SessionLocal() as db:
            nightly_data = NightlyDataCollector(db)
            result = await nightly_data.sync()
            auto_analysis = await self._enqueue_auto_failure_analysis(
                db,
                nightly_data.last_materialized_job_ids,
            )
            await db.commit()
        logger.info(
            "Nightly data task %d completed: %s auto_analysis=%s",
            ctx.task_id,
            result,
            auto_analysis,
        )

    async def _enqueue_auto_failure_analysis(
        self,
        db: AsyncSession,
        job_ids: set[int],
    ) -> dict[str, int]:
        """Queue every failed Nightly job materialized by this sync.

        Analysis is deliberately enqueued as a durable Collector task rather
        than executed inline, so a slow LLM or missing job log cannot extend
        or fail the CI synchronization task. Records without an analysis row
        and legacy ``reused`` records are eligible for automatic work. The
        Automatic work is forced so every newly
        materialized job gets a real analysis; the cross-job failure
        fingerprint cache is reserved for explicit non-forced requests.
        """
        from infrastructure.tasks.task_manager import TaskManager

        if not getattr(settings, "CI_AUTO_FAILURE_ANALYSIS_ENABLED", True):
            return {"selected": 0, "queued": 0, "skipped": 0, "active": 0}

        failure_conclusions = ("failure", "timed_out", "startup_failure", "cancelled")
        new_job_ids = {int(job_id) for job_id in job_ids if job_id is not None}
        if not new_job_ids:
            return {"selected": 0, "queued": 0, "skipped": 0, "active": 0}

        # A queued failure-analysis task has no JobFailureAnalysis row until
        # the Collector starts it. Count and exclude those tasks explicitly;
        # otherwise every sync would repeatedly select the same queued jobs
        # and never fill an available slot with the next pending failure.
        active_tasks_result = await db.execute(
            text("""
                SELECT JSON_UNQUOTE(JSON_EXTRACT(task_params, '$.job_id'))
                FROM collection_tasks
                WHERE task_type = 'failure_analysis'
                  AND status IN ('pending', 'running')
            """)
        )
        active_task_rows = active_tasks_result.all()
        active_job_ids = {
            int(job_id)
            for (job_id,) in active_task_rows
            if job_id is not None and str(job_id).strip().isdigit()
        }
        first_record_id = func.min(DailyFailureRecord.id)
        latest_job_started_at = func.max(CIJob.started_at)
        candidate_query = (
            select(DailyFailureRecord.job_id)
            .join(CIJob, CIJob.job_id == DailyFailureRecord.job_id)
            .join(
                CIResult,
                and_(
                    CIResult.run_id == CIJob.run_id,
                    CIResult.run_id == DailyFailureRecord.run_id,
                    CIResult.workflow_name == CIJob.workflow_name,
                    CIResult.workflow_name == DailyFailureRecord.workflow_name,
                ),
            )
            .join(
                WorkflowConfig,
                and_(
                    WorkflowConfig.workflow_name == CIResult.workflow_name,
                    WorkflowConfig.auto_failure_analysis_enabled.is_(True),
                ),
            )
            .outerjoin(JobFailureAnalysis, JobFailureAnalysis.job_id == DailyFailureRecord.job_id)
            .where(
                CIJob.conclusion.in_(failure_conclusions),
                DailyFailureRecord.job_id.isnot(None),
                # A queued Collector task has no analysis row until it starts;
                # TaskManager's stable dedupe key makes a repeated scan safe.
                # ``reused`` rows are legacy cross-job results and must be
                # re-analyzed once so their report belongs to this concrete
                # GitHub job instead of another test case.
                or_(
                    JobFailureAnalysis.id.is_(None),
                    JobFailureAnalysis.analysis_status == "reused",
                ),
                DailyFailureRecord.job_id.in_(new_job_ids),
            )
            .group_by(DailyFailureRecord.job_id)
            .order_by(latest_job_started_at.desc(), first_record_id)
        )
        if active_job_ids:
            candidate_query = candidate_query.where(~DailyFailureRecord.job_id.in_(active_job_ids))

        records_result = await db.execute(candidate_query)
        selected_job_ids = list(dict.fromkeys(
            int(job_id) for (job_id,) in records_result.all() if job_id is not None
        ))
        if not selected_job_ids:
            return {
                "selected": 0,
                "queued": 0,
                "skipped": 0,
                "active": len(active_task_rows),
            }

        queued = 0
        skipped = 0
        for job_id in selected_job_ids:
            task_id = await TaskManager.create_task(
                db,
                "failure_analysis",
                # Scheduler-originated analysis is always a real analysis for
                # this concrete GitHub job.  The worker derives force from
                # the origin; keeping no force=false payload here prevents
                # the obsolete cross-job reuse path from returning.
                {"job_id": job_id, "triggered_by": "scheduler"},
                f"failure_analysis:{job_id}",
                required_capability="python",
                # Keep automatic analysis behind collection/sync work. Manual
                # analysis requests still use their explicit higher priority.
                priority=-10,
            )
            if task_id is not None:
                queued += 1
            else:
                skipped += 1

        return {
            "selected": len(selected_job_ids),
            "queued": queued,
            "skipped": skipped,
            "active": len(active_task_rows),
        }

    async def _run_model_sync(self, ctx: TaskContext, task_params: dict):
        """模型报告同步。"""
        from model_sync.model_sync_service import ModelSyncService

        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async with SessionLocal() as db:
                service = ModelSyncService(db, github)
                total, collected = await service.sync_all_enabled_configs(
                    days_back=int(task_params.get("days_back", settings.MODEL_SYNC_DAYS_BACK)),
                    runs_limit=int(task_params.get("runs_limit", settings.MODEL_SYNC_RUNS_LIMIT)),
                )
                logger.info(
                    "Model sync task %d completed: %d configs, %d reports",
                    ctx.task_id,
                    total,
                    collected,
                )
        finally:
            await github.close()

    async def _run_failure_analysis(self, ctx: TaskContext, task_params: dict):
        """Run one failure analysis from the durable task queue."""
        from failure_analysis.failure_analysis import FailureAnalysisService

        job_id = int(task_params["job_id"])
        triggered_by = str(task_params.get("triggered_by", "manual"))
        # Scheduler-originated work is always forced. Manual requests retain
        # their explicit force option for deliberate reruns.
        force = triggered_by == "scheduler" or bool(task_params.get("force", False))
        async with SessionLocal() as db:
            if triggered_by == "scheduler":
                if not settings.CI_AUTO_FAILURE_ANALYSIS_ENABLED:
                    logger.info("Skipping automatic failure analysis for job %d while globally disabled", job_id)
                    return
                eligible = await db.scalar(
                    select(CIJob.job_id)
                    .join(
                        CIResult,
                        and_(
                            CIResult.run_id == CIJob.run_id,
                            CIResult.workflow_name == CIJob.workflow_name,
                        ),
                    )
                    .join(
                        DailyFailureRecord,
                        and_(
                            DailyFailureRecord.job_id == CIJob.job_id,
                            DailyFailureRecord.run_id == CIResult.run_id,
                            DailyFailureRecord.workflow_name == CIResult.workflow_name,
                        ),
                    )
                    .join(
                        WorkflowConfig,
                        and_(
                            WorkflowConfig.workflow_name == CIResult.workflow_name,
                            WorkflowConfig.auto_failure_analysis_enabled.is_(True),
                        ),
                    )
                    .where(
                        CIJob.job_id == job_id,
                        CIJob.conclusion.in_(("failure", "timed_out", "startup_failure", "cancelled")),
                    )
                    .limit(1)
                )
                if eligible is None:
                    logger.info("Skipping automatic failure analysis for job %d without a linked persisted workflow", job_id)
                    return
            await FailureAnalysisService().analyze_failed_job(
                job_id=job_id,
                db=db,
                force=force,
                triggered_by=triggered_by,
            )

    async def _run_code_metrics_collect(self, ctx: TaskContext, task_params: dict):
        """Run local code-metrics tools only in the Collector execution role."""
        from collector.code_metrics import CodeMetricsCollector

        async with SessionLocal() as db:
            result = await CodeMetricsCollector(db).collect(
                branch=str(task_params.get("branch", "main"))
            )
        logger.info("Code metrics task %d completed: %s", ctx.task_id, result)

    async def _run_repo_cache_refresh(self, ctx: TaskContext, task_params: dict) -> None:
        """Refresh both bare mirrors and their fixed main worktrees."""
        from infrastructure.clients.github_cache import get_github_cache, get_vllm_cache

        repo_type = str(task_params.get("repo_type", "all"))
        caches = {"ascend": get_github_cache(), "vllm": get_vllm_cache()}
        if repo_type not in {"ascend", "vllm", "all"}:
            raise ValueError(f"unsupported repository cache type: {repo_type}")
        selected = caches if repo_type == "all" else {repo_type: caches[repo_type]}
        repositories = {}
        for name, cache in selected.items():
            ready = await asyncio.to_thread(cache.pull)
            if not ready or not cache.fetch_full_history():
                raise RuntimeError(f"repository mirror refresh failed: {name}")
            repositories[name] = str(cache.cache_dir.resolve())
        logger.info("Repository mirror refresh task %d completed: %s", ctx.task_id, repositories)

    async def _run_code_heatmap_sync(self, ctx: TaskContext, task_params: dict):
        """Synchronize the code heatmap via GitHub from the Collector role."""
        from collector.heatmap import sync_heatmap_from_github

        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async with SessionLocal() as db:
                result = await sync_heatmap_from_github(
                    db,
                    github,
                    settings.GITHUB_OWNER,
                    settings.GITHUB_REPO,
                    days=int(task_params.get("days", 30)),
                )
            logger.info("Code heatmap task %d completed: %s", ctx.task_id, result)
        finally:
            await github.close()

    async def _run_resource_metrics_collect(self, ctx: TaskContext):
        """Collect node and NPU metrics from the Collector execution role."""
        from collector.alert_evaluator import AlertEvaluator
        from collector.resource_metrics import ResourceMetricsCollector

        async with SessionLocal() as db:
            count = await ResourceMetricsCollector(db).collect_snapshot()
            alerts = await AlertEvaluator(db).evaluate_all_rules()
        logger.info(
            "Resource metrics task %d completed: clusters=%d alerts=%d",
            ctx.task_id,
            count,
            alerts,
        )

    async def _run_resource_metrics_cleanup(self, ctx: TaskContext):
        """Delete expired resource metrics from the Collector execution role."""
        from collector.resource_metrics import ResourceMetricsCollector

        async with SessionLocal() as db:
            deleted = await ResourceMetricsCollector(db).cleanup_old_metrics()
        logger.info("Resource metrics cleanup task %d deleted %d rows", ctx.task_id, deleted)

    async def _run_npu_queue_snapshot(self, ctx: TaskContext):
        """Persist the current CI-derived queue state for the 24h trend."""
        from npu_queue.snapshots import collect_current_snapshot

        async with SessionLocal() as db:
            snapshot = await collect_current_snapshot(db)
        logger.info("NPU queue snapshot task %d captured at %s", ctx.task_id, snapshot.captured_at)

    async def _run_npu_queue_snapshot_cleanup(self, ctx: TaskContext):
        from npu_queue.snapshots import cleanup_snapshots

        async with SessionLocal() as db:
            deleted = await cleanup_snapshots(db, retention_days=settings.NPU_QUEUE_SNAPSHOT_RETENTION_DAYS)
        logger.info("NPU queue snapshot cleanup task %d deleted %d rows", ctx.task_id, deleted)

    async def _run_issues_derivation(self, ctx: TaskContext):
        """Derive test-board issue counts from durable worker execution."""
        from test_board.issues_found_derivator import IssuesFoundDerivator

        async with SessionLocal() as db:
            result = await IssuesFoundDerivator(db).derive_all()
            logger.info("issues derivation task %d completed: %s", ctx.task_id, result)

    async def _run_test_board_sync(self, ctx: TaskContext, task_params: dict):
        """Parse CI test results and derive issues in the Collector role."""
        from test_board.issues_found_derivator import IssuesFoundDerivator
        from test_board.test_board_service import TestBoardService

        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async with SessionLocal() as db:
                count = await TestBoardService(db, github).parse_ci_results(
                    days_back=int(task_params.get("days_back", 7))
                )
                derivation = await IssuesFoundDerivator(db).derive_all() if count else None
            logger.info("test-board task %d completed: parsed=%d derivation=%s", ctx.task_id, count, derivation)
        finally:
            await github.close()

    async def _run_coverage_sync(self, ctx: TaskContext, task_params: dict):
        """Read the external coverage.py artifact and persist normalized data."""
        from test_board.coverage_sync import sync_all_coverage

        async with SessionLocal() as db:
            result = await sync_all_coverage(
                db,
                source=str(task_params.get("source", "all")),
                strategy=str(task_params.get("strategy", "hourly")),
            )
        failed_sources = [
            name for name, value in result.items()
            if isinstance(value, dict) and value.get("success") is False
        ]
        if failed_sources:
            raise RuntimeError(
                "coverage synchronization failed for: " + ", ".join(failed_sources)
            )
        logger.info("coverage task %d completed: %s", ctx.task_id, result)

    async def _run_support_matrix_sync(self, ctx: TaskContext, task_params: dict):
        """Synchronize upstream support-matrix data in the Collector role."""
        from support_matrix.support_matrix_sync import sync_support_matrix

        async with SessionLocal() as db:
            result = await sync_support_matrix(db, dry_run=bool(task_params.get("dry_run", False)))
        if result.get("success") is False:
            # A collector task that returns a business-level failure must go
            # through the worker retry/dead-letter path.  Treating the
            # returned error as a successful coroutine completion would mark
            # the durable task ``completed`` and suppress all retries.
            raise RuntimeError(result.get("error") or "support matrix sync failed")
        logger.info("support-matrix task %d completed: %s", ctx.task_id, result)

    async def _run_pr_sync(self, ctx: TaskContext, task_params: dict):
        """Synchronize pull-request pipeline data from GitHub."""
        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async with SessionLocal() as db:
                collector = PRPipelineCollector(github, db)
                await collector.collect_prs(
                    settings.GITHUB_OWNER,
                    settings.GITHUB_REPO,
                    days_back=int(task_params.get("days_back", 7)),
                    incremental=bool(task_params.get("incremental", False)),
                    max_items=(
                        int(task_params["max_items"])
                        if task_params.get("max_items") is not None
                        else None
                    ),
                    lookback_minutes=int(task_params.get("lookback_minutes", 15)),
                )
        finally:
            await github.close()

    async def _run_pr_historical_sync(self, ctx: TaskContext, task_params: dict):
        """Collect historical PR data; this can be long-running and rate-limited."""
        from collector.pr_pipeline_historical import PRPipelineHistoricalCollector

        github = GitHubClient(settings.GITHUB_TOKEN)
        try:
            async with SessionLocal() as db:
                result = await PRPipelineHistoricalCollector(github, db).collect_historical(
                    settings.GITHUB_OWNER,
                    settings.GITHUB_REPO,
                    phases=list(task_params.get("phases", ["A", "B"])),
                    months_back=int(task_params.get("months_back", 3)),
                )
            logger.info("Historical PR task %d completed: %s", ctx.task_id, result)
        finally:
            await github.close()
