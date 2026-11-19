"""Event-driven automatic processing, restored from durable nodes on startup."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from daily_pipeline.notifications import execute_notification
from daily_pipeline.revert import execute_revert
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.daily_pipeline_tracking import ACTIVE, AUTOMATIC_STAGES
from infrastructure.persistence.models import DailyPipelineFailure as Failure
from infrastructure.persistence.models import DailyPipelineProcessNode as Node

logger = logging.getLogger(__name__)
PROCESSING_RETRY_SECONDS = 60
MISFIRE_GRACE_SECONDS = 60


class DailyProcessRunner:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        # The dedicated scheduler serializes PR writes across failures as well
        # as nodes, so the same diagnosed PR can reuse an already-created revert.
        self.semaphore = asyncio.Semaphore(1)

    def register(self, node_id):
        job_id = f"daily-processing-{node_id}"
        point = datetime.now(UTC)
        if self.scheduler.get_job(job_id) is not None:
            self.scheduler.modify_job(job_id, next_run_time=point)
            return
        self.scheduler.add_job(
            self.process,
            "interval",
            seconds=PROCESSING_RETRY_SECONDS,
            id=job_id,
            name=job_id,
            kwargs={"node_id": node_id},
            next_run_time=point,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        logger.info("Automatic processing registered node_id=%s", node_id)

    async def process(self, node_id):
        try:
            async with self.semaphore:
                async with SessionLocal() as db:
                    node = await db.get(Node, node_id)
                    if node is None:
                        complete = True
                    elif node.stage_code == "problem_pr_notification":
                        complete = await execute_notification(db, node_id)
                    else:
                        complete = await execute_revert(db, node_id)
        except Exception:
            logger.exception("Automatic processing interrupted; will retry node_id=%s", node_id)
            raise
        if complete:
            job = self.scheduler.get_job(f"daily-processing-{node_id}")
            if job is not None:
                job.remove()
            logger.info("Automatic processing unregistered node_id=%s", node_id)

    async def restore(self):
        async with SessionLocal() as db:
            node_ids = list(
                await db.scalars(
                    select(Node.id)
                    .join(Failure, Failure.id == Node.failure_id)
                    .where(
                        Failure.processing_status.in_(ACTIVE),
                        Node.stage_code.in_(AUTOMATIC_STAGES),
                        Node.status.in_(("pending", "running", "waiting")),
                    )
                )
            )
        for node_id in node_ids:
            self.register(node_id)
        logger.info("Automatic processing restored count=%s", len(node_ids))
