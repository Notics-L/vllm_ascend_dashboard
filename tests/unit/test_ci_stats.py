import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import mysql

from api.v1.ci import (
    _build_log_root_cause_context,
    _apply_benchmark_config,
    _benchmark_config_reference,
    _extract_log_error_summary,
    _extract_merged_pr_number,
    _extract_revert_target,
    _fetch_pr_details_bounded,
    _merged_within_commit_boundary,
    _parse_benchmark_metrics,
    _performance_trends_cache,
    _save_performance_trends_cache,
    _with_performance_trend_job_context,
    get_ci_stats,
    list_runs,
)
from contracts.schemas import CIStats


@pytest.mark.asyncio
async def test_pr_detail_fetch_uses_bounded_concurrency_and_preserves_order():
    class FakeClient:
        def __init__(self):
            self.active = 0
            self.peak = 0

        async def get_pr_detail(self, owner, repo, number):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {"number": number, "merged_at": "2026-09-17T00:00:00Z"}

    client = FakeClient()
    results = await _fetch_pr_details_bounded(
        client, "owner", "repo", list(range(1, 10)), max_concurrency=4
    )

    assert client.peak == 4
    assert [number for number, _, _ in results] == list(range(1, 10))
    assert all(error is None for _, _, error in results)


def test_pr_merge_time_is_constrained_by_version_commit_boundary():
    start = "2026-09-11T17:34:55+08:00"
    end = "2026-09-14T21:26:49+08:00"

    assert not _merged_within_commit_boundary("2026-09-08T09:59:00Z", start, end)
    assert _merged_within_commit_boundary("2026-09-12T02:00:00Z", start, end)
    assert not _merged_within_commit_boundary("2026-09-15T02:00:00Z", start, end)


def test_pr_merge_time_uses_available_single_sided_boundary():
    assert not _merged_within_commit_boundary(
        "2026-09-08T09:59:00Z", "2026-09-11T17:34:55+08:00", None
    )
    assert _merged_within_commit_boundary(
        "2026-09-12T09:59:00Z", "2026-09-11T17:34:55+08:00", None
    )


def test_revert_title_uses_final_number_as_current_pr():
    title = '[Revert] Revert "Feature" (#15908) (#16409)'
    assert _extract_merged_pr_number(title) == 16409


def test_revert_target_supports_generated_revert_pr_message():
    message = (
        '[Revert] Revert "Feature" (#15908) (#16409)\n\n'
        'Revert of PR #15908 (merged onto `main`).\n'
        'Original PR: #15908\n'
        'Merge commit: `daa644121e1142821777e3ba60a49ab6dd354920`'
    )
    target_pr, target_sha = _extract_revert_target(
        message,
        16409,
        {"daa644121e1142821777e3ba60a49ab6dd354920": 15908},
    )
    assert target_pr == 15908
    assert target_sha == "daa644121e1142821777e3ba60a49ab6dd354920"


def test_log_context_preserves_aisbench_verdict_over_generic_noise():
    noise = "\n".join(f"[ERROR] generic orchestration error {index}" for index in range(40))
    performance = (
        "[2026-09-10 22:26:09] [ERROR] The following aisbench case failed: "
        "{'case_type': 'performance', 'case_name': 'perf'}, reason is Performance "
        "verification failed. The current Output Token Throughput is 186.3474 token/s, "
        "which is not greater than or equal to 0.97 * baseline 347.4475."
    )
    accuracy = (
        "Accuracy verification failed. The accuracy of /datasets/aime2025 is 0.0, "
        "which is not within 10 relative to baseline 93.33."
    )

    excerpt = _build_log_root_cause_context(
        "Waiting for all pods to become Running and Ready\nStream logs\n"
        + noise + "\n" + performance + "\n" + accuracy,
        max_chars=1800,
    )

    assert "aisbench 验收失败判定" in excerpt
    assert "Output Token Throughput is 186.3474" in excerpt
    assert "accuracy of /datasets/aime2025 is 0.0" in excerpt


