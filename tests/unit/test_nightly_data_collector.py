import asyncio
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

from collector.nightly_data import NightlyDataCollector
from infrastructure.persistence.models import DailyFailureRecord
from tooling.parsers.nightly_config_parser import NightlyConfigParser


def _snapshot(*, report_date: str, branch: str = "main", workflow: str = "Nightly-A3", job_name: str, test_model: str):
    return SimpleNamespace(
        report_date=report_date,
        source_branch=branch,
        workflow_name=workflow,
        job_name=job_name,
        test_model=test_model,
    )


class _FakeResult:
    def __init__(self, rows, rowcount=0):
        self.rows = rows
        self.rowcount = rowcount

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.added = []
        self.commit_count = 0

    async def execute(self, _statement):
        response = next(self.responses)
        if isinstance(response, tuple) and len(response) == 2 and response[0] == "rowcount":
            return _FakeResult([], rowcount=response[1])
        return _FakeResult(response)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commit_count += 1


class _LockResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _LockConnection:
    def __init__(self, acquired: int):
        self.acquired = acquired
        self.operations = []

    async def execute(self, statement, _params):
        sql = str(statement)
        self.operations.append(sql)
        return _LockResult(self.acquired if "GET_LOCK" in sql else 1)


class _LockConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _LockEngine:
    def __init__(self, acquired: int):
        self.connection = _LockConnection(acquired)

    def connect(self):
        return _LockConnectionContext(self.connection)


class _BoundFakeSession:
    def __init__(self, acquired: int):
        self.bind = _LockEngine(acquired)
        self.rollback_count = 0

    async def rollback(self):
        self.rollback_count += 1


def test_sync_skips_when_another_worker_holds_the_materialization_lock():
    collector = NightlyDataCollector(_BoundFakeSession(acquired=0))

    async def should_not_run():
        raise AssertionError("locked synchronization must not write data")

    collector.snapshot_configs = should_not_run
    collector.populate_daily_failure_records = should_not_run

    assert asyncio.run(collector.sync()) == {
        "nightly_test_cases": 0,
        "daily_failure_records": 0,
    }
    assert len(collector.db.bind.connection.operations) == 1
    assert "GET_LOCK" in collector.db.bind.connection.operations[0]


def test_sync_releases_materialization_lock_after_writes():
    collector = NightlyDataCollector(_BoundFakeSession(acquired=1))

    async def snapshot_configs():
        return 4

    async def populate_daily_failure_records():
        return 2

    collector.snapshot_configs = snapshot_configs
    collector.populate_daily_failure_records = populate_daily_failure_records

    assert asyncio.run(collector.sync()) == {
        "nightly_test_cases": 4,
        "daily_failure_records": 2,
    }
    operations = collector.db.bind.connection.operations
    assert "GET_LOCK" in operations[0]
    assert "RELEASE_LOCK" in operations[-1]


def test_sync_retries_transient_mysql_lock_timeout():
    collector = NightlyDataCollector(_BoundFakeSession(acquired=1))
    attempts = 0

    async def snapshot_configs():
        return 4

    async def populate_daily_failure_records():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError(
                "UPDATE daily_failure_records",
                {},
                Exception(1205, "Lock wait timeout exceeded"),
            )
        return 2

    collector.snapshot_configs = snapshot_configs
    collector.populate_daily_failure_records = populate_daily_failure_records

    assert asyncio.run(collector.sync()) == {
        "nightly_test_cases": 4,
        "daily_failure_records": 2,
    }
    assert attempts == 2
    assert collector.db.rollback_count == 1


