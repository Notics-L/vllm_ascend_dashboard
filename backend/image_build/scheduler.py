"""Run image build workflow synchronization and collection in a dedicated process."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from tempfile import gettempdir
from time import monotonic, time

from sqlalchemy import select

from image_build.collection import collect_runs_forever
from image_build.workflow_sync import (
    ImageBuildWorkflowSync,
    WorkflowSyncCredentialError,
    WorkflowSyncInProgress,
)
from infrastructure.core.github_audit import github_audit_writer
from infrastructure.db.base import SessionLocal, engine
from infrastructure.persistence.models.image_build_schedule import (
    WORKFLOW_SYNC_TASK,
    ImageBuildScheduledTask,
)
from scheduler.leadership import SchedulerLeaderLease

LEASE_NAME = WORKFLOW_SYNC_TASK
LEASE_RENEW_INTERVAL = 10
LEASE_REQUEST_TIMEOUT = 5
SCHEDULE_POLL_INTERVAL = 10
SCHEDULE_READ_TIMEOUT = 5
HEARTBEAT_MAX_AGE = 45
HEARTBEAT_PATH = Path(gettempdir()) / "image-build-scheduler.heartbeat"

logger = logging.getLogger(__name__)


async def read_workflow_sync_interval() -> int | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(ImageBuildScheduledTask.interval_minutes).where(
                ImageBuildScheduledTask.task_key == WORKFLOW_SYNC_TASK,
                ImageBuildScheduledTask.enabled.is_(True),
            )
        )


async def scan_workflows(sync: ImageBuildWorkflowSync) -> None:
    last_finished_at = None
    while True:
        try:
            interval_minutes = await asyncio.wait_for(
                read_workflow_sync_interval(), SCHEDULE_READ_TIMEOUT,
            )
        except Exception:
            logger.exception("Could not read the image build workflow schedule")
        else:
            if interval_minutes is not None and (
                last_finished_at is None or monotonic() - last_finished_at >= interval_minutes * 60
            ):
                try:
                    await sync.run()
                except WorkflowSyncInProgress:
                    logger.info("Image build workflow scan already in progress")
                except WorkflowSyncCredentialError as exc:
                    logger.info("Image build workflow scan skipped: %s", exc)
                except Exception:
                    logger.exception("Image build workflow scan failed")
                finally:
                    last_finished_at = monotonic()
        await asyncio.sleep(SCHEDULE_POLL_INTERVAL)


async def run_leader_jobs(sync: ImageBuildWorkflowSync) -> None:
    async with asyncio.TaskGroup() as jobs:
        jobs.create_task(scan_workflows(sync), name="image-build-workflow-sync")
        jobs.create_task(collect_runs_forever(), name="image-build-collection")


async def run_scheduler(stop: asyncio.Event) -> None:
    lease = SchedulerLeaderLease(lease_name=LEASE_NAME)
    sync = ImageBuildWorkflowSync()
    scan: asyncio.Task[None] | None = None

    HEARTBEAT_PATH.unlink(missing_ok=True)
    try:
        while not stop.is_set():
            try:
                leader = await asyncio.wait_for(lease.acquire_or_renew(), LEASE_REQUEST_TIMEOUT)
            except Exception:
                logger.exception("Image build scheduler lease check failed")
                leader = False
            else:
                # A standby also needs to report that its lease loop is healthy.
                HEARTBEAT_PATH.touch()

            if stop.is_set():
                break
            if leader and scan is None:
                scan = asyncio.create_task(run_leader_jobs(sync), name=LEASE_NAME)
                logger.info("Image build scheduler became leader: %s", lease.owner)
            elif not leader and scan is not None:
                scan.cancel()
                await asyncio.gather(scan, return_exceptions=True)
                scan = None
                logger.warning("Image build scheduler lost its lease; scan stopped")

            try:
                await asyncio.wait_for(stop.wait(), LEASE_RENEW_INTERVAL)
            except TimeoutError:
                continue
    finally:
        HEARTBEAT_PATH.unlink(missing_ok=True)
        if scan is not None:
            scan.cancel()
            await asyncio.gather(scan, return_exceptions=True)
        try:
            await asyncio.wait_for(lease.release(), LEASE_REQUEST_TIMEOUT)
        except Exception:
            logger.exception("Could not release the image build scheduler lease")


def is_healthy() -> bool:
    try:
        return time() - HEARTBEAT_PATH.stat().st_mtime <= HEARTBEAT_MAX_AGE
    except FileNotFoundError:
        return False


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    try:
        async with github_audit_writer("image_build"):
            await run_scheduler(stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["--healthcheck"]:
        sys.exit(0 if is_healthy() else 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