def test_compact_error_prefers_aisbench_verdict_to_generic_exception():
    log = (
        "AssertionError: wrapper failed\n"
        "[ERROR] The following aisbench case failed, reason is Performance verification failed. "
        "The current Output Token Throughput is 186.3474 token/s.\n"
        "RuntimeError: command failed\n"
    )

    assert "Performance verification failed" in (_extract_log_error_summary(log) or "")


def test_parse_benchmark_metrics_reads_success_table_and_failed_baseline():
    log = (
        "##[group]Run Pytest\n"
        "{'case_name': 'vllm-api-stream-chat'}\n"
        "| Output Token Throughput | total | 901.7436 token/s |\n"
        "##[group]Stream logs\n"
        "The current Output Token Throughput is 901.7436 token/s, which is not greater than or equal to 0.97 * baseline 939.8253.\n"
        "Accuracy verification failed. The accuracy of /datasets/aime2025 is 93.2, which is not within 10 relative to baseline 93.33.\n"
        "##[group]Post job cleanup\n"
    )

    metrics = _parse_benchmark_metrics(log)
    ott = next(item for item in metrics if item["metric_type"] == "ott")
    accuracy = next(item for item in metrics if item["metric_type"] == "accuracy")

    assert ott["value"] == 901.7436
    assert ott["case_name"] == "OTT"
    assert ott["baseline"] == 939.8253
    assert ott["source_step"] == "stream_logs"
    assert accuracy["case_name"] == "Accuracy"
    assert accuracy["baseline"] == 93.33


def test_parse_benchmark_metrics_reads_success_markdown_accuracy_table():
    log = (
        "##[group]Run Pytest\n"
        "The markdown format results is as below:\n"
        "| dataset | version | metric | mode | vllm-api-general-chat |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| gsm8k | 50861a | accuracy | gen | 96.06 |\n"
    )

    metrics = _parse_benchmark_metrics(log)

    assert metrics == [{
        "case_name": "Accuracy",
        "metric_type": "accuracy",
        "value": 96.06,
        "baseline": None,
        "unit": "%",
        "source_step": "run_pytest",
        "source_excerpt": "| gsm8k | 50861a | accuracy | gen | 96.06 |",
    }]


def test_success_metrics_get_baseline_from_yaml_at_run_sha():
    metrics = _parse_benchmark_metrics(
        "##[group]Run Pytest\n| gsm8k | 50861a | accuracy | gen | 96.06 |\n"
    )
    config = """
benchmarks:
  acc:
    case_type: accuracy
    baseline: 96.74
    threshold: 2
  perf_1:
    case_type: performance
    baseline: 3583.8747
    threshold: 0.97
"""

    _apply_benchmark_config(metrics, config, "tests/e2e/cases/QWEN.yaml", "deadbeef")

    accuracy = metrics[0]
    assert accuracy["baseline"] == 96.74
    assert accuracy["threshold_tolerance"] == 2.0
    assert accuracy["config_path"] == "tests/e2e/cases/QWEN.yaml"
    assert accuracy["config_head_sha"] == "deadbeef"


def test_performance_config_ignores_zero_baseline_warmup_case():
    metrics = _parse_benchmark_metrics(
        "##[group]Stream logs\n| Output Token Throughput | total | 1059.36 token/s |\n"
    )
    config = """
benchmarks:
  perf_warmup:
    case_type: performance
    baseline: 0
    threshold: 0.97
  perf:
    case_type: performance
    baseline: 923.60
    threshold: 0.97
"""

    _apply_benchmark_config(metrics, config, "tests/e2e/cases/GLM.yaml", "deadbeef")

    ott = metrics[0]
    assert ott["baseline"] == 923.60
    assert ott["threshold_ratio"] == 0.97


def test_success_metric_reads_benchmark_nested_under_yaml_test_case():
    metrics = _parse_benchmark_metrics(
        "2026-10-12T17:00:59Z │ Output Token Throughput │ total │ 1280.7847 token/s │\n"
    )
    config = """
test_cases:
  - name: Qwen3_6_27b_A5_Acceptance_Rate_02
    benchmarks:
      perf:
        case_type: performance
        baseline: 342
        threshold: 0.95
"""

    _apply_benchmark_config(metrics, config, "tests/e2e/cases/Qwen3_6_27b_A5.yaml", "deadbeef")

    ott = metrics[0]
    assert ott["baseline"] == 342
    assert ott["threshold_ratio"] == 0.95


