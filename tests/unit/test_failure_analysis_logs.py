import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import httpx
import pytest

from failure_analysis.failure_analysis import FailureAnalysisService
from infrastructure.clients import github_cache
from infrastructure.clients.claude_code_cli import ClaudeCodeCLI
from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.config import settings


def test_claude_cli_never_inherits_dashboard_github_credentials(monkeypatch):
    names = (
        "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_ACCESS_TOKEN", "GITHUB_API_TOKEN",
    )
    for name in names:
        monkeypatch.setenv(name, "test-github-credential")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "test-configured-github-credential")

    env = ClaudeCodeCLI()._build_env_direct("test-provider-key", "http://localhost:8080", "model")

    assert not set(names).intersection(env)
    assert "test-configured-github-credential" not in env.values()
    assert env["ANTHROPIC_API_KEY"] == "test-provider-key"
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8080"


@pytest.mark.asyncio
async def test_analysis_downloads_missing_logs_via_audited_backend_and_cleans_on_cancel(
    tmp_path, monkeypatch, github_audit_records,
):
    service = FailureAnalysisService()
    monkeypatch.setattr(service, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "test-configured-github-credential")
    shared = tmp_path / "failure-analysis" / "Nightly"
    shared.mkdir(parents=True)
    archive = shared / "run_456_logs.zip"
    archive.write_bytes(b"existing archive")
    requests = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-configured-github-credential"
        requests.append(str(request.url))
        if request.url.path.endswith("/logs"):
            return httpx.Response(200, content=b"temporary log evidence")
        return httpx.Response(200, json={"jobs": []})

    monkeypatch.setattr(
        "failure_analysis.failure_analysis.GitHubAsyncClient",
        lambda **kwargs: GitHubAsyncClient(transport=httpx.MockTransport(respond), **kwargs),
    )
    downloaded = asyncio.Event()
    paths = {}

    async def analyze():
        with service._temporary_analysis_logs() as workspace:
            result = await service._download_all_logs(SimpleNamespace(
                job_id=123, run_id=456, workflow_name="Nightly", job_name="example",
            ))
            paths.update(workspace=workspace, job=Path(result["job_log"]), jobs=Path(result["jobs_list"]))
            assert Path(result["run_log_zip"]) == archive
            downloaded.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(analyze())
    await asyncio.wait_for(downloaded.wait(), timeout=5)
    assert paths["job"].read_bytes() == b"temporary log evidence"
    assert paths["jobs"].exists()
    assert len(requests) == 2
    assert requests[0].endswith("/actions/jobs/123/logs")
    assert requests[1].endswith("/actions/runs/456/jobs?per_page=100")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not paths["workspace"].exists()
    assert archive.read_bytes() == b"existing archive"


def test_extract_report_summary_unwraps_nested_json_envelope():
    payload = (
        '{"result":"{\\"report\\":{\\"problem_category\\":\\"基础设施\\",'
        '\\"root_cause_summary\\":\\"Runner 断开\\",'
        '\\"improvement_measures_summary\\":\\"重试并检查 Runner\\"}}"}'
    )

    assert FailureAnalysisService._extract_report_summary(payload) == {
        "problem_category": "基础设施",
        "root_cause_summary": "Runner 断开",
        "improvement_measures_summary": "重试并检查 Runner",
    }


def test_extract_report_summary_reads_fields_from_malformed_wrapper():
    payload = (
        '前置说明 {"problem_category":"其他", '
        '"root_cause_summary":"证据不足", '
        '"improvement_measures_summary":"补充日志"'
    )

    assert FailureAnalysisService._extract_report_summary(payload) == {
        "problem_category": "其他",
        "root_cause_summary": "证据不足",
        "improvement_measures_summary": "补充日志",
    }


def test_extract_report_summary_accepts_gateway_aliases_and_list_values():
    payload = (
        '{"report":{"category":"基础设施", "root_cause":"Runner 断开", '
        '"recommendations":["重试任务", "检查 Runner"]}}'
    )

    assert FailureAnalysisService._extract_report_summary(payload) == {
        "problem_category": "基础设施",
        "root_cause_summary": "Runner 断开",
        "improvement_measures_summary": "重试任务；检查 Runner",
    }


def test_extract_report_summary_merges_sibling_json_objects():
    payload = (
        '{"result":{"problem_category":"基础设施"},'
        '"content":{"root_cause_summary":"Runner 断开",'
        '"improvement_measures_summary":"重试并检查 Runner"}}'
    )

    assert FailureAnalysisService._extract_report_summary(payload) == {
        "problem_category": "基础设施",
        "root_cause_summary": "Runner 断开",
        "improvement_measures_summary": "重试并检查 Runner",
    }


def test_extract_report_summary_recovers_markdown_headings_without_json_footer():
    payload = """## 失败原因
Runner 在初始化阶段断开，日志显示连接被远端关闭。

## 改进建议
检查 Runner 网络和启动依赖后重新执行。
"""

    assert FailureAnalysisService._extract_report_summary(payload) == {
        "root_cause_summary": "Runner 在初始化阶段断开，日志显示连接被远端关闭。",
        "improvement_measures_summary": "检查 Runner 网络和启动依赖后重新执行。",
    }


