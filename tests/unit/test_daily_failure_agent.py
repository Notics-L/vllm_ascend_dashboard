import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from daily_pipeline.failure_analysis import (
    AnalysisError,
    AnalysisRequest,
    Classification,
    DailyFailureAgent,
    HarnessOptions,
)
from daily_pipeline.failure_analysis import agent as agent_module

RUN = "https://github.com/org/repo/actions/runs/123"
JOB = f"{RUN}/job/456"
SUCCESS = "https://github.com/org/repo/actions/runs/100/job/200"
PR = "https://github.com/org/repo/pull/55"


def classification_response(**changes):
    return {
        "category": "code",
        "job_url": JOB,
        "summary": "The test failed after an API signature change.",
        "evidence": [{"kind": "log", "url": f"{JOB}#step:3:28", "detail": "TypeError"}],
        "missing_information": [],
        **changes,
    }


def localization_response(**candidate_changes):
    return {
        "summary": "A PR changed the failing call.",
        "comparisons": [
            {
                "repository": "org/repo",
                "successful_sha": "a" * 40,
                "failed_sha": "b" * 40,
            }
        ],
        "candidate_prs": [
            {
                "url": PR,
                "basis": "diff_match",
                "detail": "Changed the call signature.",
                "evidence": [{"kind": "pr", "url": f"{PR}/files", "detail": "Updated function."}],
                **candidate_changes,
            }
        ],
        "missing_information": [],
    }


@pytest.fixture
def make_agent(tmp_path):
    def create(*responses):
        run = Mock(
            side_effect=[json.dumps(item) if isinstance(item, dict) else item for item in responses]
        )

        @contextmanager
        def open_runner(options, workspace, request):
            assert workspace == tmp_path
            yield run

        options = HarnessOptions(harness_home=tmp_path / "harness", workspace=tmp_path)
        return DailyFailureAgent(options, runner_factory=open_runner), run

    return create


@pytest.mark.parametrize(
    "category", ["environment", "version_compatibility", "insufficient_evidence"]
)
def test_analyze_only_localizes_code_failures(make_agent, category):
    agent, run = make_agent(classification_response(category=category))

    result = agent.analyze(AnalysisRequest(failure_url=JOB))

    assert result.classification.category == category
    assert result.localization is None and result.localization_error is None
    assert run.call_count == 1


def test_analyze_passes_classification_to_localization(make_agent):
    fenced_response = f"```json\n{json.dumps(localization_response())}\n```"
    agent, run = make_agent(classification_response(), fenced_response)

    result = agent.analyze(AnalysisRequest(failure_url=JOB, case_name="cpu", success_url=SUCCESS))

    assert result.localization.candidate_prs[0].url.unicode_string() == PR
    assert result.localization.comparisons[0].successful_sha == "a" * 40
    assert result.localization_error is None and run.call_count == 2
    assert '"failure_url"' in run.call_args_list[0].args[0]
    assert "get_daily_job_context" in run.call_args_list[0].args[0]
    assert '"classification"' in run.call_args_list[1].args[0]


def test_ambiguous_run_returns_normal_insufficient_evidence(make_agent):
    response = classification_response(
        category="insufficient_evidence",
        job_url=None,
        evidence=[],
        missing_information=["Specify Job"],
    )
    agent, run = make_agent(response)

    result = agent.classify(AnalysisRequest(failure_url=RUN))

    assert result.job_url is None
    assert result.missing_information == ["Specify Job"]
    assert run.call_count == 1


@pytest.mark.parametrize(
    ("request_url", "changes", "message"),
    [
        (JOB, {"evidence": []}, "requires log evidence"),
        (JOB, {"job_url": f"{RUN}/job/999"}, "outside the requested failure"),
        (f"{RUN}/attempts/2", {"run_attempt": 1}, "requested workflow attempt"),
    ],
)
def test_classify_rejects_unverified_job_evidence(make_agent, request_url, changes, message):
    agent, _ = make_agent(classification_response(**changes))

    with pytest.raises(AnalysisError, match=message):
        agent.classify(AnalysisRequest(failure_url=request_url))


def test_classify_accepts_requested_attempt(make_agent):
    agent, _ = make_agent(classification_response(run_attempt=2))

    result = agent.classify(AnalysisRequest(failure_url=f"{RUN}/attempts/2"))

    assert result.run_attempt == 2


def test_locate_is_callable_with_serialized_classification(make_agent):
    agent, run = make_agent(localization_response(basis="timing_only"))
    classification = Classification.model_validate_json(json.dumps(classification_response()))

    result = agent.locate(AnalysisRequest(failure_url=JOB, success_url=SUCCESS), classification)

    assert result.candidate_prs[0].basis == "timing_only"
    assert run.call_count == 1


def test_locate_rejects_wrong_category_and_job_before_invoking_agent(make_agent):
    agent, run = make_agent()
    request = AnalysisRequest(failure_url=JOB)
    non_code = Classification.model_validate(classification_response(category="environment"))
    wrong_job = Classification.model_validate(classification_response(job_url=f"{RUN}/job/999"))

    with pytest.raises(AnalysisError, match="requires a code classification"):
        agent.locate(request, non_code)
    with pytest.raises(AnalysisError, match="outside the requested failure"):
        agent.locate(request, wrong_job)
    run.assert_not_called()


@pytest.mark.parametrize(
    "failed_response",
    [
        "invalid JSON",
        localization_response(url=f"{RUN}/job/456"),
        localization_response(evidence=[]),
        localization_response(
            evidence=[
                {
                    "kind": "pr",
                    "url": "https://github.com/org/repo/pull/99/files",
                    "detail": "Another PR",
                }
            ]
        ),
    ],
)
def test_localization_failure_preserves_valid_classification(make_agent, failed_response):
    agent, _ = make_agent(classification_response(), failed_response)

    result = agent.analyze(AnalysisRequest(failure_url=JOB))

    assert result.classification.category == "code"
    assert result.localization is None
    assert result.localization_error


@pytest.mark.parametrize(
    "inputs",
    [
        {"failure_url": "https://example.com/org/repo/actions/runs/123"},
        {"failure_url": JOB, "success_url": f"{JOB}#step:1:1"},
        {"failure_url": JOB, "case_name": "   "},
    ],
)
def test_request_validation(inputs):
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(inputs)


def test_harness_runner_does_not_prompt_when_read_only_mode_fails(monkeypatch, tmp_path):
    monkeypatch.setenv(agent_module.GITHUB_MCP_TOKEN, "dummy-token")
    monkeypatch.setattr(agent_module, "which", lambda _: "github-mcp-server")
    session = SimpleNamespace(run=Mock(return_value=SimpleNamespace(finish_reason="error")))
    harness = SimpleNamespace(start_session=lambda: session, close=Mock())
    monkeypatch.setitem(
        agent_module.sys.modules,
        "deepseek_harness",
        SimpleNamespace(DeepSeekHarness=lambda **_: harness),
    )
    monkeypatch.setitem(
        agent_module.sys.modules,
        "deepseek_harness.errors",
        SimpleNamespace(HarnessError=RuntimeError),
    )

    with agent_module.harness_runner(
        HarnessOptions(harness_home=tmp_path / "harness"),
        tmp_path,
        AnalysisRequest(failure_url=JOB),
    ) as run:
        with pytest.raises(AnalysisError, match="read-only permission preset"):
            run("Read the logs")

    session.run.assert_called_once_with("/permission read-only")
    harness.close.assert_called_once()
