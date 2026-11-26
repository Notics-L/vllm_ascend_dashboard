from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.v1.image_build import TriggerTaskPayload, _validate_trigger_task
from daily_pipeline.cron import schedule_trigger
from image_build.schedule_conflicts import BUILD_TIMEZONE, schedules_overlap


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        ("0 8 * * *", "0 8 * * *", True),
        ("0 8 * * *", "0 8 * * 1-5", True),
        ("*/15 8 * * *", "30 8 * * 1", True),
        ("*/15 8 * * *", "7 8 * * 1", False),
        ("0 8 * * 1", "0 8 * * 2", False),
        ("0 8 * * 0", "0 8 * * 7", True),
        ("0 8 15 * 1", "0 8 * * 1", True),
        ("0 8 15 * 1", "0 8 15 * *", True),
        ("0 8 29 2 *", "0 8 * 2 1", True),
        ("0 8 31 2 *", "0 8 * * *", False),
        ("0 8 last 2 *", "0 8 29 2 *", True),
    ],
)
def test_cron_overlap(first, second, overlap):
    assert schedules_overlap(schedule_trigger(first, BUILD_TIMEZONE), second) is overlap


@pytest.mark.asyncio
@pytest.mark.parametrize("cron_fields", [{}, {"cron_expression": None}, {"cron_expression": ""}, {"cron_expression": "   "}])
async def test_manual_task_skips_cron_conflict_lookup(cron_fields):
    workflow = SimpleNamespace(
        is_available=True,
        inputs_json=[{"name": "vllm_ascend_branch", "type": "string", "required": True}],
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value="example/builds"),
        get=AsyncMock(return_value=workflow),
        execute=AsyncMock(),
    )
    inputs = {"vllm_ascend_branch": "main"}
    payload = TriggerTaskPayload(workflow_branch="main", inputs=inputs, **cron_fields)

    assert await _validate_trigger_task(db, payload) == ("", "branch", inputs)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_cron_still_requires_valid_expression():
    payload = TriggerTaskPayload(workflow_branch="main", cron_expression="invalid")
    with pytest.raises(HTTPException) as error:
        await _validate_trigger_task(SimpleNamespace(), payload)

    assert error.value.status_code == 400
    assert "Cron" in error.value.detail


@pytest.mark.asyncio
async def test_cron_conflict_checks_only_consider_scheduled_tasks():
    workflow = SimpleNamespace(
        is_available=True,
        inputs_json=[{"name": "vllm_ascend_branch", "type": "string", "required": True}],
    )
    inputs = {"vllm_ascend_branch": "main"}
    existing_task = SimpleNamespace(id=4, cron_expression="0 8 * * *", inputs_json=inputs)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value="example/builds"),
        get=AsyncMock(return_value=workflow),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(existing_task, None)])),
    )
    payload = TriggerTaskPayload(workflow_branch="main", cron_expression=" 0 8  * * * ", inputs=inputs)

    with pytest.raises(HTTPException) as error:
        await _validate_trigger_task(db, payload)

    assert error.value.status_code == 409
    assert "任务 #4" in error.value.detail
    query = db.execute.await_args.args[0].compile()
    assert "image_build_trigger_tasks.cron_expression !=" in str(query)
    assert "" in query.params.values()
