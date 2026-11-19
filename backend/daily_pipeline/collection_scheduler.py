"""Own a recurring collection job for each unfinished Daily workflow run."""
import asyncio
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.daily_pipeline_collection import DailyCollectionRepository

from daily_pipeline.collection import DailyCollectionService

logger = logging.getLogger(__name__)
COLLECTION_INTERVAL_SECONDS = 60
COLLECTION_CONCURRENCY = 3
MISFIRE_GRACE_SECONDS = 60


class DailyRunCollector:
    """In-memory jobs are reconstructed from unfinished persisted runs on startup."""

    def __init__(self, scheduler: AsyncIOScheduler):
        self.scheduler = scheduler
        self.semaphore = asyncio.Semaphore(COLLECTION_CONCURRENCY)

    def register(self, run_id: int):
        job_id = f"daily-collection-{run_id}"
        point = datetime.now(UTC)
        job = self.scheduler.get_job(job_id)
        if job is not None:
            # A manual refresh wakes the existing job without replacing its
            # instance limit or creating a second collector for the same run.
            self.scheduler.modify_job(job_id, next_run_time=point)
            logger.info("Daily collection requested again run_id=%s", run_id)
            return
        self.scheduler.add_job(
            self.collect, "interval", seconds=COLLECTION_INTERVAL_SECONDS,
            id=job_id, name=job_id, kwargs={"run_id": run_id},
            next_run_time=point, replace_existing=False,
            max_instances=1, coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        logger.info(
            "Daily recurring collection registered run_id=%s interval_seconds=%s",
            run_id, COLLECTION_INTERVAL_SECONDS,
        )

    async def collect(self, run_id: int):
        try:
            async with self.semaphore:
                async with SessionLocal() as db:
                    complete = await DailyCollectionService(DailyCollectionRepository(db)).collect(run_id)
        except Exception:
            # Keep the interval job registered so a transient GitHub or database
            # error does not silently stop collection for this run.
            logger.exception(
                "Daily recurring collection failed; will retry run_id=%s interval_seconds=%s",
                run_id, COLLECTION_INTERVAL_SECONDS,
            )
            raise
        if complete:
            job = self.scheduler.get_job(f"daily-collection-{run_id}")
            if job is not None:
                job.remove()
            logger.info("Daily recurring collection unregistered run_id=%s reason=complete_or_deleted", run_id)

    async def restore(self):
        async with SessionLocal() as db:
            run_ids = await DailyCollectionRepository(db).pending_run_ids()
        for run_id in run_ids:
            self.register(run_id)
        logger.info("Daily recurring collections restored count=%s", len(run_ids))