def test_match_snapshot_prefers_exact_report_date():
    old = _snapshot(
        report_date="2026-08-13",
        job_name="old-model",
        test_model="old-model.yaml",
    )
    exact = _snapshot(
        report_date="2026-08-14",
        job_name="new-model",
        test_model="new-model.yaml",
    )
    snapshot_map = {
        ("2026-08-13", "main", "Nightly-A3", old.job_name): old,
        ("2026-08-14", "main", "Nightly-A3", exact.job_name): exact,
    }

    result = NightlyDataCollector._match_snapshot(
        snapshot_map,
        {("main", "Nightly-A3"): [exact, old]},
        report_date="2026-08-14",
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="single-node (main, new-model.yaml) / new-model",
    )

    assert result is exact


def test_match_snapshot_falls_back_for_historical_job_without_exact_snapshot():
    current = _snapshot(
        report_date="2026-08-14",
        job_name="MiniMax-M3-W8A8-A3",
        test_model="MiniMax-M3-W8A8-A3.yaml",
    )

    result = NightlyDataCollector._match_snapshot(
        {
            ("2026-08-14", "main", "Nightly-A3", current.job_name): current,
        },
        {("main", "Nightly-A3"): [current]},
        report_date="2026-08-12",
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="single-node (main, MiniMax-M3-W8A8-A3.yaml) / MiniMax-M3-W8A8-A3",
    )

    assert result is current


def test_report_date_uses_job_completion_date_when_nightly_crosses_midnight():
    job = SimpleNamespace(
        started_at=datetime(2026, 8, 17, 15, 45, tzinfo=UTC),  # 23:45 Beijing
        completed_at=datetime(2026, 8, 17, 16, 10, tzinfo=UTC),  # 00:10 next day
    )

    assert NightlyDataCollector._report_date_for_job(job) == "2026-08-18"


def test_report_date_uses_workflow_completion_for_the_whole_nightly_batch():
    job = SimpleNamespace(
        started_at=datetime(2026, 8, 17, 15, 30, tzinfo=UTC),  # 23:30 Beijing
        completed_at=datetime(2026, 8, 17, 15, 50, tzinfo=UTC),  # 23:50 Beijing
    )
    workflow_completed_at = datetime(2026, 8, 17, 16, 10, tzinfo=UTC)  # next day

    assert (
        NightlyDataCollector._report_date_for_job(job, workflow_completed_at)
        == "2026-08-18"
    )


def test_report_date_uses_workflow_completion_when_job_completion_is_missing():
    job = SimpleNamespace(
        started_at=datetime(2026, 8, 17, 15, 45, tzinfo=UTC),
        completed_at=None,
    )
    workflow_completed_at = datetime(2026, 8, 17, 16, 5, tzinfo=UTC)

    assert (
        NightlyDataCollector._report_date_for_job(job, workflow_completed_at)
        == "2026-08-18"
    )


def test_report_date_treats_naive_database_timestamps_as_utc():
    job = SimpleNamespace(
        started_at=datetime(2026, 8, 17, 15, 45),
        completed_at=datetime(2026, 8, 17, 16, 10),
    )

    assert NightlyDataCollector._report_date_for_job(job) == "2026-08-18"


def test_materialization_excludes_job_without_matching_persisted_workflow():
    now = datetime.now(UTC).replace(microsecond=0)
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        workflow_name="Nightly-A3",
        job_name="single-node (main, model.yaml) / model",
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={"run_attempt": 1, "head_branch": "main"},
    )
    for workflow_rows in (
        [],
        [(456, "Nightly-A2", now, "main", {"run_attempt": 1}, "schedule")],
    ):
        db = _FakeSession([[job], workflow_rows, [], []])
        count = asyncio.run(NightlyDataCollector(db).populate_daily_failure_records())
        assert count == 0
        assert db.added == []


def test_source_branch_prefers_workflow_branch_over_display_name():
    job = SimpleNamespace(
        job_name="single-node (releases-v0.26.0rc, glm-4.7-w8a8) / glm-4.7-w8a8",
        data='{"head_branch": "main"}',
    )

    assert (
        NightlyDataCollector._source_branch_for_job(job, "releases/v0.26.0rc")
        == "releases/v0.26.0rc"
    )


