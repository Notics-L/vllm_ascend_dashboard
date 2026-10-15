from types import SimpleNamespace

from infrastructure.github_run_metadata import build_run_metadata, parse_workflow_ref


def test_pull_request_ref_extracts_pr_number():
    assert parse_workflow_ref(
        "vllm-project/vllm-ascend/.github/workflows/_selected_tests.yaml@refs/pull/18658/merge"
    ) == (18658, None)


def test_main_ref_is_not_misidentified_as_a_pr():
    assert parse_workflow_ref(
        "vllm-project/vllm-ascend/.github/workflows/pr_nightly_command.yml@refs/heads/main"
    ) == (None, "branch/main")


def test_ci_result_builds_readable_run_name_and_url():
    result = SimpleNamespace(
        workflow_name="Nightly-A5",
        run_number=439,
        event="workflow_dispatch",
        data='{"display_title":"Nightly-A5 (manual)","run_number":439}',
    )
    metadata = build_run_metadata(
        organization="vllm-project", repository="vllm-ascend",
        workflow_run_id=38170355411, workflow_ref="refs/heads/main",
        job_display_name="run-selected-tests", ci_result=result,
    )
    assert metadata["run_display_name"] == "Nightly-A5 (manual) #439"
    assert metadata["workflow_name"] == "Nightly-A5"
    assert metadata["run_display_title"] == "Nightly-A5 (manual)"
    assert metadata["pr_number"] is None
    assert metadata["event"] == "workflow_dispatch"
    assert metadata["run_url"] == "https://github.com/vllm-project/vllm-ascend/actions/runs/38170355411"


def test_missing_ci_run_falls_back_without_error():
    metadata = build_run_metadata(
        organization=None, repository=None, workflow_run_id=38524547407,
        workflow_ref=None, job_display_name="run-selected-tests",
    )
    assert metadata["run_display_name"] == "38524547407"
    assert metadata["run_url"] is None


def test_ci_run_raw_repository_backfills_a_missing_occupancy_repository():
    result = SimpleNamespace(
        workflow_name="Nightly-A5", run_number=439, event="schedule",
        data='{"repository":{"full_name":"vllm-project/vllm-ascend"}}',
    )
    metadata = build_run_metadata(
        organization=None, repository=None, workflow_run_id=38170355411,
        workflow_ref=None, job_display_name="nightly", ci_result=result,
    )
    assert metadata["run_url"] == "https://github.com/vllm-project/vllm-ascend/actions/runs/38170355411"


def test_job_workflow_name_is_only_a_name_fallback_not_a_trigger():
    metadata = build_run_metadata(
        organization=None, repository=None, workflow_run_id=38036486788,
        workflow_ref="repo/.github/workflows/schedule_nightly_test_a3_560t.yaml@refs/heads/main",
        job_display_name="single-node", fallback_workflow_name="Nightly-A3-560T",
    )
    assert metadata["run_display_name"] == "Nightly-A3-560T"
    assert metadata["event"] is None
    assert metadata["trigger_scope"] == "branch/main"


def test_pr_title_is_rendered_as_workflow_display_title():
    metadata = build_run_metadata(
        organization="vllm-project", repository="vllm-ascend", workflow_run_id=37758328506,
        workflow_ref="repo/.github/workflows/pr_test.yaml@refs/pull/17900/merge",
        job_display_name="run-selected-tests", fallback_workflow_name="E2E",
        fallback_display_title="[Feature][KV Offload] Support GLM-5.2 V2 sparse offload with PP and DSpark #39270",
    )
    assert metadata["run_display_name"] == (
        "[Feature][KV Offload] Support GLM-5.2 V2 sparse offload with PP and DSpark #39270"
    )
    assert metadata["workflow_name"] == "E2E"
    assert metadata["run_display_title"] == "[Feature][KV Offload] Support GLM-5.2 V2 sparse offload with PP and DSpark #39270"
    assert metadata["workflow_file"] == "pr_test.yaml"