def test_benchmark_config_reference_prefers_full_path():
    log = "running tests/e2e/cases/models/configs/Qwen/QWEN3_235B_PD_3_5K_1_5k.yaml"

    assert _benchmark_config_reference(log) == "tests/e2e/cases/models/configs/Qwen/QWEN3_235B_PD_3_5K_1_5k.yaml"
    assert _benchmark_config_reference(
        "single-node (main, Qwen3_6_27b_A5.yaml, tests/e2e/cases/model... / Qwen3_6_27b_A5)"
    ) == "Qwen3_6_27b_A5.yaml"


def test_parse_benchmark_metrics_reads_unicode_stream_log_table():
    """Run Pytest tables can use box-drawing separators in GitHub raw logs."""
    log = (
        "2026-10-13T03:36:49Z ====> Run pytest entry: tests/e2e/test_multi_node.py\n"
        "2026-10-13T03:50:42Z │ Output Token Throughput │ total │ 0.0 token/s │\n"
        "2026-10-13T03:50:42Z [ERROR] The following aisbench case failed: "
        "{'case_type': 'performance', 'baseline': 3583.8747, 'case_name': 'perf_1'}, "
        "reason is Performance verification failed. The current Output Token Throughput is 0.0 "
        "token/s, which is not greater than or equal to 0.97 * baseline 3583.8747.\n"
    )

    metrics = _parse_benchmark_metrics(log)
    ott = next(item for item in metrics if item["metric_type"] == "ott" and item["case_name"] == "OTT")

    assert ott["value"] == 0.0
    assert ott["baseline"] == 3583.8747
    assert ott["source_step"] == "run_pytest"


def test_parse_benchmark_metrics_falls_back_when_downloaded_log_has_no_step_heading():
    log = "2026-10-12T17:00:59Z │ Output Token Throughput │ total │ 1280.7847 token/s │\n"

    metrics = _parse_benchmark_metrics(log)

    assert metrics == [{
        "case_name": "OTT",
        "metric_type": "ott",
        "value": 1280.7847,
        "baseline": None,
        "unit": "token/s",
        "source_step": "run_pytest",
        "source_excerpt": log.strip(),
    }]


def test_parse_benchmark_metrics_does_not_treat_accuracy_configuration_as_result():
    log = (
        "##[group]Run Pytest\n"
        "accuracy: 5\n"
        "{'case_type': 'accuracy', 'baseline': 96.74}\n"
    )

    assert _parse_benchmark_metrics(log) == []


def test_parse_benchmark_metrics_keeps_runtime_failed_accuracy_as_null():
    log = (
        "##[group]Stream logs\n"
        "[ERROR] The following aisbench case failed: {'case_type': 'accuracy', "
        "'baseline': 96.74, 'threshold': 2, 'case_name': 'acc'}, reason is "
        "Some errors happened to Aisbench runtime\n"
    )

    metrics = _parse_benchmark_metrics(log)

    assert len(metrics) == 1
    accuracy = metrics[0]
    assert accuracy["case_name"] == "Accuracy"
    assert accuracy["metric_type"] == "accuracy"
    assert accuracy["value"] is None
    assert accuracy["baseline"] == 96.74
    assert accuracy["threshold_ratio"] is None
    assert accuracy["threshold_tolerance"] == 2.0
    assert accuracy["reason"] == "Some errors happened to Aisbench runtime"
    assert accuracy["source_step"] == "stream_logs"


