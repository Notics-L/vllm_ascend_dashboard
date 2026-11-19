"""Run-scoped collectors continue without a global collection Cron."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from daily_pipeline import collection_scheduler as module


@asynccontextmanager
async def fake_session():
    yield object()


@pytest.fixture
async def collector(monkeypatch):
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start(paused=True)
    service = SimpleNamespace(collect=AsyncMock(return_value=False))
    monkeypatch.setattr(module, "SessionLocal", fake_session)
    monkeypatch.setattr(module, "DailyCollectionService", lambda repository: service)
    value = module.DailyRunCollector(scheduler)
    try:
        yield value, service
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_registration_is_immediate_recurring_and_idempotent(collector):
    value, _ = collector
    before = datetime.now(UTC)
    value.register(101)
    job = value.scheduler.get_job("daily-collection-101")
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval.total_seconds() == 60
    assert job.next_run_time >= before
    assert job.max_instances == 1
    value.register(101)
    assert value.scheduler.get_jobs() == [job]


@pytest.mark.asyncio
async def test_only_completed_run_is_unregistered(collector):
    value, service = collector
    value.register(101)
    value.register(102)
    await value.collect(101)
    assert value.scheduler.get_job("daily-collection-101") is not None
    service.collect.return_value = True
    await value.collect(101)
    assert value.scheduler.get_job("daily-collection-101") is None
    assert value.scheduler.get_job("daily-collection-102") is not None


@pytest.mark.asyncio
async def test_transient_collection_error_keeps_retry_registered(collector):
    value, service = collector
    value.register(101)
    service.collect.side_effect = RuntimeError("GitHub temporarily unavailable")
    with pytest.raises(RuntimeError):
        await value.collect(101)
    assert value.scheduler.get_job("daily-collection-101") is not None


@pytest.mark.asyncio
async def test_scheduler_actually_retries_until_complete(collector, monkeypatch):
    value, service = collector
    monkeypatch.setattr(module, "COLLECTION_INTERVAL_SECONDS", 0.02)
    completed = asyncio.Event()
    calls = 0

    async def collect(run_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient GitHub error")
        if calls == 2:
            return False  # Main run or a dispatched Nightly is still running.
        completed.set()
        return True

    service.collect.side_effect = collect
    value.register(101)
    value.scheduler.resume()
    await asyncio.wait_for(completed.wait(), timeout=3)
    assert calls == 3
    assert value.scheduler.get_job("daily-collection-101") is None


@pytest.mark.asyncio
async def test_repeated_notification_does_not_overlap_an_active_collector(collector, monkeypatch):
    value, service = collector
    monkeypatch.setattr(module, "COLLECTION_INTERVAL_SECONDS", 0.02)
    started, finish, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def collect(run_id):
        started.set()
        await finish.wait()
        completed.set()
        return True

    service.collect.side_effect = collect
    value.register(101)
    value.scheduler.resume()
    await asyncio.wait_for(started.wait(), timeout=3)
    value.register(101)
    await asyncio.sleep(0.06)
    assert service.collect.await_count == 1
    finish.set()
    await asyncio.wait_for(completed.wait(), timeout=3)
    assert value.scheduler.get_job("daily-collection-101") is None


@pytest.mark.asyncio
async def test_startup_recovers_unfinished_runs_without_a_global_task(collector, monkeypatch):
    value, _ = collector
    repository = SimpleNamespace(pending_run_ids=AsyncMock(return_value=[101, 102]))
    monkeypatch.setattr(module, "DailyCollectionRepository", lambda db: repository)
    await value.restore()
    assert {job.id for job in value.scheduler.get_jobs()} == {"daily-collection-101", "daily-collection-102"}
    assert value.scheduler.get_job("collect_daily_runs") is None
