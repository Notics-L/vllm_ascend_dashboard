from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from infrastructure.persistence.models import User
from scheduler.service import DataSyncScheduler
from workflow_actions.schedule_schemas import WorkflowScheduleCreate, cron_preview, cron_trigger
from workflow_actions.schedules import (
    ScheduleRunningError,
    create_schedule,
    run_scheduled_task,
    set_schedule_enabled,
    trigger_schedule,
    update_schedule,
)
from workflow_actions.schemas import WorkflowActionRequest
from workflow_actions.service import _github_request, _result, execute_workflow_action


class FakeResult:
    def __init__(self, value, rowcount=1):
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, schedule=None, permission=object()):
        self.schedule = schedule
        self.permission = permission
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement):
        return FakeResult(self.permission)

    async def get(self, model, schedule_id):
        return self.schedule

    def add(self, item):
        self.added.append(item)
        self.schedule = item

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.schedule.id = 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, schedule):
        schedule.id = 1

    async def delete(self, permission):
        self.permission = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def schedule_request(**overrides):
    values = {
        "name": "A3 nightly",
        "workflow_path": ".github/workflows/schedule_nightly_test_a3.yaml",
        "ref": "main",
        "inputs_yaml": "aop_enabled:\n  default: false\n",
        "cron_expression": "0 2 * * *",
        "enabled": True,
    }
    values.update(overrides)
    return WorkflowScheduleCreate(**values)


@pytest.fixture(autouse=True)
def no_reload(monkeypatch):
    async def emit(db):
        return None

    monkeypatch.setattr("workflow_actions.schedules.emit_scheduler_reload", emit)


def test_cron_validation():
    with pytest.raises(ValidationError):
        schedule_request(cron_expression="not-a-cron")


@pytest.mark.parametrize(
    ("weekday", "normalized"),
    [
        ("0", "mon"),
        ("1", "tue"),
        ("0-4", "mon-fri"),
        ("5-6", "sat,sun"),
        ("*/2", "mon,wed,fri,sun"),
    ],
)
def test_cron_preview_matches_scheduler_weekdays(weekday, normalized):
    expression = f"0 9 * * {weekday}"
    preview = cron_preview(expression)
    assert preview == f"0 9 * * {normalized}"
    start = datetime(2026, 11, 16, tzinfo=UTC)
    assert cron_trigger(expression).get_next_fire_time(None, start) == cron_trigger(
        preview
    ).get_next_fire_time(None, start)


@pytest.mark.asyncio
async def test_schedule_permission_is_required_for_creation():
    db = FakeDB(permission=None)
    with pytest.raises(PermissionError, match="无定时任务权限"):
        await create_schedule(db, User(id=1, username="user"), schedule_request())
    assert db.added == []


@pytest.mark.asyncio
async def test_invalid_inputs_yaml_is_rejected_before_schedule_creation():
    db = FakeDB()
    with pytest.raises(ValueError):
        await create_schedule(
            db,
            User(id=1, username="user"),
            schedule_request(inputs_yaml="run_date:\n  default: 2026-11-17\n"),
        )
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_outbox_failure_rolls_back_schedule_creation(monkeypatch):
    db = FakeDB()

    async def fail_reload(db):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr("workflow_actions.schedules.emit_scheduler_reload", fail_reload)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await create_schedule(db, User(id=1, username="user"), schedule_request())

    assert db.rollbacks == 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_create_update_and_toggle_schedule():
    db = FakeDB()
    user = User(id=1, username="user")
    schedule = await create_schedule(db, user, schedule_request())
    assert schedule.setter_username == "user"
    assert schedule.timezone == "Asia/Shanghai"

    other_user = User(id=2, username="manager")
    updated = await update_schedule(
        db,
        other_user,
        schedule.id,
        schedule_request(cron_expression="*/10 * * * *"),
    )
    assert updated.cron_expression == "*/10 * * * *"
    assert updated.setter_username == "manager"

    disabled = await set_schedule_enabled(db, other_user, schedule.id, False)
    assert disabled.enabled is False


