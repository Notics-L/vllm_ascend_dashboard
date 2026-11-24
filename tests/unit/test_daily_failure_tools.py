import httpx

from daily_pipeline.failure_analysis import AnalysisRequest, daily_tools
from daily_pipeline.failure_analysis.daily_tools import DailyEvidence

RUN = "https://github.com/org/repo/actions/runs/123"
JOB = f"{RUN}/job/456"
JOB_API = "/repos/org/repo/actions/jobs/456"
RUN_API = "/repos/org/repo/actions/runs/123"


def job(job_id, name, *, run_id=123, conclusion="failure"):
    return {
        "id": job_id,
        "name": name,
        "run_id": run_id,
        "conclusion": conclusion,
        "head_sha": "a" * 40,
        "labels": ["linux", "a3-560t"],
        "steps": [{"number": 4, "name": "Run tests", "conclusion": conclusion}],
    }


def test_exact_job_has_verified_run_and_steps(tmp_path):
    def handle(request):
        assert request.url.path == JOB_API
        return httpx.Response(200, json=job(456, "A3 case"))

    with httpx.Client(
        base_url=daily_tools.GITHUB_API, transport=httpx.MockTransport(handle)
    ) as client:
        result = DailyEvidence(
            client, AnalysisRequest(failure_url=JOB), tmp_path
        ).get_daily_job_context()

    assert result["job"]["url"] == JOB
    assert result["job"]["runner_labels"] == ["linux", "a3-560t"]
    assert result["job"]["steps"][0]["number"] == 4


def test_job_from_another_run_cannot_supply_log_evidence(tmp_path):
    def handle(request):
        assert request.url.path == JOB_API
        return httpx.Response(200, json=job(456, "A3 case", run_id=124))

    with httpx.Client(
        base_url=daily_tools.GITHUB_API, transport=httpx.MockTransport(handle)
    ) as client:
        result = DailyEvidence(
            client, AnalysisRequest(failure_url=JOB), tmp_path
        ).search_daily_job_log()

    assert result == {
        "job": None,
        "missing": "No matching completed Job",
        "candidates": [],
    }


def test_attempt_search_checks_all_pages_and_refuses_ambiguous_matches(tmp_path):
    pages = []

    def handle(request):
        assert request.url.path == f"{RUN_API}/attempts/2/jobs"
        page = request.url.params["page"]
        pages.append(page)
        if page == "1":
            return httpx.Response(
                200,
                json={"jobs": [job(456, "A3 case (main)")]},
                headers={
                    "Link": f'<{daily_tools.GITHUB_API}{request.url.path}?page=2>; rel="next"'
                },
            )
        return httpx.Response(200, json={"jobs": [job(789, "A3 case (release)")]})

    with httpx.Client(
        base_url=daily_tools.GITHUB_API, transport=httpx.MockTransport(handle)
    ) as client:
        link = f"{RUN}/attempts/2"
        result = DailyEvidence(
            client, AnalysisRequest(failure_url=link, case_name="A3 case"), tmp_path
        ).get_daily_job_context()
        selected = DailyEvidence(
            client,
            AnalysisRequest(failure_url=link, case_name="A3 case (release)"),
            tmp_path,
        ).get_daily_job_context()

    assert pages == ["1", "2", "1", "2"]
    assert result["job"] is None
    assert [entry["url"] for entry in result["candidates"]] == [JOB, f"{RUN}/job/789"]
    assert selected["job"]["url"] == f"{RUN}/job/789"
    assert selected["job"]["run_attempt"] == 2


def test_search_uses_the_job_log_and_does_not_forward_github_token(tmp_path):
    urls = []

    def handle(request):
        urls.append(str(request.url))
        if request.url.host == "logs.example.com":
            assert "Authorization" not in request.headers
            return httpx.Response(
                200, text="Start\n\x1b[31mError: fail\x1b[0m\nTrace\nERROR: exit\n"
            )
        assert request.headers["Authorization"] == "Bearer fixture"
        if request.url.path == JOB_API:
            return httpx.Response(200, json=job(456, "A3 case"))
        assert request.url.path == f"{JOB_API}/logs"
        return httpx.Response(302, headers={"Location": "https://logs.example.com/fixture"})

    with httpx.Client(
        base_url=daily_tools.GITHUB_API,
        headers={"Authorization": "Bearer fixture"},
        transport=httpx.MockTransport(handle),
    ) as client:
        evidence = DailyEvidence(client, AnalysisRequest(failure_url=JOB), tmp_path)
        first = evidence.search_daily_job_log(query="error", limit=1)
        last = evidence.search_daily_job_log(query="error", start_line=first["next_line"])

    assert len(urls) == 3
    assert first["job_url"] == JOB
    assert first["lines"] == [{"line": 2, "text": "Error: fail"}]
    assert last["lines"] == [{"line": 4, "text": "ERROR: exit"}]
    assert last["next_line"] is None


def test_prepared_log_is_bounded_and_reports_truncation(monkeypatch, tmp_path):
    def handle(request):
        assert request.url.path == JOB_API
        return httpx.Response(200, json=job(456, "A3 case"))

    monkeypatch.setattr(daily_tools, "MAX_LOG_BYTES", 10)
    (tmp_path / "job-456.log").write_bytes(b"Error\n" + b"x" * 30)
    with httpx.Client(
        base_url=daily_tools.GITHUB_API, transport=httpx.MockTransport(handle)
    ) as client:
        result = DailyEvidence(
            client, AnalysisRequest(failure_url=JOB), tmp_path
        ).search_daily_job_log()

    assert result["lines"] == [{"line": 1, "text": "Error"}]
    assert result["truncated"] is True


def test_success_context_remains_independent_of_failed_job(tmp_path):
    def handle(request):
        assert request.url.path == "/repos/org/repo/actions/jobs/900"
        return httpx.Response(200, json=job(900, "A3 case", run_id=100, conclusion="success"))

    success_url = "https://github.com/org/repo/actions/runs/100/job/900"
    request = AnalysisRequest(failure_url=JOB, success_url=success_url)
    with httpx.Client(
        base_url=daily_tools.GITHUB_API, transport=httpx.MockTransport(handle)
    ) as client:
        result = DailyEvidence(client, request, tmp_path).get_daily_job_context("success")

    assert result["job"]["url"] == success_url
    assert result["job"]["conclusion"] == "success"