def test_cli_prefers_nested_answer_with_more_report_fields():
    payload = {
        "result": '{"problem_category":"基础设施"}',
        "content": '{"problem_category":"基础设施",'
        '"root_cause_summary":"Runner 断开",'
        '"improvement_measures_summary":"重试并检查 Runner"}',
    }

    assert ClaudeCodeCLI._content_from_payload(payload) == payload["content"]


def test_cli_max_turns_envelope_is_not_treated_as_partial_report():
    payload = {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "terminal_reason": "max_turns",
        "num_turns": 101,
    }
    cli = ClaudeCodeCLI()

    result = cli._parse_output(
        json.dumps(payload), "", 1.0, "json", "glm-5.3", exit_code=1
    )

    assert result.turns == 101
    assert ClaudeCodeCLI._error_from_payload(result.raw_json) == (
        "Claude Code CLI 在生成最终报告前达到最大分析轮次"
        "（已执行 101 轮）；请缩小分析范围或提高轮次上限后重试"
    )


def test_legacy_parser_recovery_is_usable_for_missing_renderer_fields():
    parsed = FailureAnalysisService.parse_llm_response(
        '{"root_cause_summary":"Runner 断开",'
        '"improvement_measures_summary":"重试并检查 Runner"}'
    )

    assert parsed["root_cause_summary"] == "Runner 断开"
    assert parsed["improvement_measures_summary"] == "重试并检查 Runner"
    assert FailureAnalysisService._has_recoverable_report_text(parsed) is True


def test_legacy_parser_recovers_markdown_without_invalid_regex():
    """A non-JSON Markdown report must not fail during its final fallback."""
    parsed = FailureAnalysisService.parse_llm_response(
        """问题分类：基础设施
**根因摘要**：Runner 初始化时网络连接被远端关闭。
**改进措施**：检查 Runner 网络和启动依赖后重试。
"""
    )

    assert parsed["problem_category"] == "基础设施"
    assert parsed["root_cause_summary"] == "Runner 初始化时网络连接被远端关闭。"
    assert parsed["improvement_measures_summary"] == "检查 Runner 网络和启动依赖后重试。"


@pytest.mark.asyncio
async def test_claude_cli_context_prepares_last_good_evidence(monkeypatch):
    """CLI analyses always receive the resolved branch-safe comparison index."""
    service = FailureAnalysisService()
    service._get_ci_result = AsyncMock(return_value=None)
    service._download_all_logs = AsyncMock(
        return_value={
            "job_log": None,
            "run_log_zip": None,
            "artifacts_dir": None,
            "jobs_list": None,
        }
    )
    service._fetch_job_annotations = AsyncMock(side_effect=AssertionError("must not preload"))
    service._fetch_historical_run_comparison = AsyncMock(
        side_effect=AssertionError("must not preload")
    )
    service._fetch_commit_diff = AsyncMock(side_effect=AssertionError("must not preload"))
    service._prepare_last_good_evidence = AsyncMock(
        return_value=("### Last-good 对比证据\n未找到兼容成功运行", {})
    )
    monkeypatch.setattr(
        github_cache,
        "ensure_analysis_repos_ready",
        lambda *, update: {"vllm_ascend": "/cache/ascend", "vllm": "/cache/vllm"},
    )

    context = await service._build_job_context(
        SimpleNamespace(
            job_id=123,
            run_id=456,
            workflow_name="Nightly-A3",
            job_name="single-node (main, qwen)",
            hardware="A3",
            runner_name="runner-1",
            runner_labels="[]",
            conclusion="failure",
            duration_seconds=10,
            started_at=None,
            completed_at=None,
            steps_data="[]",
        ),
        db=object(),
    )

    assert "Last-good 对比证据" in context
    assert "先读取本地索引中的最小必要证据" in context
    assert service._download_all_logs.await_args.args[1] is not None
    assert service._download_all_logs.await_args.kwargs["materialize_artifacts"] is True
    service._prepare_last_good_evidence.assert_awaited_once()
    service._fetch_job_annotations.assert_not_awaited()
    service._fetch_historical_run_comparison.assert_not_awaited()
    service._fetch_commit_diff.assert_not_awaited()


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowsDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _statement):
        return _RowsResult(self.rows)


