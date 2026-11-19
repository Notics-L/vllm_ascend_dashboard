from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from api.deps import get_current_user, get_db
from api.v1.workflow_actions import router
from infrastructure.persistence.models import User, WorkflowActionSchedule
from workflow_actions.schemas import (
    WorkflowActionPermissionCreate,
    WorkflowActionPermissionUpdate,
    WorkflowActionRequest,
)
from workflow_actions.service import (
    build_command,
    execute_workflow_action,
    parse_workflow_inputs,
)


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, permission=None):
        self.permission = permission
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return FakeScalarResult(self.permission)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def mock_dedicated_token(monkeypatch):
    async def get_token(db):
        return "actions-test-token"

    monkeypatch.setattr("workflow_actions.service.get_github_token", get_token)


def dispatch_request(**overrides):
    values = {
        "action": "dispatch",
        "workflow_path": ".github/workflows/schedule_nightly_test_a3.yaml",
        "ref": "main",
        "inputs_yaml": "download_path:\n  default: /tmp/result\n",
    }
    values.update(overrides)
    return WorkflowActionRequest(**values)


@pytest.mark.asyncio
async def test_schedule_viewer_can_read_details_but_cannot_manage():
    payload = {
        "name": "A3 nightly",
        "workflow_path": ".github/workflows/schedule_nightly_test_a3.yaml",
        "ref": "main",
        "inputs_yaml": "test_cases:\n  default: all\n",
        "cron_expression": "0 9 * * 1",
        "enabled": True,
    }
    schedule = WorkflowActionSchedule(
        id=1, last_output="GitHub 返回：204", last_submission_status="submitted", **payload
    )
    db = AsyncMock()
    db.execute.return_value = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [schedule]
    db.execute.return_value.scalar_one_or_none.return_value = None
    app = FastAPI()
    app.include_router(router, prefix="/workflow-actions")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: User(id=7, username="viewer")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        permission = await client.get("/workflow-actions/schedule-permission")
        assert permission.json() == {"allowed": False}

        response = await client.get("/workflow-actions/schedules")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["inputs_yaml"] == payload["inputs_yaml"]
        assert item["last_output"] == schedule.last_output
        assert item["last_submission_status"] == "submitted"
        assert item["cron_preview_expression"] == "0 9 * * tue"
        assert item["next_run_at"] is not None

        response = await client.get(
            "/workflow-actions/schedules/cron-preview", params={"expression": "0 9 * * 6"}
        )
        assert response.json() == {"expression": "0 9 * * sun"}

        response = await client.get(
            "/workflow-actions/schedules/cron-preview", params={"expression": "0 9 * * 7"}
        )
        assert response.status_code == 400

        for method, path, body in [
            ("POST", "/schedules", payload),
            ("PUT", "/schedules/1", payload),
            ("PATCH", "/schedules/1/enabled", {"enabled": False}),
            ("POST", "/schedules/1/trigger", None),
        ]:
            response = await client.request(method, f"/workflow-actions{path}", json=body)
            assert response.status_code == 403


def test_workflow_yaml_extracts_only_default_values():
    inputs = parse_workflow_inputs(
        """
inputs:
  vllm_ascend_branch:
    description: Branch to test
    required: true
    default: main
    type: string
  test_cases:
    description: Test cases to run
    default: all
    type: string
  download_path:
    default: ''
    type: string
  aop_enabled:
    default: false
    type: boolean
  profile_options_json:
    default: '{}'
    type: string
  request_id:
    required: false
    type: string
"""
    )
    assert inputs == {
        "vllm_ascend_branch": "main",
        "test_cases": "all",
        "download_path": "",
        "aop_enabled": False,
        "profile_options_json": "{}",
    }
    assert parse_workflow_inputs("# no overrides") == {}


def test_command_contains_only_input_keys():
    request = dispatch_request(
        inputs_yaml="""
download_path:
  default: /tmp/result
download_timeout:
  default: secret-value
"""
    )

    command = build_command(request, parse_workflow_inputs(request.inputs_yaml))

    assert '"input_keys":["download_path","download_timeout"]' in command
    assert 'secret-value' not in command
    assert '/tmp/result' not in command


def test_non_scalar_default_is_rejected():
    with pytest.raises(ValueError, match="default 必须"):
        parse_workflow_inputs("run_date:\n  default: 2026-11-17\n")
    with pytest.raises(ValueError, match="default 必须"):
        parse_workflow_inputs("items:\n  default: [one, two]\n")
    with pytest.raises(ValueError, match="GitHub Secrets"):
        parse_workflow_inputs("github_token:\n  default: secret-value\n")


def test_run_action_requires_run_id_and_valid_workflow_path():
    with pytest.raises(ValidationError):
        dispatch_request(action="rerun", run_id=None)
    with pytest.raises(ValidationError):
        dispatch_request(workflow_path="schedule_nightly_test_a3.yaml")


