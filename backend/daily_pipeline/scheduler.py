"""Schedule Daily module tasks from their database configuration."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from apscheduler import events
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from daily_pipeline.cases import DailyCaseService
from daily_pipeline.code_resolution import process_code_issue, ready_code_merge
from daily_pipeline.collection_scheduler import DailyRunCollector
from daily_pipeline.cron import schedule_trigger
from daily_pipeline.dispatch import trigger_daily_pipeline
from daily_pipeline.logging import setup_daily_file_logging
from daily_pipeline.processing_scheduler import DailyProcessRunner
from daily_pipeline.recommendation_events import watch_daily_recommendations
from daily_pipeline.tracking import DailyTrackingService
from daily_pipeline.trigger_config import TRIGGER_TASK
from daily_pipeline.version_resolution import advance_version_flow, version_work_ids
from infrastructure.clients.daily_pipeline_scheduler import (
    COLLECTION_PURPOSE,
    PROCESSING_PURPOSE,
    RELOAD_PURPOSE,
    SERVICE_SUBJECT,
)
from infrastructure.core.config import settings
from infrastructure.core.logging import setup_db_logging
from infrastructure.core.security import decode_token
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.daily_pipeline_cases import DailyCaseRepository
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository
from infrastructure.persistence.daily_pipeline_schedules import enabled_schedule, enabled_schedules
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineIssue
from infrastructure.persistence.models import DailyPipelineProcessNode as Node

logger = logging.getLogger("daily_pipeline.scheduler")
CATALOG_TASK = "sync_nightly_cases"
ISSUE_SYNC_TASK = "sync_daily_issues"
MISFIRE_GRACE_SECONDS = 60
EVENT_NAMES = {
    events.EVENT_SCHEDULER_STARTED: "scheduler_started",
    events.EVENT_SCHEDULER_SHUTDOWN: "scheduler_shutdown",
    events.EVENT_SCHEDULER_PAUSED: "scheduler_paused",
    events.EVENT_SCHEDULER_RESUMED: "scheduler_resumed",
    events.EVENT_EXECUTOR_ADDED: "executor_added",
    events.EVENT_EXECUTOR_REMOVED: "executor_removed",
    events.EVENT_JOBSTORE_ADDED: "jobstore_added",
    events.EVENT_JOBSTORE_REMOVED: "jobstore_removed",
    events.EVENT_ALL_JOBS_REMOVED: "all_jobs_removed",
    events.EVENT_JOB_ADDED: "job_added",
    events.EVENT_JOB_REMOVED: "job_removed",
    events.EVENT_JOB_MODIFIED: "job_modified",
    events.EVENT_JOB_SUBMITTED: "job_submitted",
    events.EVENT_JOB_MAX_INSTANCES: "job_max_instances",
    events.EVENT_JOB_EXECUTED: "job_executed",
    events.EVENT_JOB_ERROR: "job_error",
    events.EVENT_JOB_MISSED: "job_missed",
}


def log_scheduler_event(event: events.SchedulerEvent):
    name = EVENT_NAMES.get(event.code, str(event.code))
    job_id = getattr(event, "job_id", None)
    scheduled = getattr(event, "scheduled_run_time", None)
    scheduled_times = getattr(event, "scheduled_run_times", None)
    details = "APScheduler event=%s job_id=%s scheduled=%s"
    args = (name, job_id, scheduled or scheduled_times)
    if event.code == events.EVENT_JOB_ERROR:
        logger.error(
            details + " exception=%s: %s traceback=%s",
            *args, type(event.exception).__name__, event.exception, event.traceback,
        )
    elif event.code in (events.EVENT_JOB_MISSED, events.EVENT_JOB_MAX_INSTANCES):
        logger.warning(details, *args)
    else:
        logger.info(details, *args)


@asynccontextmanager
async def task_execution(task_key: str):
    started_at = time.perf_counter()
    logger.info("Scheduled task started task_key=%s", task_key)
    try:
        yield
    except Exception as exc:
        logger.error(
            "Scheduled task finished task_key=%s result=failure duration_seconds=%.3f exception_type=%s",
            task_key, time.perf_counter() - started_at, type(exc).__name__,
        )
        raise
    logger.info(
        "Scheduled task finished task_key=%s result=success duration_seconds=%.3f",
        task_key, time.perf_counter() - started_at,
    )


async def refresh_daily_catalog():
    async with task_execution(CATALOG_TASK):
        async with SessionLocal() as db:
            await DailyCaseService(DailyCaseRepository(db)).refresh_catalog()


async def trigger_scheduled_pipeline(cron: str):
    async with task_execution(TRIGGER_TASK):
        async with SessionLocal() as db:
            result = await trigger_daily_pipeline(db, event="定时触发", cron=cron)
        if result["result"] == "触发失败":
            raise RuntimeError(f"Scheduled Daily dispatch failed; history_id={result['id']}")


def apply_schedule(scheduler: AsyncIOScheduler, task_key: str, function, schedule):
    job = scheduler.get_job(task_key)
    if schedule is None:
        if job is not None:
            job.remove()
            logger.info("Stopped scheduling task_key=%s", task_key)
        return
    expression, timezone = schedule
    name = f"{task_key} [{expression} {timezone}]"
    if job is not None and job.name == name:
        logger.debug("Task unchanged task_key=%s next_run=%s", task_key, job.next_run_time)
        return
    try:
        trigger = schedule_trigger(expression, timezone)
    except ValueError:
        logger.exception(
            "Invalid task schedule task_key=%s cron=%s timezone=%s",
            task_key, expression, timezone,
        )
        raise
    kwargs = {}
    if task_key == TRIGGER_TASK:
        kwargs["cron"] = expression
    job = scheduler.add_job(
        function, trigger, id=task_key, name=name,
        kwargs=kwargs,
        replace_existing=True, max_instances=1, coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
    )
    logger.info("%s next run: %s", name, job.next_run_time)


async def sync_tracking_issues():
    async with task_execution(ISSUE_SYNC_TASK):
        async with SessionLocal() as db:
            ids = list(await db.scalars(select(DailyPipelineIssue.id).where(
                DailyPipelineIssue.deleted_at.is_(None),
                DailyPipelineIssue.state == "open",
            )))
        logger.info("Issue synchronization selected count=%s", len(ids))
        failed = []
        synced = 0
        for identity in ids:
            try:
                async with SessionLocal() as db:
                    issue = await db.get(DailyPipelineIssue, identity)
                    if issue is None or issue.deleted_at is not None or issue.state != "open":
                        continue
                    tracking = DailyTrackingService(db)
                    await tracking.sync_issue(issue)
                    state = issue.state
                    if state == "open" and await tracking.close_resolved_issue(identity, issue.run_id):
                        state = "closed"
                    synced += 1
                    logger.info("Issue synchronization completed issue_id=%s number=%s state=%s", identity, issue.number, state)
            except Exception:
                logger.exception("Issue synchronization failed issue_id=%s", identity)
                failed.append(identity)
        logger.info("Issue synchronization summary selected=%s synced=%s failed=%s", len(ids), synced, len(failed))
        if failed:
            raise RuntimeError(f"Issue synchronization failed for {len(failed)} issue(s)")


async def advance_daily_processing():
    async with task_execution("advance_daily_processing"):
        async with SessionLocal() as db:
            issue_ids = list(await db.scalars(
                select(DailyPipelineIssue.id).join(Failure, Failure.issue_id == DailyPipelineIssue.id)
                .join(Node, Node.failure_id == Failure.id)
                .where(DailyPipelineIssue.state == "open", DailyPipelineIssue.deleted_at.is_(None),
                       Failure.problem_category == "code", Failure.processing_status.in_(ACTIVE),
                       ready_code_merge())
                .distinct()
            ))
        for identity in issue_ids:
            try:
                async with SessionLocal() as db:
                    await process_code_issue(DailyTrackingService(db), identity, deadline_only=True)
            except Exception:
                logger.exception("Daily code resolution failed issue_id=%s", identity)
        async with SessionLocal() as db:
            version_ids = await version_work_ids(db)
        for identity in version_ids:
            try:
                async with SessionLocal() as db:
                    await advance_version_flow(DailyTrackingService(db), identity)
            except Exception:
                logger.exception("Daily version flow check failed flow_id=%s", identity)
        async with SessionLocal() as db:
            await DailyTrackingService(db).poll_waiting()


def task_functions():
    return {
        CATALOG_TASK: refresh_daily_catalog,
        TRIGGER_TASK: trigger_scheduled_pipeline,
        ISSUE_SYNC_TASK: sync_tracking_issues,
        "advance_daily_processing": advance_daily_processing,
    }


async def reload_daily_schedules(scheduler: AsyncIOScheduler):
    async with SessionLocal() as db:
        schedules = await enabled_schedules(db)
    invalid = []
    for task_key, function in task_functions().items():
        try:
            apply_schedule(scheduler, task_key, function, schedules.get(task_key))
        except ValueError:
            invalid.append(task_key)
    if invalid:
        raise ValueError(f"Invalid Daily schedules: {', '.join(invalid)}")


async def reload_task_schedule(scheduler: AsyncIOScheduler, task_key: str):
    function = task_functions().get(task_key)
    if function is None:
        raise LookupError("Unknown Daily task")
    async with SessionLocal() as db:
        schedule = await enabled_schedule(db, task_key)
    apply_schedule(scheduler, task_key, function, schedule)
    job = scheduler.get_job(task_key)
    return {
        "taskKey": task_key,
        "enabled": job is not None,
        "cron": schedule[0] if schedule else None,
        "timeZone": schedule[1] if schedule else None,
        "nextRunTime": job.next_run_time.isoformat() if job and job.next_run_time else None,
    }


def create_control_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_daily_file_logging(settings.DATA_DIR, component="scheduler")
        setup_db_logging(level=logging.INFO, logger_prefixes=("daily_pipeline.",))
        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        scheduler.add_listener(log_scheduler_event, events.EVENT_ALL)
        app.state.scheduler = scheduler
        app.state.schedule_lock = asyncio.Lock()
        app.state.collector = DailyRunCollector(scheduler)
        app.state.processor = DailyProcessRunner(scheduler)
        scheduler.start(paused=True)
        try:
            try:
                await reload_daily_schedules(scheduler)
            except ValueError:
                logger.exception("Some Daily tasks could not be loaded at startup")
            async with watch_daily_recommendations(recover=True):
                await app.state.collector.restore()
                await app.state.processor.restore()
                scheduler.resume()
                logger.info("Daily scheduler ready; task configurations loaded at startup")
                yield
        finally:
            scheduler.shutdown()

    app = FastAPI(lifespan=lifespan)
    bearer = HTTPBearer(auto_error=False)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/internal/schedules/{task_key}/reload")
    async def reload_task(
        task_key: str,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        payload = decode_token(credentials.credentials) if credentials else None
        if not (
            payload
            and payload.get("token_type") == "service"
            and payload.get("sub") == SERVICE_SUBJECT
            and payload.get("purpose") == RELOAD_PURPOSE
        ):
            raise HTTPException(401, "Invalid scheduler service token")
        async with app.state.schedule_lock:
            try:
                result = await reload_task_schedule(app.state.scheduler, task_key)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, "Invalid Daily schedule") from exc
            except Exception as exc:
                logger.exception("Could not apply Daily schedule task_key=%s", task_key)
                raise HTTPException(503, "Could not apply Daily schedule") from exc
        logger.info(
            "Applied schedule change task_key=%s enabled=%s cron=%s next_run=%s",
            task_key, result["enabled"], result["cron"], result["nextRunTime"],
        )
        return result

    @app.post("/internal/collections/{run_id}/collect")
    async def queue_collection(
        run_id: int,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        payload = decode_token(credentials.credentials) if credentials else None
        if not (
            payload and payload.get("token_type") == "service"
            and payload.get("sub") == SERVICE_SUBJECT
            and payload.get("purpose") == COLLECTION_PURPOSE
        ):
            raise HTTPException(401, "Invalid collection service token")
        if run_id <= 0:
            raise HTTPException(422, "Invalid Run ID")
        async with SessionLocal() as db:
            root = await DailyCollectionRepository(db).get_root(run_id)
            if root is None:
                raise HTTPException(404, "Collection run not found")
        app.state.collector.register(run_id)
        return {"runId": run_id, "queued": True}

    @app.post("/internal/processing/{node_id}/run")
    async def queue_processing(
        node_id: int,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        payload = decode_token(credentials.credentials) if credentials else None
        if not (
            payload and payload.get("token_type") == "service"
            and payload.get("sub") == SERVICE_SUBJECT
            and payload.get("purpose") == PROCESSING_PURPOSE
        ):
            raise HTTPException(401, "Invalid processing service token")
        if node_id <= 0:
            raise HTTPException(422, "Invalid process node ID")
        from infrastructure.persistence.models import DailyPipelineProcessNode
        async with SessionLocal() as db:
            node = await db.get(DailyPipelineProcessNode, node_id)
            if node is None or node.stage_code not in ("revert_pr", "problem_pr_notification"):
                raise HTTPException(404, "Automatic process node not found")
        app.state.processor.register(node_id)
        return {"nodeId": node_id, "queued": True}

    return app


async def main():
    server = uvicorn.Server(uvicorn.Config(
        create_control_app(), host="0.0.0.0", port=9001, log_config=None,
    ))
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
