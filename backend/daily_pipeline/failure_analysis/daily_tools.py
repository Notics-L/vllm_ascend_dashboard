"""Read-only GitHub Actions evidence tools for Daily failure analysis."""

from __future__ import annotations

import os
import re
from itertools import islice
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import Field

from .agent import GITHUB_MCP_TOKEN, AnalysisRequest, parse_actions_link

GITHUB_API = "https://api.github.com"
GITHUB_TIMEOUT_SECONDS = 15
JOBS_PER_PAGE = 100
MAX_JOB_PAGES = 5
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_LINE_LENGTH = 600
MAX_EXCERPT_LINES = 30
FAILURE_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
Target = Literal["failure", "success"]


class DailyEvidence:
    def __init__(self, client: httpx.Client, request: AnalysisRequest, workspace: Path):
        self._client = client
        self._request = request
        self._workspace = workspace
        self._jobs: dict[Target, dict] = {}
        self._logs: dict[str, tuple[list[str], bool]] = {}

    def _list_jobs(self, path: str) -> list[dict] | None:
        jobs: list[dict] = []
        for page in range(1, MAX_JOB_PAGES + 1):
            result = self._get(path, params={"page": page, "per_page": JOBS_PER_PAGE})
            batch = result.json()["jobs"]
            jobs.extend(batch)
            if "next" not in result.links:
                return jobs
        return None

    def _get(self, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._client.get(path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"GitHub job metadata unavailable (HTTP {exc.response.status_code})"
            ) from None
        except httpx.RequestError:
            raise RuntimeError("GitHub job metadata request failed") from None

    def get_daily_job_context(self, target: Target = "failure") -> dict:
        """Resolve the Job of the requested Daily failure or supplied successful baseline."""
        if target in self._jobs:
            return self._jobs[target]

        url = self._request.failure_url if target == "failure" else self._request.success_url
        if url is None:
            return {"job": None, "missing": "No successful comparison link supplied"}
        link = parse_actions_link(url)
        actions = f"/repos/{link.repository}/actions"

        if link.job_id is None:
            run_path = f"{actions}/runs/{link.run_id}"
            if link.attempt is not None:
                run_path += f"/attempts/{link.attempt}"
            jobs = self._list_jobs(f"{run_path}/jobs")
            if jobs is None:
                return {"job": None, "missing": "Job list exceeded the limit. Supply a Job link."}
        else:
            jobs = [self._get(f"{actions}/jobs/{link.job_id}").json()]

        conclusions = FAILURE_CONCLUSIONS if target == "failure" else {"success"}
        candidates = [
            job
            for job in jobs
            if str(job.get("run_id")) == link.run_id
            and job.get("conclusion") in conclusions
            and (link.job_id is None or str(job.get("id")) == link.job_id)
        ]
        if link.job_id is None and self._request.case_name:
            case_name = self._request.case_name.casefold()
            exact = [job for job in candidates if case_name == job["name"].casefold()]
            candidates = exact or [job for job in candidates if case_name in job["name"].casefold()]
        run_url = f"https://github.com/{link.repository}/actions/runs/{link.run_id}"
        if len(candidates) != 1:
            return {
                "job": None,
                "missing": "Supply the exact Job link"
                if candidates
                else "No matching completed Job",
                "candidates": [
                    {"name": job["name"], "url": f"{run_url}/job/{job['id']}"}
                    for job in candidates[:MAX_EXCERPT_LINES]
                ],
            }

        job = candidates[0]
        context = {
            "job": {
                "id": job["id"],
                "url": f"{run_url}/job/{job['id']}",
                "repository": link.repository,
                "name": job["name"],
                "conclusion": job["conclusion"],
                "run_attempt": int(link.attempt) if link.attempt else job.get("run_attempt"),
                "workflow_head_sha": job.get("head_sha"),
                "runner_labels": job.get("labels", []),
                "steps": [
                    {
                        "number": step["number"],
                        "name": step["name"],
                        "conclusion": step["conclusion"],
                    }
                    for step in job.get("steps", [])
                ],
            }
        }
        self._jobs[target] = context
        return context

    def _load_job_log(self, job: dict) -> tuple[list[str], bool]:
        if job["url"] in self._logs:
            return self._logs[job["url"]]

        path = self._workspace / f"job-{job['id']}.log"
        if path.is_file():
            with path.open("rb") as log:
                data = log.read(MAX_LOG_BYTES + 1)
        else:
            data = bytearray()
            try:
                with self._client.stream(
                    "GET",
                    f"/repos/{job['repository']}/actions/jobs/{job['id']}/logs",
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        remaining = MAX_LOG_BYTES + 1 - len(data)
                        data.extend(chunk[:remaining])
                        if len(data) > MAX_LOG_BYTES:
                            break
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"GitHub Job log unavailable (HTTP {exc.response.status_code})"
                ) from None
            except httpx.RequestError:
                raise RuntimeError("GitHub Job log request failed") from None

        truncated = len(data) > MAX_LOG_BYTES
        text = data[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text = text.rpartition("\n")[0]
        result = (ANSI_ESCAPE.sub("", text).splitlines(), truncated)
        self._logs[job["url"]] = result
        return result

    def search_daily_job_log(
        self,
        target: Target = "failure",
        query: Annotated[str | None, Field(max_length=200)] = None,
        start_line: Annotated[int, Field(ge=1)] = 1,
        limit: Annotated[int, Field(ge=1, le=MAX_EXCERPT_LINES)] = 12,
    ) -> dict:
        """Search the selected Job's log for literal text, or read consecutive lines with no query."""
        context = self.get_daily_job_context(target)
        if context["job"] is None:
            return context
        job = context["job"]
        lines, truncated = self._load_job_log(job)
        query = query.casefold() if query else ""
        matches = (
            (index, line)
            for index, line in enumerate(lines, 1)
            if index >= start_line and query in line.casefold()
        )
        window = list(islice(matches, limit + 1))
        return {
            "job_url": job["url"],
            "lines": [
                {"line": index, "text": line[:MAX_LINE_LENGTH]} for index, line in window[:limit]
            ],
            "line_count": len(lines),
            "truncated": truncated,
            "next_line": window[limit][0] if len(window) > limit else None,
        }


def main() -> None:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    request = AnalysisRequest.model_validate_json(os.environ["DAILY_FAILURE_ANALYSIS_REQUEST"])
    workspace = Path(os.environ["DAILY_FAILURE_ANALYSIS_WORKSPACE"])
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.getenv(GITHUB_MCP_TOKEN):
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(
        base_url=GITHUB_API, headers=headers, timeout=GITHUB_TIMEOUT_SECONDS
    ) as client:
        evidence = DailyEvidence(client, request, workspace)
        server = FastMCP("Daily CI evidence")
        annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
        server.add_tool(evidence.get_daily_job_context, annotations=annotations)
        server.add_tool(evidence.search_daily_job_log, annotations=annotations)
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
