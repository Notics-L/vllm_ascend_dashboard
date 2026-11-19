"""Collection keeps scheduling parents and actual test-job leaves distinct."""
from copy import deepcopy
from datetime import datetime

import pytest
from daily_pipeline.collection_source import (
    DailyCollectionSource,
    call_node_type,
    main_job_nodes,
    matches_case,
)


def job(identity, name, result="success", steps=()):
    return {"id": identity, "name": name, "status": "completed", "conclusion": result,
            "steps": [{"name": step} for step in steps]}


def test_selected_execution_failure_is_retained_without_pytest_report():
    nodes = main_job_nodes([
        job(11, "ensure-csrc-cache / Prepare csrc cache checks"),
        job(12, "ensure-csrc-cache / Build csrc cache", "skipped"),
        job(13, "run-all-tests (abcdef123) / cpu-0 card-(part 1-1)", "failure"),
        job(14, "run-all-tests (abcdef123) / upload-coverage-to-obs", "skipped"),
        job(15, "run-all-tests (v0.33.0) / a3-8 card-(part 2-3)"),
    ])
    assert len(nodes) == 5
    parents = [node for node in nodes if node["parent_ref"] is None]
    assert [node["name"] for node in parents] == [
        "ensure-csrc-cache", "run-all-tests (abcdef123)", "run-all-tests (v0.33.0)",
    ]
    assert all(node["source_id"] is None for node in parents)
    assert [node["node_type"] for node in parents] == ["workflow_call", "matrix", "matrix"]
    assert parents[0]["result"] == "success"
    tests = [node for node in nodes if node["node_type"] == "test_job"]
    assert tests[0]["source_id"] == 13
    assert tests[0]["parent_ref"] == "job-group:run-all-tests (abcdef123)"
    assert (tests[0]["case_type"], tests[0]["result"], tests[0]["vllm_version"]) == ("UT", "failure", "main")
    assert (tests[1]["case_type"], tests[1]["hardware"], tests[1]["vllm_version"]) == ("E2E", "A3", "release")
    assert not any("coverage" in node["name"] for node in nodes)


def test_nightly_matrix_does_not_turn_auxiliary_jobs_into_test_results():
    assert matches_case(job(1, "single-node (main, case-A, image) / case-A"), "case-A", {})
    assert not matches_case(job(2, "single-node (main, case-A, image) / cleanup"), "case-A", {})
    assert matches_case(job(3, "single-node (main, case-A, image) / ${{ inputs.name }}", "skipped"), "case-A", {})
    assert not matches_case(job(5, "single-node (main, case-A, image) / ${{ inputs.download_path }}"), "case-A", {})
    assert matches_case(job(4, '["model-A"] accuracy test'), "case-A", {"model_list": ["model-A"]})


class FakeClient:
    def __init__(self, child_status="completed", dispatch_result="success", missing_child=False):
        self.root = {"id": 100, "path": ".github/workflows/schedule_daily_test.yaml", "run_attempt": 1,
                     "status": "completed", "conclusion": "success", "created_at": "2026-11-16T08:00:00Z",
                     "updated_at": "2026-11-16T08:10:00Z"}
        self.child = {"id": 200, "path": ".github/workflows/schedule_nightly_test_a3_560t.yaml", "head_sha": "abc",
                      "status": child_status, "conclusion": "failure" if child_status == "completed" else None,
                      "created_at": "2026-11-16T08:01:00Z", "updated_at": "2026-11-16T08:20:00Z"}
        self.root_jobs = [job(1, "Dispatch Nightly-A3-560T daily subset", dispatch_result),
                          job(2, "run-all-tests (abcdef123) / cpu-0 card-(part 1-1)", "failure"),
                          job(3, "run-all-tests (abcdef123) / upload-coverage-to-obs", "skipped")]
        self.child_jobs = [job(21, "Export global env vars as job outputs"),
                           job(22, "single-node (main, case-A, image) / case-A", "failure"),
                           job(23, "single-node (main, case-A, image) / cleanup")]
        self.missing_child = missing_child

    async def run(self, identity):
        return deepcopy(self.root if identity == 100 else self.child)

    async def jobs(self, identity, attempt):
        return deepcopy(self.root_jobs if identity == 100 else self.child_jobs)

    async def job_logs(self, identity):
        assert identity == 1, "test-job logs must not be downloaded to parse pytest"
        return None if self.missing_child else "https://github.com/test/repo/actions/runs/200"

    async def find_issue(self, run_id, since):
        assert run_id == 100
        return {"number": 7, "state": "open"}

    async def workflow_runs(self, *args):
        return []


HISTORY = {"run_id": 100, "source_branch": "main", "daily_cases": [{"name": "case-A", "hardware": "A3-560T"}]}


