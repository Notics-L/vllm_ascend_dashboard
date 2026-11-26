import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from image_build import scheduler


@pytest.mark.asyncio
async def test_workflow_scan_reloads_enabled_state_and_interval(monkeypatch):
    # Each poll advances the monotonic clock by 40 seconds. Disabled rows are
    # returned as None. Shortening the interval takes effect on the next poll.
    clock = 0
    intervals = iter((None, 10, 10, None, 1, 1, 1))
    started_at = []

    async def load_interval():
        try:
            return next(intervals)
        except StopIteration:
            raise asyncio.CancelledError from None

    async def sleep(_):
        nonlocal clock
        clock += 40

    async def run():
        started_at.append(clock)

    monkeypatch.setattr(scheduler, "read_workflow_sync_interval", load_interval)
    monkeypatch.setattr(scheduler, "monotonic", lambda: clock)
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.scan_workflows(SimpleNamespace(run=run))

    assert started_at == [40, 160, 240]


@pytest.mark.asyncio
async def test_schedule_read_failure_does_not_launch_a_scan(monkeypatch):
    read_interval = AsyncMock(side_effect=[OSError("database unavailable"), asyncio.CancelledError])
    sync = SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(scheduler, "read_workflow_sync_interval", read_interval)
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await scheduler.scan_workflows(sync)

    sync.run.assert_not_awaited()