def test_batch_permission_paths_are_normalized_and_deduplicated():
    request = WorkflowActionPermissionCreate(
        usernames=["operator", "manager", "operator"],
        workflow_paths=[
            " .github/workflows/a.yaml ",
            ".github/workflows/a.yaml",
            ".github/workflows/b.yml",
        ],
        schedule_permission=True,
    )
    assert request.usernames == ["operator", "manager"]
    assert request.schedule_permission is True
    assert request.workflow_paths == [
        ".github/workflows/a.yaml",
        ".github/workflows/b.yml",
    ]

    update = WorkflowActionPermissionUpdate(
        workflow_paths=[" .github/workflows/changed.yaml "]
    )
    assert update.workflow_paths == [".github/workflows/changed.yaml"]

    schedule_only = WorkflowActionPermissionCreate(
        usernames=["operator"],
        workflow_paths=[],
        schedule_permission=True,
    )
    assert schedule_only.workflow_paths == []


@pytest.mark.asyncio
async def test_permission_is_required_before_logging_or_github(monkeypatch):
    db = FakeDB()
    called = False

    async def github_request(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)
    user = User(id=7, username="operator")

    with pytest.raises(PermissionError, match="无该工作流"):
        await execute_workflow_action(db, user, dispatch_request())

    assert called is False
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_permission_path_comparison_is_case_sensitive(monkeypatch):
    db = FakeDB(
        permission=SimpleNamespace(workflow_path=".github/workflows/OTHER.yaml")
    )
    called = False

    async def github_request(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)

    with pytest.raises(PermissionError, match="无该工作流"):
        await execute_workflow_action(
            db,
            User(id=7, username="operator"),
            dispatch_request(),
        )

    assert called is False
    assert db.added == []


@pytest.mark.asyncio
async def test_dispatch_logs_attempt_and_submits_json(monkeypatch):
    db = FakeDB(
        permission=SimpleNamespace(
            workflow_path=".github/workflows/schedule_nightly_test_a3.yaml"
        )
    )
    calls = []

    async def github_request(token, method, endpoint, payload=None):
        calls.append((method, endpoint, payload))
        return 204, None, ""

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)
    result = await execute_workflow_action(
        db,
        User(id=7, username="operator"),
        dispatch_request(),
    )

    assert result["success"] is True
    assert result["status_code"] == 204
    assert db.commits == 1
    assert len(db.added) == 1
    assert db.added[0].username == "operator"
    assert calls == [
        (
            "POST",
            "/repos/vllm-project/vllm-ascend/actions/workflows/schedule_nightly_test_a3.yaml/dispatches",
            {"ref": "main", "inputs": {"download_path": "/tmp/result"}},
        )
    ]


@pytest.mark.asyncio
async def test_log_source_does_not_expand_username(monkeypatch):
    db = FakeDB(
        permission=SimpleNamespace(
            workflow_path=".github/workflows/schedule_nightly_test_a3.yaml"
        )
    )

    async def github_request(token, method, endpoint, payload=None):
        return 204, None, ""

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)
    username = "x" * 50
    await execute_workflow_action(
        db,
        User(id=7, username=username),
        dispatch_request(),
        require_workflow_permission=False,
        log_source="（定时）",
    )

    log = db.added[0]
    assert log.username == username
    assert log.command_text.startswith("（定时）dispatch")


@pytest.mark.asyncio
async def test_run_action_checks_actual_workflow_before_post(monkeypatch):
    db = FakeDB(
        permission=SimpleNamespace(
            workflow_path=".github/workflows/schedule_nightly_test_a3.yaml"
        )
    )
    calls = []

    async def github_request(token, method, endpoint, payload=None):
        calls.append((method, endpoint))
        return 200, {"path": ".github/workflows/other.yaml"}, ""

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)
    result = await execute_workflow_action(
        db,
        User(id=7, username="operator"),
        dispatch_request(action="force-cancel", run_id=123),
    )

    assert result["success"] is False
    assert result["status_code"] == 403
    assert calls == [
        ("GET", "/repos/vllm-project/vllm-ascend/actions/runs/123")
    ]


@pytest.mark.asyncio
async def test_rerun_failed_jobs_uses_expected_endpoint(monkeypatch):
    db = FakeDB(
        permission=SimpleNamespace(
            workflow_path=".github/workflows/schedule_nightly_test_a3.yaml"
        )
    )
    calls = []

    async def github_request(token, method, endpoint, payload=None):
        calls.append((method, endpoint))
        if method == "GET":
            return 200, {"path": ".github/workflows/schedule_nightly_test_a3.yaml"}, ""
        return 201, None, ""

    monkeypatch.setattr("workflow_actions.service._github_request", github_request)
    result = await execute_workflow_action(
        db,
        User(id=7, username="operator"),
        dispatch_request(action="rerun-failed", run_id=123),
    )

    assert result["success"] is True
    assert calls[-1] == (
        "POST",
        "/repos/vllm-project/vllm-ascend/actions/runs/123/rerun-failed-jobs",
    )
