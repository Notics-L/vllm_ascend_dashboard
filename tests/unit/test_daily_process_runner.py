"""Automatic processing is queued by events and survives transient failures."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from daily_pipeline import processing_scheduler as module


@asynccontextmanager
async def fake_session():
    yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(stage_code="revert_pr")))


@pytest.fixture
async def runner(monkeypatch):
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start(paused=True)
    execute = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "SessionLocal", fake_session)
    monkeypatch.setattr(module, "execute_revert", execute)
    value = module.DailyProcessRunner(scheduler)
    try:
        yield value, execute
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_registration_deduplicates_and_completion_unregisters(runner):
    value, execute = runner
    value.register(10)
    value.register(10)
    assert len(value.scheduler.get_jobs()) == 1
    assert value.scheduler.get_job("daily-processing-10").max_instances == 1
    await value.process(10)
    assert execute.await_count == 1 and execute.await_args.args[1] == 10
    assert value.scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_transient_error_retries_without_a_global_schedule(runner, monkeypatch):
    value, execute = runner
    monkeypatch.setattr(module, "PROCESSING_RETRY_SECONDS", 0.02)
    complete = asyncio.Event()
    calls = 0

    async def run(db, node_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary network error")
        complete.set()
        return True

    execute.side_effect = run
    value.register(10)
    value.scheduler.resume()
    await asyncio.wait_for(complete.wait(), timeout=3)
    assert calls == 2
    assert value.scheduler.get_job("daily-processing-10") is None


@pytest.mark.asyncio
async def test_pr_writes_are_serialized_across_failures(runner):
    value, execute = runner
    active = 0
    peak = 0

    async def run(db, node_id):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return True

    execute.side_effect = run
    await asyncio.gather(value.process(10), value.process(20))
    assert peak == 1


@pytest.mark.asyncio
async def test_restore_reconstructs_pending_and_running_node_jobs(runner, monkeypatch):
    value, _ = runner
    class Database:
        async def scalars(self, statement):
            params = statement.compile().params.values()
            assert "environment" in params
            assert list(module.ENVIRONMENT_RECOVERY_STAGES) in params
            return [10, 20]
    @asynccontextmanager
    async def session():
        yield Database()
    monkeypatch.setattr(module, "SessionLocal", session)
    await value.restore()
    assert {job.id for job in value.scheduler.get_jobs()} == {"daily-processing-10", "daily-processing-20"}


@pytest.mark.asyncio
async def test_runner_recovers_open_environment_nodes(runner, monkeypatch):
    value, execute_revert = runner
    complete = AsyncMock(return_value=True)

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(stage_code="locating")))

    monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(module, "DailyTrackingService", lambda db: SimpleNamespace(
        complete_environment_node=complete,
    ))
    value.register(10)
    await value.process(10)
    complete.assert_awaited_once_with(10)
    execute_revert.assert_not_awaited()
    assert value.scheduler.get_jobs() == []
