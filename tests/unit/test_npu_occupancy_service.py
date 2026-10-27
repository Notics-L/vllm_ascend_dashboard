from datetime import UTC, datetime

from resource_dashboard.npu_occupancy_service import (
    NpuOccupancyService,
    enrich_records_with_runs,
    normalize_records,
    npu_cards,
    parse_timestamp,
    project_of,
)


def _env(*, env_id: str, cluster: str, status: str, created: str, expires: str | None, cards: int, project: str = "org/repo") -> dict:
    organization, repository = project.split("/", 1)
    return {
        "env_id": env_id, "cluster": cluster, "status": status, "created_at": created, "expires_at": expires,
        "extend_env_comments": {"organization": organization, "repository": repository, "job_display_name": env_id},
        "resource_summary": {"devices": [{"res_type": "npu", "npu": cards}, {"res_type": "container", "npu": 99}]},
        "node_ip": "10.0.0.1", "npu_list": "0,1",
    }


class _Client:
    def __init__(self, envs: list[dict]):
        self.envs = envs

    def fetch_window(self, start, end):
        return self.envs


def test_normalization_matches_pipeline_card_and_interval_rules():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    envs = [
        _env(env_id="active", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8),
        _env(env_id="expired", cluster="gy-005", status="expired", created="2026-09-01T10:00:00Z", expires="2026-09-01T10:04:00Z", cards=4),
        _env(env_id="rejected", cluster="gy-004", status="rejected", created="2026-09-01T10:00:00Z", expires=None, cards=32),
        _env(env_id="excluded", cluster="gy006", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=16),
    ]
    records = normalize_records(envs, snapshot)
    assert [record.env_id for record in records] == ["active", "expired"]
    assert npu_cards(envs[0]) == 8
    assert parse_timestamp("2026-09-01 10:00:00.86+00") == datetime(2026, 9, 1, 10, 0, 0, 860000, tzinfo=UTC)

    service = NpuOccupancyService(client=_Client(envs))
    result = service.trend(dimension="pool", names=[], start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC), end=datetime(2026, 9, 1, 10, 6, tzinfo=UTC))
    assert [point["occupied_cards"] for point in result["series"]] == [12, 12, 8, 8]
    assert result["metrics"]["peak"] == 12
    assert result["metrics"]["latest"] == 8
    assert result["metrics"]["capacity"] is None


def test_project_aliases_and_pool_selection_do_not_double_count():
    envs = [
        _env(env_id="one", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8, project="org/repo"),
        _env(env_id="two", cluster="gy-005", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=4, project="org/repo"),
    ]
    service = NpuOccupancyService(client=_Client(envs))
    start, end = datetime(2026, 9, 1, 10, 0, tzinfo=UTC), datetime(2026, 9, 1, 10, 2, tzinfo=UTC)
    project = service.trend(dimension="project", names=["org-repo"], start=start, end=end)
    pools = service.trend(dimension="pool", names=["SGLang资源池-贵阳-A3", "vllm资源池-贵阳-A3"], start=start, end=end)
    assert project["series"][0]["occupied_cards"] == 12
    assert pools["series"][0]["occupied_cards"] == 12
    assert project["metrics"]["capacity"] is None


def test_legacy_hyphenated_project_identifier_is_canonicalized_for_github_links():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="sglang", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8)
    env["extend_env_comments"] = {
        "repository": "sgl-project-sglang",
        "workflow_run_id": 123,
        "job_display_name": "E2E",
    }

    record = normalize_records([env], snapshot)[0]

    assert project_of(env) == "sgl-project/sglang"
    assert (record.organization, record.repository, record.project) == ("sgl-project", "sglang", "sgl-project/sglang")
    assert record.run_url == "https://github.com/sgl-project/sglang/actions/runs/123"


def test_legacy_hyphenated_repository_is_canonicalized_when_organization_is_present():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="sglang", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8)
    env["extend_env_comments"] = {
        "organization": "sgl-project",
        "repository": "sgl-project-sglang",
        "workflow_run_id": 123,
        "job_display_name": "PR Test (NPU)",
    }

    record = normalize_records([env], snapshot)[0]

    assert (record.organization, record.repository, record.project) == ("sgl-project", "sglang", "sgl-project/sglang")
    assert record.run_url == "https://github.com/sgl-project/sglang/actions/runs/123"