def test_nightly_yaml_parses_from_explicit_ref_content_without_checkout():
    cases = NightlyConfigParser.parse_content(
        """
a3:
  single_node:
    test_config:
      - name: single-node-main
        config_file_path: Qwen.yaml
        os: linux
""",
        report_date="2026-08-19",
        source_branch="releases/v1",
    )

    assert [(case.workflow, case.name, case.model_path) for case in cases] == [
        ("Nightly-A3", "single-node-main", "Qwen.yaml")
    ]

def test_source_branch_uses_job_payload_when_workflow_is_missing():
    job = SimpleNamespace(
        job_name="single-node (releases-v0.26.0rc, glm-4.7-w8a8) / glm-4.7-w8a8",
        data={"head_branch": "releases/v0.26.0rc"},
    )

    assert (
        NightlyDataCollector._source_branch_for_job(job)
        == "releases/v0.26.0rc"
    )


def test_source_branch_parses_display_name_only_for_legacy_data():
    job = SimpleNamespace(
        job_name="single-node (main, glm-4.7-w8a8) / glm-4.7-w8a8",
        data="not-json",
    )

    assert NightlyDataCollector._source_branch_for_job(job) == "main"


def test_populate_daily_failure_records_uses_persisted_workflow_not_job_raw_name():
    now = datetime.now(UTC).replace(microsecond=0)
    report_date = (now.astimezone(timezone(timedelta(hours=8)))).date().isoformat()
    job_name = "single-node (main, MiniMax-M3-W8A8-A3.yaml) / MiniMax-M3-W8A8-A3"
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        workflow_name="Nightly-A3",
        job_name=job_name,
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={
            "run_attempt": 1,
            "head_branch": "main",
            "workflow_name": "Nightly-A3 (manual)",
        },
    )
    snapshot = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="MiniMax-M3-W8A8-A3",
        test_model="MiniMax-M3-W8A8-A3.yaml",
        display_name="MiniMax-M3-W8A8-A3",
        model_fo="MiniMax-M3",
        owner=None,
        deployment_type="single-node",
    )
    db = _FakeSession(
        [
            [job],
            [(456, "Nightly-A3", now, "main", {"run_attempt": 1}, "workflow_dispatch")],
            [snapshot],
            [],
            [],
        ]
    )

    collector = NightlyDataCollector(db)
    count = asyncio.run(collector.populate_daily_failure_records())

    assert count == 1
    assert db.commit_count == 1
    assert len(db.added) == 1
    assert isinstance(db.added[0], DailyFailureRecord)
    assert db.added[0].job_id == 123
    assert collector.last_materialized_job_ids == {123}


def test_populate_daily_failure_records_marks_existing_jobs_for_auto_analysis():
    now = datetime.now(UTC).replace(microsecond=0)
    report_date = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        workflow_name="Nightly-A3",
        job_name="single-node (main, MiniMax-M3-W8A8-A3.yaml) / MiniMax-M3-W8A8-A3",
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={"run_attempt": 1, "head_branch": "main"},
    )
    snapshot = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="MiniMax-M3-W8A8-A3",
        test_model="MiniMax-M3-W8A8-A3.yaml",
        display_name="MiniMax-M3-W8A8-A3",
        model_fo="MiniMax-M3",
        owner=None,
        deployment_type="single-node",
    )
    existing = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name=job.job_name,
        job_id=123,
        run_id=456,
        problem_category=None,
    )
    db = _FakeSession([
        [job],
        [(456, "Nightly-A3", now, "main", {"run_attempt": 1}, "workflow_dispatch")],
        [snapshot],
        [existing],
        [],
    ])

    collector = NightlyDataCollector(db)
    count = asyncio.run(collector.populate_daily_failure_records())

    assert count == 0
    assert collector.last_materialized_job_ids == {123}


