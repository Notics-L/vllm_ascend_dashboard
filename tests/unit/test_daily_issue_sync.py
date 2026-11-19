"""Periodic synchronization watches only local open Issues; reopening is a UI action."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from daily_pipeline import scheduler as module


@pytest.mark.asyncio
async def test_issue_selection_is_scoped_and_rechecks_deleted_records(monkeypatch):
    issues={1:SimpleNamespace(number=101,run_id=201,state="open",deleted_at=None),2:None,3:SimpleNamespace(number=103,state="open",deleted_at=datetime.now())}
    statements=[]
    class Database:
        async def scalars(self, statement):
            statements.append(str(statement.compile(compile_kwargs={"literal_binds":True})))
            return [1,2,3]
        async def get(self, model, identity):
            return issues[identity]
    @asynccontextmanager
    async def session():
        yield Database()
    sync=AsyncMock()
    monkeypatch.setattr(module,"SessionLocal",session)
    monkeypatch.setattr(module,"DailyTrackingService",lambda db:SimpleNamespace(sync_issue=sync, close_resolved_issue=AsyncMock(return_value=False)))
    await module.sync_tracking_issues()
    sync.assert_awaited_once_with(issues[1])
    assert "state = 'open'" in statements[0]
    assert "deleted_at IS NULL" in statements[0]
    assert "EXISTS" not in statements[0]


@pytest.mark.asyncio
async def test_closed_issue_is_skipped_even_if_a_stopped_flow_exists(monkeypatch):
    issue=SimpleNamespace(number=101,state="closed",deleted_at=None)
    class Database:
        async def scalars(self, statement):
            return [1]
        async def get(self, model, identity):
            return issue
        async def scalar(self, statement):
            return 1
    @asynccontextmanager
    async def session():
        yield Database()
    sync=AsyncMock()
    monkeypatch.setattr(module,"SessionLocal",session)
    monkeypatch.setattr(module,"DailyTrackingService",lambda db:SimpleNamespace(sync_issue=sync))
    await module.sync_tracking_issues()
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_issue_failure_does_not_skip_other_issues(monkeypatch):
    issues={i:SimpleNamespace(number=i,run_id=i,state="open",deleted_at=None) for i in (1,2)}
    class Database:
        async def scalars(self, statement):
            return [1,2]
        async def get(self, model, identity):
            return issues[identity]
    @asynccontextmanager
    async def session():
        yield Database()
    sync=AsyncMock(side_effect=[RuntimeError("source unavailable"),None])
    monkeypatch.setattr(module,"SessionLocal",session)
    monkeypatch.setattr(module,"DailyTrackingService",lambda db:SimpleNamespace(sync_issue=sync, close_resolved_issue=AsyncMock(return_value=False)))
    with pytest.raises(RuntimeError,match="1 issue"):
        await module.sync_tracking_issues()
    assert sync.await_count==2


@pytest.mark.asyncio
async def test_default_issue_cron_runs_on_five_minute_boundaries():
    scheduler=AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.start(paused=True)
    try:
        module.apply_schedule(scheduler,module.ISSUE_SYNC_TASK,module.sync_tracking_issues,("*/5 * * * *","Asia/Shanghai"))
        job=scheduler.get_job(module.ISSUE_SYNC_TASK)
        point=datetime(2026,11,18,0,3,tzinfo=ZoneInfo("Asia/Shanghai"))
        first=job.trigger.get_next_fire_time(None,point)
        second=job.trigger.get_next_fire_time(first,first)
        assert first.minute==5 and second.minute==10
        assert job.max_instances==1
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)