def test_job_repository_is_used_when_arc_payload_omits_repository():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="arc-sglang", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8)
    env["extend_env_comments"] = {
        "organization": "sgl-project",
        "job_repository": "sgl-project/sglang",
        "workflow_run_id": 456,
        "job_display_name": "PR Test",
    }

    record = normalize_records([env], snapshot)[0]

    assert project_of(env) == "sgl-project/sglang"
    assert (record.organization, record.repository, record.project) == ("sgl-project", "sglang", "sgl-project/sglang")
    assert record.run_url == "https://github.com/sgl-project/sglang/actions/runs/456"


def test_project_identity_does_not_duplicate_owner_when_repository_is_already_qualified():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="pytorch", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8)
    env["extend_env_comments"] = {
        "organization": "Ascend",
        "repository": "Ascend/pytorch",
        "workflow_run_id": 456,
        "job_display_name": "JOB_UT",
    }

    record = normalize_records([env], snapshot)[0]

    assert (record.organization, record.repository, record.project) == ("Ascend", "pytorch", "Ascend/pytorch")
    assert record.run_url == "https://github.com/Ascend/pytorch/actions/runs/456"


def test_range_analysis_uses_the_whole_interval_not_one_snapshot():
    envs = [
        _env(env_id="long", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8),
        _env(env_id="short", cluster="gy-004", status="expired", created="2026-09-01T10:00:00Z", expires="2026-09-01T10:04:00Z", cards=4),
    ]
    service = NpuOccupancyService(client=_Client(envs))
    start = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    end = datetime(2026, 9, 1, 10, 6, tzinfo=UTC)

    result = service.analysis(dimension="project", names=[], start=start, end=end)

    assert len(result["groups"]) == 1
    group = result["groups"][0]
    assert group["task_count"] == 2
    assert group["peak_cards"] == 12
    assert group["average_cards"] == 10
    assert "tasks" not in group
    assert sum(len(run["tasks"]) for run in group["runs"]) == 2


def test_pre_normalized_snapshot_is_reused_without_another_client_read():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    records = normalize_records([
        _env(env_id="cached", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8),
    ], snapshot)

    service = NpuOccupancyService(records=records, snapshot_at=snapshot)
    result = service.trend(
        dimension="pool", names=[],
        start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 9, 1, 10, 2, tzinfo=UTC),
    )

    assert result["snapshot_at"] == snapshot
    assert result["series"][0]["occupied_cards"] == 8


def test_range_analysis_stops_at_the_persisted_snapshot_time():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    records = normalize_records([
        _env(env_id="cached", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=8),
    ], snapshot)

    result = NpuOccupancyService(records=records, snapshot_at=snapshot).analysis(
        dimension="pool", names=[],
        start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 9, 1, 10, 20, tzinfo=UTC),
    )

    assert result["end"] == snapshot
    # The snapshot-end bucket itself is exclusive for an active environment.
    assert result["groups"][0]["average_cards"] == 6.67


def test_range_fully_after_snapshot_is_anchored_at_latest_known_data():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    records = normalize_records([
        _env(env_id="cached", cluster="gy-004", status="active", created="2026-09-01T09:00:00Z", expires=None, cards=8),
    ], snapshot)
    service = NpuOccupancyService(records=records, snapshot_at=snapshot)
    start = datetime(2026, 9, 1, 10, 20, tzinfo=UTC)
    end = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)

    trend = service.trend(dimension="pool", names=[], start=start, end=end)
    analysis = service.analysis(dimension="pool", names=[], start=start, end=end)

    assert (trend["start"], trend["end"]) == (datetime(2026, 9, 1, 10, 0, tzinfo=UTC), snapshot)
    assert trend["metrics"]["peak"] == 8
    assert (analysis["start"], analysis["end"]) == (datetime(2026, 9, 1, 10, 0, tzinfo=UTC), snapshot)
    assert analysis["groups"][0]["peak_cards"] == 8