@pytest.mark.asyncio
async def test_last_good_evidence_downloads_branch_matching_artifacts(tmp_path):
    service = FailureAnalysisService()
    good_commit = "a" * 40
    bad_commit = "b" * 40
    log_path = tmp_path / "last-good.log"
    log_path.write_text(
        f"Branch: main\nCommit hash: {good_commit}\n", encoding="utf-8"
    )
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    current = SimpleNamespace(
        job_id=2,
        run_id=200,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen)",
        hardware="A3",
        completed_at=None,
        started_at=None,
    )
    candidate = SimpleNamespace(
        job_id=1,
        run_id=100,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen)",
        hardware="A3",
        conclusion="success",
    )
    run = SimpleNamespace(run_number=10, run_id=100)
    service._download_all_logs = AsyncMock(
        return_value={
            "job_log": str(log_path),
            "artifacts_dir": str(artifacts_dir),
        }
    )

    evidence, comparison = await service._prepare_last_good_evidence(
        current,
        _RowsDb([(run, candidate)]),
        tested_branch="main",
        tested_commit=bad_commit,
    )

    assert f"good_commit `{good_commit}`" in evidence
    assert f"bad_commit `{bad_commit}`" in evidence
    assert "必做代码调查" in evidence
    assert comparison["good_commit"] == good_commit
    assert comparison["good_branch"] == "main"
    assert service._download_all_logs.await_count == 2
    assert service._download_all_logs.await_args_list[-1].kwargs["materialize_artifacts"] is True


@pytest.mark.asyncio
async def test_last_good_evidence_rejects_different_tested_branch(tmp_path):
    service = FailureAnalysisService()
    log_path = tmp_path / "wrong-branch.log"
    log_path.write_text(
        f"Branch: releases/v0.26.0rc\nCommit hash: {'a' * 40}\n",
        encoding="utf-8",
    )
    current = SimpleNamespace(
        job_id=2,
        run_id=200,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen)",
        hardware="A3",
        completed_at=None,
        started_at=None,
    )
    candidate = SimpleNamespace(
        job_id=1,
        run_id=100,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen)",
        hardware="A3",
        conclusion="success",
    )
    run = SimpleNamespace(run_number=10, run_id=100)
    service._download_all_logs = AsyncMock(return_value={"job_log": str(log_path)})

    evidence, comparison = await service._prepare_last_good_evidence(
        current,
        _RowsDb([(run, candidate)]),
        tested_branch="main",
        tested_commit="b" * 40,
    )

    assert "已全部排除" in evidence
    assert "禁止跨分支构造 good_commit" in evidence
    assert comparison == {}
    assert service._download_all_logs.await_count == 1


def test_extract_job_log_from_run_zip_uses_matching_matrix_job(tmp_path):
    archive_path = tmp_path / "run-logs.zip"
    destination = tmp_path / "logs" / "123.log"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "single-node (main, qwen3)/0_Setup.txt",
            "target failure output",
        )
        archive.writestr(
            "single-node (main, glm)/0_Setup.txt",
            "different matrix job",
        )

    recovered = FailureAnalysisService._extract_job_log_from_run_zip(
        archive_path,
        job_id=123,
        job_name="single-node (main, qwen3)",
        destination=destination,
    )

    assert recovered is True
    assert "target failure output" in destination.read_text(encoding="utf-8")
    assert "different matrix job" not in destination.read_text(encoding="utf-8")


def test_extract_job_log_from_run_zip_rejects_ambiguous_job(tmp_path):
    archive_path = tmp_path / "run-logs.zip"
    destination = tmp_path / "logs" / "123.log"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("unrelated-job/0_Setup.txt", "unrelated")

    recovered = FailureAnalysisService._extract_job_log_from_run_zip(
        archive_path,
        job_id=123,
        job_name="single-node (main, qwen3)",
        destination=destination,
    )

    assert recovered is False
    assert not destination.exists()


@pytest.mark.asyncio
async def test_log_preparation_failure_marks_existing_analysis_failed():
    job = SimpleNamespace(
        job_id=123,
        run_id=456,
        workflow_name="Nightly-A3",
        job_name="single-node (main, qwen3)",
        conclusion="failure",
        completed_at=None,
        steps_data="[]",
    )
    analysis = SimpleNamespace(
        id=99,
        workflow_name="Nightly-A3",
        analysis_status="analyzing",
        analysis_phase="queued",
        error_message=None,
        failure_fingerprint=None,
        triggered_by="manual",
    )

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    db = AsyncMock()
    db.execute.side_effect = [Result(job), Result(None), Result(analysis)]
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()

    service = FailureAnalysisService()
    service._get_llm_config = AsyncMock(
        return_value=SimpleNamespace(
            provider="zhipu",
            decrypted_api_key="masked",
            api_base_url="https://example.test",
            default_model="glm-5.2",
        )
    )
    service._get_agent_config = AsyncMock(
        return_value={"runtime": "claude_cli", "max_turns": 3, "timeout_seconds": 60}
    )
    service._get_system_prompt = AsyncMock(return_value="prompt")
    service._build_job_context = AsyncMock(
        side_effect=RuntimeError("Required GitHub job log unavailable")
    )

    result = await service.analyze_failed_job(123, db, force=True)

    assert result is analysis
    assert analysis.analysis_status == "failed"
    assert analysis.analysis_phase == "failed"
    assert "Required GitHub job log unavailable" in analysis.error_message
    db.commit.assert_awaited_once()
