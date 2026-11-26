from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from api.v1 import image_build
from api.v1.image_build import TriggerTaskPayload, _validate_trigger_task, trigger_task
from daily_pipeline.source_recommendation import SourceRecommendation
from image_build import trigger
from infrastructure.persistence.models import ImageBuildWorkflowDefinition


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "has_recommendation"), [
    ("recommended", True), ("recommended", False), ("branch", False), ("manual", False),
])
async def test_save_recommended_mode_requires_sha_for_selected_source_branch(monkeypatch, mode, has_recommendation):
    inputs = {"vllm_ascend_branch": "refs/heads/release/test"}
    if mode == "manual":
        inputs["source_ref"] = "refs/pull/17/head"
    workflow = SimpleNamespace(
        is_available=True,
        inputs_json=[
            {"name": "vllm_ascend_branch", "type": "string", "required": True},
            {"name": "source_ref", "type": "string", "required": False},
        ],
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value="example/builds"),
        get=AsyncMock(return_value=workflow),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
    )
    payload = TriggerTaskPayload(
        workflow_branch="main", cron_expression="0 8 * * *", source_ref_mode=mode, inputs=inputs,
    )

    recommendation = SimpleNamespace(commit_sha="a" * 40) if has_recommendation else None
    read_recommendation = AsyncMock(return_value=recommendation)
    monkeypatch.setattr(image_build, "get_source_recommendation", read_recommendation)

    if mode == "recommended" and not has_recommendation:
        with pytest.raises(HTTPException) as error:
            await _validate_trigger_task(db, payload)
        assert error.value.status_code == 400
        assert error.value.detail == trigger.NO_SOURCE_RECOMMENDATION
        db.execute.assert_not_awaited()
    else:
        _, saved_mode, saved_inputs = await _validate_trigger_task(db, payload)
        assert saved_mode == mode
        assert saved_inputs == inputs

    if mode == "recommended":
        read_recommendation.assert_awaited_once_with(db, "release/test", repository="example/builds")
        assert "source_ref" not in inputs
    else:
        read_recommendation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_branch", ["main", "release/test"])
async def test_recommended_task_without_sha_does_not_dispatch(monkeypatch, source_branch):
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None), add=Mock(),
        flush=AsyncMock(), refresh=AsyncMock(), commit=AsyncMock(),
    )
    task = SimpleNamespace(
        id=1, workflow_branch="main", source_ref_mode="recommended",
        inputs_json={"vllm_ascend_branch": source_branch},
    )
    read_recommendation = AsyncMock(return_value=None)
    get_token = AsyncMock()
    monkeypatch.setattr(trigger, "get_source_recommendation", read_recommendation)
    monkeypatch.setattr(trigger, "get_github_token", get_token)

    record = await trigger.submit_trigger_task(
        db, task, "tester", repository="example/builds", supports_source_ref=True, source_branch=source_branch,
    )

    assert record.status == "failed"
    assert record.message == trigger.NO_SOURCE_RECOMMENDATION
    read_recommendation.assert_awaited_once_with(db, source_branch, repository="example/builds")
    get_token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pr_number", [21, None])
@pytest.mark.parametrize("source_branch", ["main", "release/test"])
async def test_trigger_uses_configured_repository_for_workflow_recommendation_and_history(
    monkeypatch, pr_number, source_branch,
):
    repository = "example/builds"
    task = SimpleNamespace(
        id=7, workflow_branch="main", source_ref_mode="recommended",
        inputs_json={"vllm_ascend_branch": source_branch},
    )
    workflow = SimpleNamespace(is_available=True, inputs_json=[
        {"name": "vllm_ascend_branch", "type": "string", "required": True},
        {"name": "source_ref", "type": "string", "required": False},
    ])

    def capture_record(record):
        record.id = 10
        record.triggered_at = datetime.now(UTC)

    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[" Example/Builds ", None]),
        get=AsyncMock(side_effect=[task, workflow]),
        add=Mock(side_effect=capture_record), flush=AsyncMock(), refresh=AsyncMock(), commit=AsyncMock(),
    )
    read_recommendation = AsyncMock(return_value=SourceRecommendation(
        commit_sha="recommended-sha", pr_number=pr_number, committed_at="2026-11-26T08:00:00Z",
        branch=source_branch, trigger_history_id=7, problem_pr_number=None, repository=repository,
    ))
    dispatch = AsyncMock(return_value={"success": True, "status_code": 200, "run_id": 9})
    monkeypatch.setattr(trigger, "get_source_recommendation", read_recommendation)
    monkeypatch.setattr(trigger, "get_github_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(trigger, "dispatch_workflow", dispatch)

    result = await trigger_task(7, db, SimpleNamespace(username="tester"))

    assert db.scalar.await_args_list[0].args[0].compile().params == {"config_key_1": "repository"}
    assert db.get.await_args.args == (ImageBuildWorkflowDefinition, (repository, "main"))
    read_recommendation.assert_awaited_once_with(db, source_branch, repository=repository)
    assert dispatch.await_args.kwargs["repository"] == repository
    assert dispatch.await_args.kwargs["inputs"]["source_ref"] == "recommended-sha"
    snapshot = db.add.call_args.args[0].dispatch_snapshot
    assert snapshot["repository"] == repository
    assert snapshot["source_recommendation"]["pr_number"] == pr_number
    assert snapshot["source_recommendation"]["committed_at"] == "2026-11-26T08:00:00Z"
    assert result.run_url == "https://github.com/example/builds/actions/runs/9"


@pytest.mark.asyncio
async def test_trigger_rejects_missing_repository_configuration():
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        get=AsyncMock(return_value=SimpleNamespace(workflow_branch="main")),
        commit=AsyncMock(),
    )

    with pytest.raises(HTTPException) as error:
        await trigger_task(7, db, SimpleNamespace(username="tester"))

    assert error.value.status_code == 503
    assert "image_build_config" in error.value.detail
    assert db.get.await_count == 1