def test_records_with_the_same_workflow_run_are_aggregated_once():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    envs = [
        _env(env_id="one", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=2),
        _env(env_id="two", cluster="gy-004", status="active", created="2026-09-01T10:01:00Z", expires=None, cards=3),
    ]
    for env in envs:
        env["extend_env_comments"].update({
            "workflow_run_id": 38170355411,
            "workflow_ref": "repo/.github/workflows/nightly.yml@refs/pull/18658/merge",
        })
    records = enrich_records_with_runs(normalize_records(envs, snapshot), {
        38170355411: type("Run", (), {
            "workflow_name": "Nightly-A5", "run_number": 439, "event": "pull_request", "data": None,
        })(),
    })
    result = NpuOccupancyService(records=records, snapshot_at=snapshot).analysis(
        dimension="project", names=[],
        start=datetime(2026, 9, 1, 10, 0, tzinfo=UTC), end=datetime(2026, 9, 1, 10, 5, tzinfo=UTC),
    )
    run = result["groups"][0]["runs"][0]
    assert len(result["groups"][0]["runs"]) == 1
    assert run["run_display_name"] == "Nightly-A5 #439"
    assert run["pr_number"] == 18658
    assert run["cards"] == 5
    assert run["task_count"] == 2


def test_ci_job_name_overrides_the_raw_job_display_name():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="job", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=2)
    env["extend_env_comments"].update({"job_id": 12345, "job_display_name": "raw-name"})
    record = enrich_records_with_runs(normalize_records([env], snapshot), {}, {
        12345: type("Job", (), {"job_name": "E2E / selected tests"})(),
    })[0]
    assert record.job_name == "E2E / selected tests"


def test_workflow_config_name_is_used_when_ci_run_and_job_history_are_missing():
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="workflow", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=2)
    env["extend_env_comments"].update({
        "workflow_ref": "repo/.github/workflows/pr_test.yaml@refs/pull/17900/merge",
    })
    record = enrich_records_with_runs(
        normalize_records([env], snapshot), {}, {}, {17900: "[Feature] example #39270"}, {("org", "repo", "pr_test.yaml"): "E2E"},
    )[0]
    assert record.run_display_name == "[Feature] example #39270"
    assert record.workflow_name == "E2E"


def test_workflow_name_can_come_from_another_ci_job_in_the_same_run():
    """A raw occupancy job can point at a reusable child workflow file."""
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="workflow-child", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=2)
    env["extend_env_comments"].update({
        "workflow_run_id": 37758328506,
        "workflow_ref": "vllm-project/vllm-ascend/.github/workflows/_selected_tests.yaml@refs/pull/17900/merge",
    })
    record = enrich_records_with_runs(
        normalize_records([env], snapshot), {}, {}, {17900: "[Feature] example #39270"}, {},
        {37758328506: "E2E"},
    )[0]
    assert record.workflow_file == "_selected_tests.yaml"
    assert record.workflow_name == "E2E"


def test_workflow_name_resolution_uses_run_metadata_before_all_local_fallbacks():
    """The exact GitHub run must win over config and CI-job-derived names."""
    snapshot = datetime(2026, 9, 1, 10, 10, tzinfo=UTC)
    env = _env(env_id="priority", cluster="gy-004", status="active", created="2026-09-01T10:00:00Z", expires=None, cards=2)
    env["extend_env_comments"].update({
        "workflow_run_id": 37758328506,
        "workflow_ref": "vllm-project/vllm-ascend/.github/workflows/_selected_tests.yaml@refs/pull/17900/merge",
    })
    record = enrich_records_with_runs(
        normalize_records([env], snapshot),
        {
            37758328506: type("Run", (), {
                # This column may have been normalized from a local config.
                "workflow_name": "Configured E2E",
                "data": '{"name":"E2E"}',
            })(),
        },
        {},
        {},
        {("vllm-project", "vllm-ascend", "_selected_tests.yaml"): "Mapped E2E"},
        {37758328506: "CI-job E2E"},
    )[0]
    assert record.workflow_name == "E2E"
