import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from collector.ci import CICollector
from contracts.schemas import CIJobResponse, CIResultResponse, WorkflowConfigResponse
from infrastructure.persistence.models import CIJob
from tooling.workflow_rules import matches_workflow_policy


def test_workflow_policy_matches_beijing_overnight_window_and_raw_name():
    rule = (21, 3, r"^Nightly-A3 [(]scheduled[)]$")
    assert matches_workflow_policy(datetime(2026, 9, 28, 15, tzinfo=UTC), "Nightly-A3 (scheduled)", *rule)
    assert matches_workflow_policy(datetime(2026, 9, 28, 18, tzinfo=UTC), "nightly-a3 (SCHEDULED)", *rule)
    assert not matches_workflow_policy(datetime(2026, 9, 28, 19, tzinfo=UTC), "Nightly-A3 (scheduled)", *rule)
    assert not matches_workflow_policy(datetime(2026, 9, 28, 15, tzinfo=UTC), "Nightly-A3 (manual)", *rule)


def test_unconfigured_policy_does_not_filter():
    assert matches_workflow_policy(None, "any workflow", None, None, None)
    assert matches_workflow_policy(None, "any workflow", 21, None, " ")
    assert not matches_workflow_policy(None, "name", None, None, "[")


def test_job_api_exposes_logical_name_only():
    assert "workflow_name" in CIJobResponse.model_fields
    assert "actual_workflow_name" not in CIJobResponse.model_fields
    assert "actual_workflow_name" not in CIResultResponse.model_fields
    assert "materialize_name_regex" in WorkflowConfigResponse.model_fields


@pytest.mark.asyncio
async def test_ci_collector_filters_raw_name_before_persisting_workflow_or_jobs():
    github = SimpleNamespace(
        get_workflow_runs=AsyncMock(return_value=[
            {"id": 1, "name": "Nightly-A3 (manual)", "created_at": "2026-09-28T15:00:00Z", "event": "schedule"},
            {"id": 2, "name": "Nightly-A3 (scheduled)", "created_at": "2026-09-28T11:00:00Z", "event": "schedule"},
            {"id": 3, "name": "Nightly-A3 (scheduled)", "created_at": "2026-09-28T15:00:00Z", "event": "schedule"},
        ])
    )
    db = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    collector = CICollector(github, db)  # type: ignore[arg-type]
    collector._get_last_synced_run_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    collector._save_ci_result = AsyncMock(return_value=True)  # type: ignore[method-assign]
    collector._collect_jobs = AsyncMock()  # type: ignore[method-assign]
    progress = SimpleNamespace(update_collected_count=lambda _: None)

    count = await collector._collect_single_workflow(
        workflow_file="nightly-a3.yml",
        since=datetime(2026, 9, 27, tzinfo=UTC),
        progress=progress,
        max_runs=100,
        hardware="A3",
        stats_start_hour=21,
        stats_end_hour=3,
        materialize_name_regex=r"^Nightly-A3 [(]scheduled[)]$",
    )

    assert count == 1
    assert [call.args[0]["id"] for call in collector._save_ci_result.await_args_list] == [3]
    assert [call.args[0] for call in collector._collect_jobs.await_args_list] == [3]