def test_performance_trends_cache_reuses_completed_job_evidence():
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        status="completed",
        started_at=datetime(2026, 10, 13, tzinfo=UTC),
        conclusion="failure",
        data='{"github": "payload"}',
    )
    metrics = [{"case_name": "OTT", "metric_type": "ott", "value": 100.0, "baseline": 110.0}]

    _save_performance_trends_cache(job, metrics, None)
    cache = _performance_trends_cache(job)

    assert cache is not None
    assert cache["items"] == metrics
    hydrated = _with_performance_trend_job_context(job, cache["items"])
    assert hydrated[0]["job_id"] == 123
    assert hydrated[0]["run_id"] == 456
    assert hydrated[0]["value"] == 100.0

    job.status = "in_progress"
    assert _performance_trends_cache(job) is None


def test_performance_trends_cache_rejects_entries_from_an_older_schema():
    job = SimpleNamespace(
        status="completed",
        data='{"dashboard_evidence": {"performance_trends": {"version": 3, "items": []}}}',
    )

    assert _performance_trends_cache(job) is None


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _OneResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _ScalarsResult:
    def scalars(self):
        return self

    def all(self):
        return []


@pytest.mark.asyncio
async def test_ci_runs_filter_and_order_by_workflow_start_time():
    db = AsyncMock()
    db.execute.side_effect = [
        _RowsResult([("nightly", None, None)]),
        _ScalarsResult(),
    ]

    await list_runs(
        db,
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 7, tzinfo=UTC),
        limit=100,
    )

    statement = db.execute.await_args_list[1].args[0]
    sql = str(statement.compile(
        dialect=mysql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert "ci_results.started_at >=" in sql
    assert "ci_results.started_at <=" in sql
    assert "ORDER BY ci_results.started_at DESC" in sql
    assert "coalesce(ci_results.completed_at, ci_results.started_at)" not in sql


@pytest.mark.asyncio
async def test_ci_stats_combines_workflow_and_hardware_filters_for_all_aggregates():
    db = AsyncMock()
    db.execute.side_effect = [
        _RowsResult([("nightly", None, None)]),
        _OneResult(SimpleNamespace(
            total_runs=4,
            passed_runs=1,
            failed_runs=1,
            avg_duration=90,
        )),
        _OneResult(SimpleNamespace(runs=2, success_runs=1, avg_duration=60)),
    ]

    result = await get_ci_stats(
        db,
        workflow_name="nightly",
        hardware="A2",
        start_time=datetime(2026, 9, 1, tzinfo=UTC),
        end_time=datetime(2026, 9, 7, tzinfo=UTC),
    )

    assert result["total_runs"] == 4
    assert result["passed_runs"] == 1
    assert result["failed_runs"] == 1
    assert result["other_runs"] == 2
    assert result["success_rate"] == 25.0
    assert result["last_7_days"]["success_rate"] == 50.0

    aggregate_statements = [call.args[0] for call in db.execute.await_args_list[1:]]
    for statement in aggregate_statements:
        sql = str(statement.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        ))
        assert "ci_results.workflow_name = 'nightly'" in sql
        assert "ci_results.hardware = 'A2'" in sql
        assert "ci_results.started_at >=" in sql
        assert "ci_results.started_at <=" in sql
        assert "coalesce(ci_results.completed_at, ci_results.started_at)" not in sql


@pytest.mark.asyncio
async def test_ci_stats_returns_all_count_fields_when_no_workflows_are_enabled():
    db = AsyncMock()
    db.execute.return_value = _RowsResult([])

    result = await get_ci_stats(db)

    assert result["total_runs"] == 0
    assert result["passed_runs"] == 0
    assert result["failed_runs"] == 0
    assert result["other_runs"] == 0


def test_ci_stats_contract_keeps_existing_fields_and_adds_build_counts():
    stats = CIStats.model_validate({
        "total_runs": 3,
        "passed_runs": 1,
        "failed_runs": 1,
        "other_runs": 1,
        "success_rate": 33.33,
        "avg_duration_seconds": None,
        "last_7_days": {"runs": 0, "success_rate": 0.0, "avg_duration_seconds": None},
    })

    assert stats.model_dump() == {
        "total_runs": 3,
        "passed_runs": 1,
        "failed_runs": 1,
        "other_runs": 1,
        "success_rate": 33.33,
        "avg_duration_seconds": None,
        "last_7_days": {"runs": 0, "success_rate": 0.0, "avg_duration_seconds": None},
    }