@pytest.mark.asyncio
async def test_immediate_trigger_uses_schedule_permission_not_workflow_permission(monkeypatch):
    schedule = SimpleNamespace(
        id=1,
        workflow_path=".github/workflows/a.yaml",
        ref="main",
        inputs_yaml="",
        enabled=True,
        last_output=None,
        last_run_at=None,
    )
    db = FakeDB(schedule=schedule)
    seen = {}

    async def execute(db, user, request, **kwargs):
        seen.update(kwargs)
        return {"success": True, "output": "submitted", "run_url": None}

    monkeypatch.setattr("workflow_actions.schedules.execute_workflow_action", execute)
    result = await trigger_schedule(db, User(id=2, username="manager"), 1)

    assert result["success"] is True
    assert seen["require_workflow_permission"] is False
    assert seen["log_source"] == "（立即）"
    assert schedule.last_output == "submitted"
    assert schedule.last_submission_status == "submitted"
    assert schedule.last_run_at is not None


@pytest.mark.asyncio
async def test_running_schedule_rejects_duplicate_immediate_trigger():
    schedule = SimpleNamespace(
        id=1,
        workflow_path=".github/workflows/a.yaml",
        ref="main",
        inputs_yaml="",
        enabled=True,
    )
    db = FakeDB(schedule=schedule)
    async def no_acquire(statement):
        return FakeResult(object(), rowcount=0)

    db.execute = no_acquire
    with pytest.raises(ScheduleRunningError):
        await trigger_schedule(db, User(id=2, username="manager"), 1)


@pytest.mark.asyncio
async def test_cron_execution_does_not_recheck_setter_permission(monkeypatch):
    schedule = SimpleNamespace(
        id=1,
        setter_user_id=99,
        setter_username="revoked-user",
        workflow_path=".github/workflows/a.yaml",
        ref="main",
        inputs_yaml="",
        enabled=True,
        last_output=None,
        last_run_at=None,
    )
    db = FakeDB(schedule=schedule)
    seen = {}

    async def execute(db, user, request, **kwargs):
        seen["username"] = user.username
        seen.update(kwargs)
        return {"success": False, "output": "token missing", "run_url": None}

    monkeypatch.setattr("workflow_actions.schedules.SessionLocal", lambda: db)
    monkeypatch.setattr("workflow_actions.schedules.execute_workflow_action", execute)

    await run_scheduled_task(1)

    assert seen["username"] == "revoked-user"
    assert seen["require_workflow_permission"] is False
    assert seen["log_source"] == "（定时）"
    assert seen["retry_delays"] == (2, 5, 10)
    assert schedule.last_output == "token missing"
    assert schedule.last_submission_status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "expected_status", "expected_calls"),
    [
        ([httpx.ConnectError("connection failed"), 204], 204, 2),
        ([429, 204], 204, 2),
        ([httpx.ConnectTimeout("connection timed out")] * 4, None, 4),
        ([httpx.ReadTimeout("response timed out"), 204], None, 1),
        ([503, 204], 503, 1),
        ([401, 204], 401, 1),
    ],
)
async def test_dispatch_retries_only_when_safe(
    monkeypatch, responses, expected_status, expected_calls
):
    calls = []

    def respond(request):
        calls.append(request)
        result = responses[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, httpx.Response) else httpx.Response(result)

    client_class = httpx.AsyncClient

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(respond), **kwargs)

    async def sleep(delay):
        return None

    monkeypatch.setattr("workflow_actions.service.httpx.AsyncClient", client)
    monkeypatch.setattr("workflow_actions.service.asyncio.sleep", sleep)

    status, _, _ = await _github_request(
        "token", "POST", "/dispatches", {}, retry_delays=(2, 5, 10)
    )

    assert status == expected_status
    assert len(calls) == expected_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_calls", "expected_delays"),
    [
        (httpx.Response(429), 2, [60]),
        (httpx.Response(403, headers={"Retry-After": "90"}), 2, [90]),
        (httpx.Response(403, json={"message": "secondary rate limit exceeded"}), 2, [60]),
        (httpx.Response(429, headers={"Retry-After": "601"}), 1, []),
    ],
)
async def test_dispatch_honors_rate_limit_wait(
    monkeypatch, response, expected_calls, expected_delays
):
    calls = []
    delays = []

    def respond(request):
        calls.append(request)
        return response if len(calls) == 1 else httpx.Response(204)

    client_class = httpx.AsyncClient

    def client(**kwargs):
        return client_class(transport=httpx.MockTransport(respond), **kwargs)

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("workflow_actions.service.httpx.AsyncClient", client)
    monkeypatch.setattr("workflow_actions.service.asyncio.sleep", sleep)
    await _github_request("token", "POST", "/dispatches", {}, retry_delays=(2, 5, 10))

    assert len(calls) == expected_calls
    assert delays == expected_delays