@pytest.mark.asyncio
async def test_failed_parent_save_never_collects_jobs():
    run = {"id": 4, "name": "Nightly-A3", "created_at": "2026-09-28T15:00:00Z", "event": "schedule"}
    github = SimpleNamespace(get_workflow_runs=AsyncMock(return_value=[run]))
    db = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())
    collector = CICollector(github, db)  # type: ignore[arg-type]
    collector._get_last_synced_run_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    collector._save_ci_result = AsyncMock(side_effect=RuntimeError("parent save failed"))  # type: ignore[method-assign]
    collector._collect_jobs = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="parent save failed"):
        await collector._collect_single_workflow(
            workflow_file="nightly-a3.yml",
            since=datetime(2026, 9, 27, tzinfo=UTC),
            progress=SimpleNamespace(update_collected_count=lambda _: None),
        )

    db.flush.assert_not_awaited()
    collector._collect_jobs.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_parent_flush_never_collects_jobs():
    run = {"id": 5, "name": "Nightly-A3", "created_at": "2026-09-28T15:00:00Z", "event": "schedule"}
    github = SimpleNamespace(get_workflow_runs=AsyncMock(return_value=[run]))
    db = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock(side_effect=RuntimeError("insert failed")))
    collector = CICollector(github, db)  # type: ignore[arg-type]
    collector._get_last_synced_run_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    collector._save_ci_result = AsyncMock(return_value=True)  # type: ignore[method-assign]
    collector._collect_jobs = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="insert failed"):
        await collector._collect_single_workflow(
            workflow_file="nightly-a3.yml",
            since=datetime(2026, 9, 27, tzinfo=UTC),
            progress=SimpleNamespace(update_collected_count=lambda _: None),
        )

    collector._collect_jobs.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_collection_rejects_missing_parent_instead_of_using_filename():
    github = SimpleNamespace(get_job_list=AsyncMock(return_value=[{"id": 501}]))
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    collector = CICollector(github, db)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="parent CIResult is missing"):
        await collector._collect_jobs(5, {"id": 5}, "nightly-a3.yml", "A3")

    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_parent_creation_error_raises_instead_of_returning_false():
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "Nightly-A3")),
        add=Mock(),
    )
    collector = CICollector(None, db)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="Failed to create CI result"):
        await collector._create_ci_result(
            {"id": 6, "name": "Nightly-A3", "unexpected": {"not JSON serializable"}},
            "nightly-a3.yml",
            "A3",
        )

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_job_keeps_logical_name_and_original_github_payload():
    db = SimpleNamespace(add=Mock())
    github = SimpleNamespace(owner="vllm-project", repo="vllm-ascend")
    collector = CICollector(github, db)  # type: ignore[arg-type]
    payload = {
        "id": 501, "name": "sample job", "workflow_name": "Nightly-A3 (scheduled)",
        "status": "completed", "conclusion": "failure",
    }

    assert await collector._create_ci_job(payload, 42, "Nightly-A3", "A3")
    saved = db.add.call_args.args[0]
    assert isinstance(saved, CIJob)
    assert saved.run_id == 42
    assert saved.workflow_name == "Nightly-A3"
    assert json.loads(saved.data)["workflow_name"] == "Nightly-A3 (scheduled)"

    payload["workflow_name"] = "Nightly-A3 (manual)"
    assert await collector._update_ci_job(saved, payload, "Nightly-A3", "A3")
    assert saved.workflow_name == "Nightly-A3"
    assert json.loads(saved.data)["workflow_name"] == "Nightly-A3 (manual)"


@pytest.mark.asyncio
async def test_scheduled_ci_sync_does_not_use_config_event_as_api_filter():
    config = SimpleNamespace(
        workflow_file="nightly-a3.yml",
        hardware="A3",
        event="schedule",
        actor=None,
        stats_start_hour=21,
        stats_end_hour=3,
        materialize_name_regex=r"^Nightly-A3 [(]scheduled[)]$",
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [config]),
    )))
    collector = CICollector(None, db)  # type: ignore[arg-type]
    collector._persist_progress = AsyncMock()  # type: ignore[method-assign]
    collector._collect_single_workflow = AsyncMock(return_value=1)  # type: ignore[method-assign]

    assert await collector.collect_workflow_runs(days_back=1) == 1
    kwargs = collector._collect_single_workflow.await_args.kwargs
    assert kwargs["workflow_file"] == "nightly-a3.yml"
    assert kwargs["event"] is None
    assert kwargs["stats_start_hour"] == 21
    assert kwargs["stats_end_hour"] == 3
    assert kwargs["materialize_name_regex"] == r"^Nightly-A3 [(]scheduled[)]$"
