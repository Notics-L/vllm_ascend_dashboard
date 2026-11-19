"""Issue closure checkpoints and explicit page-driven workflow restoration."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from infrastructure.persistence import daily_pipeline_tracking as module


def node(identity, status, stage="classify"):
    return SimpleNamespace(id=identity, status=status, stage_code=stage,
                           finished_at=None, next_run_at=None, updated_at=None)


def failure(status="running"):
    return SimpleNamespace(id=1, processing_status=status, end_reason=None,
                           ended_at=None, replaced_by_id=None, resume_state=None)


@pytest.mark.asyncio
async def test_close_checkpoints_unfinished_nodes_without_changing_completed_work():
    completed = node(10, "completed")
    waiting = node(11, "waiting", "problem_pr_notification")
    pending = node(12, "pending", "pr_merge")
    due = datetime(2026, 11, 18, 12)
    waiting.next_run_at = due
    execution = SimpleNamespace(id=20, status="waiting", finished_at=None)
    db = SimpleNamespace(scalars=AsyncMock(side_effect=[[completed, waiting, pending], [execution]]),
                         execute=AsyncMock())
    row = failure()
    await module.end_failure(db, row, "issue_closed")
    assert row.processing_status == "ended" and row.end_reason == "issue_closed"
    assert completed.status == "completed"
    assert waiting.status == pending.status == "ended"
    assert waiting.next_run_at is None
    assert row.resume_state == {
        "status": "running",
        "nodes": [{"id": 11, "status": "waiting", "finished_at": None, "next_run_at": due.isoformat()},
                  {"id": 12, "status": "pending", "finished_at": None, "next_run_at": None}],
        "executions": [{"id": 20, "status": "waiting", "finished_at": None}],
    }
    original = row.resume_state
    await module.end_failure(db, row, "issue_closed")
    assert row.resume_state is original
    assert db.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [False, True])
async def test_resume_reuses_waiting_notification_without_new_delivery(monkeypatch, claimed):
    row = failure("ended")
    row.end_reason = "issue_closed"
    row.ended_at = datetime(2026, 11, 18)
    row.resume_state = {"status": "running", "nodes": [{"id": 11, "status": "waiting"}],
                        "executions": [{"id": 20, "status": "waiting"}]}
    completed = node(10, "completed")
    waiting = node(11, "ended", "problem_pr_notification")
    receipt = {"delivery_mode": "pull", "ready": True, "delivery_started": claimed,
               "receipt_hash": "original-hash", "claimed_by": "original-consumer"}
    execution = SimpleNamespace(id=20, status="ended", request_data=receipt.copy(),
                                result_data={}, finished_at=datetime(2026, 11, 18))
    db = SimpleNamespace(scalars=AsyncMock(side_effect=[[row], [completed, waiting]]),
                         scalar=AsyncMock(return_value=execution))
    monkeypatch.setattr(module, "latest_failure_for_case", AsyncMock(return_value=row))
    await module.resume_issue_failures(db, SimpleNamespace(id=7))
    assert completed.status == "completed"
    assert waiting.status == execution.status == "waiting"
    assert waiting.finished_at is execution.finished_at is None
    assert execution.request_data == receipt
    assert row.processing_status == "running"
    assert row.end_reason is row.ended_at is row.resume_state is None
    if claimed:
        assert execution.result_data["delivery_status"] == "unknown"
    else:
        assert execution.result_data == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("previous,restored", [("pending", "pending"), ("running", "pending"), ("failed", "failed")])
async def test_resume_restores_only_unfinished_stage(monkeypatch, previous, restored):
    row = failure("ended")
    row.resume_state = {"status": "pending", "nodes": [{"id": 11, "status": previous}]}
    current = node(11, "ended" if previous != "failed" else "failed", "locating")
    db = SimpleNamespace(scalars=AsyncMock(side_effect=[[row], [current]]), scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(module, "latest_failure_for_case", AsyncMock(return_value=row))
    await module.resume_issue_failures(db, SimpleNamespace(id=7))
    assert current.status == restored
    assert row.processing_status == "pending"


@pytest.mark.asyncio
async def test_newer_failure_prevents_restoring_superseded_work(monkeypatch):
    row = failure("ended")
    row.resume_state = {"status": "running"}
    db = SimpleNamespace(scalars=AsyncMock(return_value=[row]))
    monkeypatch.setattr(module, "latest_failure_for_case", AsyncMock(return_value=SimpleNamespace(id=2)))
    await module.resume_issue_failures(db, SimpleNamespace(id=7))
    assert row.processing_status == "ended" and row.end_reason == "superseded"
    assert row.replaced_by_id == 2 and row.resume_state is None
    assert db.scalars.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("page_action", [False, True])
async def test_background_save_cannot_resume_but_page_action_can(monkeypatch, page_action):
    issue = SimpleNamespace(id=7, state="closed", number=101, closed_at=datetime(2026, 11, 18))
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[1, 1, issue]),
                         scalars=AsyncMock(return_value=[]), execute=AsyncMock())
    resume = AsyncMock()
    monkeypatch.setattr(module, "resume_issue_failures", resume)
    monkeypatch.setattr(module, "reconcile_case_availability", AsyncMock())
    await module.save_issue(db, "owner/repo", 100, {"number": 101, "state": "open"}, allow_reopen=page_action)
    assert issue.state == ("open" if page_action else "closed")
    assert (issue.closed_at is None) == page_action
    assert resume.await_count == int(page_action)