@pytest.mark.asyncio
async def test_child_workflow_is_sibling_of_dispatch_with_only_test_leaves():
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(FakeClient(), HISTORY, [])
    nodes = {node["ref"]: node for node in snapshot["nodes"]}
    assert nodes["job:1"]["parent_ref"] is None
    assert nodes["workflow:200"]["parent_ref"] is None
    matrix_ref = "job-group:200:single-node (main, case-A, image)"
    assert nodes[matrix_ref]["parent_ref"] == "workflow:200"
    assert nodes[matrix_ref]["node_type"] == "matrix"
    assert nodes[matrix_ref]["source_id"] is None
    assert nodes["job:22"]["parent_ref"] == matrix_ref
    assert nodes["job:22"]["case_type"] == "Daily"
    assert "job:21" not in nodes and "job:23" not in nodes
    assert snapshot["complete"]
    assert snapshot["run"]["finished_at"] == datetime(2026, 11, 16, 8, 20)
    assert snapshot["run"]["issue_number"] == 7
    assert snapshot["run"]["result"] == "failure"
    assert set(snapshot) == {"run", "nodes", "complete"}


@pytest.mark.asyncio
async def test_running_child_keeps_round_pending_without_an_end_time():
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(FakeClient("in_progress"), HISTORY, [])
    assert not snapshot["complete"]
    assert snapshot["run"]["finished_at"] is None


@pytest.mark.asyncio
async def test_successful_dispatch_waits_for_exact_child_association():
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(FakeClient(missing_child=True), HISTORY, [])
    assert not snapshot["complete"]
    assert snapshot["run"]["finished_at"] is None
    assert not any(node["node_type"] == "workflow_run" for node in snapshot["nodes"])


@pytest.mark.asyncio
async def test_failed_dispatch_does_not_wait_for_a_workflow_it_never_started():
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(
        FakeClient(dispatch_result="failure", missing_child=True), HISTORY, [],
    )
    assert snapshot["complete"]
    assert snapshot["run"]["result"] == "failure"


@pytest.mark.asyncio
async def test_duplicate_selected_case_does_not_duplicate_native_job_nodes():
    history = deepcopy(HISTORY)
    history["daily_cases"] += history["daily_cases"]
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(FakeClient(), history, [])
    assert sum(node["ref"] == "job:22" for node in snapshot["nodes"]) == 1


@pytest.mark.asyncio
async def test_matrix_aggregates_omitted_cleanup_failure_even_when_test_succeeds():
    client = FakeClient()
    client.child_jobs[1]["conclusion"] = "success"
    client.child_jobs[2]["conclusion"] = "failure"
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(client, HISTORY, [])
    nodes = {node["ref"]: node for node in snapshot["nodes"]}
    matrix = nodes["job-group:200:single-node (main, case-A, image)"]
    assert matrix["result"] == "failure"
    assert nodes["job:22"]["result"] == "success"
    assert "job:23" not in nodes


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [("queued", "queued"), ("in_progress", "running")])
async def test_matrix_cannot_succeed_while_an_actual_member_is_pending(status, expected):
    client = FakeClient(child_status="in_progress")
    client.child_jobs[1]["conclusion"] = "success"
    client.child_jobs[2].update(status=status, conclusion=None)
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(client, HISTORY, [])
    nodes = {node["ref"]: node for node in snapshot["nodes"]}
    assert nodes["job-group:200:single-node (main, case-A, image)"]["result"] == expected
    assert nodes["job:22"]["result"] == "success"
    assert "job:23" not in nodes
    assert not snapshot["complete"]


@pytest.mark.asyncio
async def test_nested_invocations_keep_shared_ancestors_without_duplicate_nodes():
    client = FakeClient()
    outer = "single-node (main, shared-image)"
    inner = outer + " / execute-tests"
    client.child_jobs = [
        job(21, "Export global env vars as job outputs"),
        job(22, inner + " / case-A"),
        job(23, inner + " / cleanup", "failure"),
        job(24, inner + " / case-B"),
    ]
    history = deepcopy(HISTORY)
    history["daily_cases"].append({"name": "case-B", "hardware": "A3-560T"})
    snapshot = await DailyCollectionSource("test/repo", "unused")._collect(client, history, [])
    nodes = {node["ref"]: node for node in snapshot["nodes"]}
    assert len(nodes) == len(snapshot["nodes"])
    outer_ref, inner_ref = f"job-group:200:{outer}", f"job-group:200:{inner}"
    assert nodes[outer_ref]["parent_ref"] == "workflow:200"
    assert nodes[outer_ref]["node_type"] == "matrix"
    assert nodes[inner_ref]["parent_ref"] == outer_ref
    assert nodes[inner_ref]["node_type"] == "workflow_call"
    assert nodes[outer_ref]["result"] == nodes[inner_ref]["result"] == "failure"
    assert nodes["job:22"]["parent_ref"] == nodes["job:24"]["parent_ref"] == inner_ref
    assert nodes["job:22"]["result"] == nodes["job:24"]["result"] == "success"
    assert "job:21" not in nodes and "job:23" not in nodes


def test_truncated_expanded_matrix_name_still_has_matrix_type():
    assert call_node_type("single-node (main, ForceFail-e2e-Daily, linux-aarch64-nightly-a3-2, force_fail.yaml, tests/SIT/ca...") == "matrix"
    assert call_node_type("ensure-csrc-cache") == "workflow_call"