def test_empty_error_response_is_not_reported_as_submitted():
    assert "请求失败" in _result(503, None)["output"]
    assert "请求已提交" in _result(204, None)["output"]


@pytest.mark.asyncio
async def test_uncertain_dispatch_error_is_reported(monkeypatch):
    def respond(request):
        raise httpx.ReadTimeout("")

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "workflow_actions.service.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    status, body, error = await _github_request(
        "token", "POST", "/dispatches", {}, retry_delays=(2, 5, 10)
    )
    output = _result(status, body, error)["output"]
    assert "ReadTimeout" in output
    assert "提交状态未确认" in output


@pytest.mark.asyncio
async def test_scheduled_server_failure_reports_uncertain_dispatch(monkeypatch):
    async def token(db):
        return "token"

    async def request(*args, **kwargs):
        return 503, None, ""

    monkeypatch.setattr("workflow_actions.service.get_github_token", token)
    monkeypatch.setattr("workflow_actions.service._github_request", request)
    result = await execute_workflow_action(
        FakeDB(),
        User(id=1, username="user"),
        WorkflowActionRequest(action="dispatch", workflow_path=".github/workflows/a.yaml"),
        require_workflow_permission=False,
        retry_delays=(2, 5, 10),
    )
    assert "请求失败" in result["output"]
    assert "提交状态未确认" in result["output"]
    assert result["submission_status"] == "unknown"


@pytest.mark.asyncio
async def test_connection_retry_respects_total_wait_limit(monkeypatch):
    calls = []
    delays = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "599"})
        raise httpx.ConnectError("")

    async def sleep(delay):
        delays.append(delay)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "workflow_actions.service.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr("workflow_actions.service.asyncio.sleep", sleep)
    status, _, error = await _github_request(
        "token", "POST", "/dispatches", {}, retry_delays=(2, 5, 10)
    )
    assert status is None
    assert error == "ConnectError"
    assert len(calls) == 2
    assert delays == [599]


class FakeScheduleResult:
    def __init__(self, schedules):
        self.schedules = schedules

    def scalars(self):
        return self

    def all(self):
        return self.schedules


class FakeScheduler:
    def __init__(self):
        self.added = []
        self.jobs = [
            SimpleNamespace(id="workflow_action_schedule:old"),
            SimpleNamespace(id="unrelated"),
        ]

    def add_job(self, func, **kwargs):
        self.added.append(kwargs)

    def get_jobs(self):
        return list(self.jobs)

    def remove_job(self, job_id):
        self.jobs = [job for job in self.jobs if job.id != job_id]


@pytest.mark.asyncio
async def test_scheduler_reloads_cron_tasks_and_removes_deleted_jobs(monkeypatch):
    tasks = [
        SimpleNamespace(
            id=7,
            name="A3 nightly",
            cron_expression="0 2 * * *",
            timezone="Asia/Shanghai",
        )
    ]
    db = FakeDB()
    db.schedule_result = tasks

    async def execute(statement):
        return FakeScheduleResult(tasks)

    db.execute = execute
    monkeypatch.setattr("scheduler.service.SessionLocal", lambda: db)
    instance = object.__new__(DataSyncScheduler)
    instance.scheduler = FakeScheduler()

    await instance._reload_workflow_action_schedules()

    assert instance.scheduler.added[0]["id"] == "workflow_action_schedule:7"
    assert instance.scheduler.added[0]["kwargs"] == {"schedule_id": 7}
    assert [job.id for job in instance.scheduler.jobs] == ["unrelated"]
