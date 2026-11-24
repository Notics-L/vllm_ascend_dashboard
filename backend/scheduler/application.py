"""
Scheduler 独立进程入口。
通过 `python -m scheduler` 启动，与 API 进程解耦。
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from infrastructure.core.github_audit import github_audit_writer

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("scheduler")

SHUTDOWN = False


def _handle_shutdown(signum, frame):
    global SHUTDOWN
    logger.info("Received %s, initiating graceful shutdown", signal.Signals(signum).name)
    SHUTDOWN = True


@github_audit_writer("scheduler")
async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_shutdown, sig, None)
        except NotImplementedError:
            signal.signal(sig, _handle_shutdown)

    from scheduler.leadership import SchedulerLeaderLease
    from scheduler.service import start_scheduler_async, stop_scheduler_async

    lease = SchedulerLeaderLease()
    active = False

    try:
        while not SHUTDOWN:
            try:
                leader = await lease.acquire_or_renew()
            except Exception as exc:
                logger.error("Scheduler leadership check failed: %s", exc, exc_info=True)
                leader = False

            if leader and not active:
                await start_scheduler_async()
                active = True
                logger.info("Scheduler became leader: %s", lease.owner)
            elif not leader and active:
                logger.warning("Scheduler leadership lost; stopping local APScheduler")
                await stop_scheduler_async()
                active = False

            await asyncio.sleep(10)
    finally:
        if active:
            logger.info("Shutting down Scheduler leader...")
            await stop_scheduler_async()
        try:
            await lease.release()
        except Exception as exc:
            logger.warning("Failed to release Scheduler leadership lease: %s", exc)
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