def test_populate_daily_failure_records_replaces_cancelled_with_failure():
    now = datetime.now(UTC).replace(microsecond=0)
    report_date = now.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    job_name = "single-node (main, MiniMax-M3-W8A8-A3.yaml) / MiniMax-M3-W8A8-A3"
    job = SimpleNamespace(
        job_id=124,
        run_id=457,
        workflow_name="Nightly-A3",
        job_name=job_name,
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={"run_attempt": 1, "head_branch": "main"},
    )
    snapshot = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="MiniMax-M3-W8A8-A3",
        test_model="MiniMax-M3-W8A8-A3.yaml",
        display_name="MiniMax-M3-W8A8-A3",
        model_fo="MiniMax-M3",
        owner=None,
        deployment_type="single-node",
    )
    existing = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name=job_name,
        job_id=123,
        run_id=456,
        conclusion="cancelled",
        started_at=None,
        completed_at=None,
        duration_seconds=None,
        hardware="A3",
        problem_category=None,
        github_job_url=None,
    )
    db = _FakeSession([
        [job],
        [(457, "Nightly-A3", now, "main", {"run_attempt": 1}, "workflow_dispatch")],
        [snapshot],
        [existing],
        [],
    ])

    collector = NightlyDataCollector(db)
    count = asyncio.run(collector.populate_daily_failure_records())

    assert count == 0
    assert existing.job_id == 124
    assert existing.run_id == 457
    assert existing.conclusion == "failure"
    assert collector.last_materialized_job_ids == {124}


def test_populate_daily_failure_records_deduplicates_pending_records_by_key():
    now = datetime.now(UTC).replace(microsecond=0)
    report_date = (now.astimezone(timezone(timedelta(hours=8)))).date().isoformat()
    job_name = "single-node (main, MiniMax-M3-W8A8-A3.yaml) / MiniMax-M3-W8A8-A3"
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        workflow_name="Nightly-A3",
        job_name=job_name,
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={"run_attempt": 1, "head_branch": "main"},
    )
    duplicate_job = SimpleNamespace(**{**vars(job), "job_id": 124})
    snapshot = SimpleNamespace(
        report_date=report_date,
        source_branch="main",
        workflow_name="Nightly-A3",
        job_name="MiniMax-M3-W8A8-A3",
        test_model="MiniMax-M3-W8A8-A3.yaml",
        display_name="MiniMax-M3-W8A8-A3",
        model_fo="MiniMax-M3",
        owner=None,
        deployment_type="single-node",
    )
    db = _FakeSession(
        [
            [job, duplicate_job],
            [(456, "Nightly-A3", now, "main", {"run_attempt": 1}, "workflow_dispatch")],
            [snapshot],
            [],
            [],
        ]
    )

    count = asyncio.run(NightlyDataCollector(db).populate_daily_failure_records())

    assert count == 1
    assert db.commit_count == 1
    assert len(db.added) == 1
    assert db.added[0].job_id == 123


def test_populate_daily_failure_records_purges_legacy_pr_nightly_rows():
    now = datetime.now(UTC).replace(microsecond=0)
    job = SimpleNamespace(
        job_id=94951079187,
        run_id=31858609426,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen3-30b-acc) / qwen3-30b-acc",
        conclusion="failure",
        started_at=now,
        completed_at=now,
        duration_seconds=60,
        hardware="A3",
        data={"run_attempt": 1, "head_branch": "main"},
        steps_data=[
            {
                "name": "uninstall vlm vllm-ascend and remove code (if pr test)",
                "conclusion": "success",
            }
        ],
    )
    db = _FakeSession(
        [
            [job],
            [(31858609426, "Nightly-A3", now, "main", {"run_attempt": 1}, "workflow_dispatch")],
            ("rowcount", 7),
            [],
            [],
            [],
            [],
        ]
    )

    count = asyncio.run(NightlyDataCollector(db).populate_daily_failure_records())

    assert count == 0
    assert db.commit_count == 1
    assert db.added == []
