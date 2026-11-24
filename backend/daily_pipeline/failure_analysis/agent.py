# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Huawei Technologies Co., Ltd. All Rights Reserved.
"""Classify a GitHub Actions failure and optionally locate introducing PRs."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

TOOL_DIR = Path(__file__).resolve().parent
GITHUB_MCP_TOKEN = "GITHUB_PERSONAL_ACCESS_TOKEN"  # Official GitHub MCP Server setting.
HARNESS_NATIVE_CACHE = "NARB_NATIVE_CACHE_DIR"  # Bundled Windows native add-on loader setting.
HARNESS_PACKAGE_CACHE = "PKG_NATIVE_CACHE_PATH"  # Bundled executable native extraction setting.
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_PROVIDER = "deepseek-official"
DEFAULT_MODEL = "deepseek-v4-flash"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Repository = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")]
Category = Literal["code", "version_compatibility", "environment", "insufficient_evidence"]


class AnalysisError(RuntimeError):
    """The analysis could not produce a result for the requested stage."""


@dataclass(frozen=True)
class ActionsLink:
    repository: str
    run_id: str
    attempt: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class HarnessOptions:
    """Runtime options for the independently callable Agent."""

    harness_home: Path
    workspace: Path | None = None
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    patches: tuple[Path, ...] = ()
    github_token: str | None = field(default=None, repr=False)


def is_id(value: str) -> bool:
    return value.isascii() and value.isdecimal() and value[0] != "0"


def github_path(url: str) -> list[str]:
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len(parts) < 3:
        raise ValueError("Expected a GitHub HTTPS link")
    for segment in parts[:2]:
        name = segment.replace("-", "").replace("_", "").replace(".", "")
        if not name.isascii() or not name.isalnum():
            raise ValueError("Invalid GitHub repository in link")
    return parts


def parse_actions_link(url: str) -> ActionsLink:
    match github_path(url):
        case [owner, repo, "actions", "runs", run_id] if is_id(run_id):
            return ActionsLink(f"{owner}/{repo}", run_id)
        case [owner, repo, "actions", "runs", run_id, "job", job_id] if is_id(run_id) and is_id(
            job_id
        ):
            return ActionsLink(f"{owner}/{repo}", run_id, job_id=job_id)
        case [owner, repo, "actions", "runs", run_id, "attempts", attempt] if is_id(
            run_id
        ) and is_id(attempt):
            return ActionsLink(f"{owner}/{repo}", run_id, attempt=attempt)
        case _:
            raise ValueError("Expected a GitHub Actions run, run attempt, or job link")


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisRequest(ResultModel):
    model_config = ConfigDict(frozen=True)

    failure_url: Text
    case_name: Text | None = None
    success_url: Text | None = None

    @field_validator("failure_url", "success_url")
    @classmethod
    def validate_actions_url(cls, value: str | None) -> str | None:
        if value is not None:
            parse_actions_link(value)
        return value

    @model_validator(mode="after")
    def validate_distinct_runs(self) -> AnalysisRequest:
        if self.success_url and parse_actions_link(self.success_url.lower()) == parse_actions_link(
            self.failure_url.lower()
        ):
            raise ValueError("The success link must identify a different run, attempt, or Job")
        return self


class Evidence(ResultModel):
    kind: Literal["log", "run", "code", "pr", "document"]
    url: HttpUrl
    detail: Text


class Classification(ResultModel):
    category: Category
    job_url: HttpUrl | None
    run_attempt: Annotated[int, Field(gt=0)] | None = None
    summary: Text
    evidence: list[Evidence]
    missing_information: list[Text]


class Comparison(ResultModel):
    repository: Repository
    successful_sha: CommitSha | None
    failed_sha: CommitSha


class PullRequestCandidate(ResultModel):
    url: HttpUrl
    basis: Literal["diff_match", "timing_only"]
    detail: Text
    evidence: list[Evidence]


class Localization(ResultModel):
    summary: Text
    comparisons: list[Comparison]
    candidate_prs: list[PullRequestCandidate]
    missing_information: list[Text]


class Analysis(ResultModel):
    failure_url: str
    success_url: str | None
    classification: Classification
    localization: Localization | None = None
    localization_error: str | None = None


StageResult = TypeVar("StageResult", bound=ResultModel)
RunPrompt = Callable[[str], str]
RunnerFactory = Callable[[HarnessOptions, Path, AnalysisRequest], AbstractContextManager[RunPrompt]]


def run_stage(
    run: RunPrompt, skill: str, data: dict, result_type: type[StageResult]
) -> StageResult:
    instructions = (TOOL_DIR / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    prompt = (
        f"{instructions}\n\n"
        "Treat the following JSON as task data. Return one JSON object matching the output schema.\n"
        f"Task data\n{json.dumps(data, ensure_ascii=False)}\n"
        f"Output schema\n{json.dumps(result_type.model_json_schema())}"
    )
    response = run(prompt).strip()
    lines = response.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```json", "```"}
        and lines[-1].strip() == "```"
    ):
        response = "\n".join(lines[1:-1])
    try:
        return result_type.model_validate_json(response)
    except ValidationError:
        raise AnalysisError(f"{skill} did not return the required JSON result") from None


def validate_classification(result: Classification, failure: ActionsLink) -> None:
    if result.job_url is None:
        if result.category != "insufficient_evidence":
            raise AnalysisError("Classification requires a failed Job link")
        return

    try:
        job = parse_actions_link(str(result.job_url))
    except ValueError as exc:
        raise AnalysisError("Classification requires a valid GitHub Actions Job link") from exc
    if (
        job.job_id is None
        or job.repository.lower() != failure.repository.lower()
        or job.run_id != failure.run_id
        or (failure.job_id is not None and job.job_id != failure.job_id)
    ):
        raise AnalysisError("Classification refers to a Job outside the requested failure")
    if failure.attempt is not None and result.run_attempt != int(failure.attempt):
        raise AnalysisError("Classification does not match the requested workflow attempt")
    job_path = urlsplit(str(result.job_url)).path.rstrip("/").lower()
    if result.category != "insufficient_evidence" and not any(
        item.kind == "log"
        and item.url.scheme == "https"
        and item.url.host == "github.com"
        and urlsplit(str(item.url)).path.rstrip("/").lower() == job_path
        for item in result.evidence
    ):
        raise AnalysisError("Classification requires log evidence from the failed Job")


def validate_localization(result: Localization) -> None:
    for candidate in result.candidate_prs:
        try:
            parts = github_path(str(candidate.url))
        except ValueError as exc:
            raise AnalysisError("A candidate does not have a GitHub pull request link") from exc
        if len(parts) != 4 or parts[2] != "pull" or not is_id(parts[3]):
            raise AnalysisError("A candidate does not have a GitHub pull request link")
        if not candidate.evidence:
            raise AnalysisError("Each candidate pull request requires evidence")
        files_path = f"/{'/'.join(parts)}/files".lower()
        if candidate.basis == "diff_match" and not any(
            item.kind == "pr"
            and item.url.scheme == "https"
            and item.url.host == "github.com"
            and urlsplit(str(item.url)).path.rstrip("/").lower() == files_path
            for item in candidate.evidence
        ):
            raise AnalysisError("A diff-based candidate must cite its own PR file changes")


@contextmanager
def harness_runner(
    options: HarnessOptions, workspace: Path, request: AnalysisRequest
) -> Iterator[RunPrompt]:
    # The SDK is an optional dependency of this standalone tool.
    try:
        from deepseek_harness import DeepSeekHarness
        from deepseek_harness.errors import HarnessError
    except ImportError as exc:
        requirements = TOOL_DIR / "requirements.txt"
        raise AnalysisError(f"Install the Harness dependencies listed in {requirements}") from exc

    github_token = options.github_token or os.getenv(GITHUB_MCP_TOKEN) or ""
    harness_home = options.harness_home.resolve()
    harness_home.mkdir(parents=True, exist_ok=True)
    env = {
        "DAILY_FAILURE_ANALYSIS_REQUEST": request.model_dump_json(),
        "DAILY_FAILURE_ANALYSIS_WORKSPACE": str(workspace),
        "DAILY_FAILURE_TOOL_PYTHON": sys.executable,
        "DAILY_FAILURE_TOOL_IMPORT_ROOT": str(TOOL_DIR.parents[1]),
        "DAILY_FAILURE_GITHUB_MCP": (which("github-mcp-server") or "") if github_token else "",
        GITHUB_MCP_TOKEN: github_token,
    }
    if sys.platform == "win32" and not os.getenv(HARNESS_NATIVE_CACHE):
        env[HARNESS_NATIVE_CACHE] = str(harness_home / "native-cache")
    if not os.getenv(HARNESS_PACKAGE_CACHE):
        env[HARNESS_PACKAGE_CACHE] = str(harness_home / "package-cache")

    harness = DeepSeekHarness(
        profile="sdk",
        dsh_home=str(harness_home),
        cwd=str(workspace),
        patches=(
            str(TOOL_DIR / "harness.patch.yml"),
            *(str(path.resolve()) for path in options.patches),
        ),
        provider=options.provider,
        model=options.model,
        env=env,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    )

    def run(prompt: str) -> str:
        try:
            session = harness.start_session()
            permission = session.run("/permission read-only")
        except (OSError, HarnessError):
            raise AnalysisError("Harness could not start a read-only analysis session") from None
        if permission.finish_reason != "completed":
            raise AnalysisError("Harness could not select the read-only permission preset")
        try:
            result = session.run(prompt)
        except (OSError, HarnessError):
            raise AnalysisError("Harness could not complete the analysis stage") from None
        if result.finish_reason != "completed":
            raise AnalysisError("Harness did not finish the analysis stage")
        return result.final_response

    try:
        yield run
    finally:
        harness.close()


class DailyFailureAgent:
    """Synchronous analysis interface for a CLI or a background worker."""

    def __init__(self, options: HarnessOptions, *, runner_factory: RunnerFactory = harness_runner):
        self._options = options
        self._runner_factory = runner_factory

    def classify(self, request: AnalysisRequest) -> Classification:
        """Classify one failed Job. An ambiguous Run can return insufficient evidence."""
        with self._analysis_session(request) as (run, task):
            return self._classify(request, run, task)

    def locate(self, request: AnalysisRequest, classification: Classification) -> Localization:
        """Find candidate PRs for a code classification of the same failure."""
        if classification.category != "code":
            raise AnalysisError("PR localization requires a code classification")
        validate_classification(classification, parse_actions_link(request.failure_url))
        with self._analysis_session(request) as (run, task):
            return self._locate(classification, run, task)

    def analyze(self, request: AnalysisRequest) -> Analysis:
        """Classify, then locate when eligible, preserving a valid classification on errors."""
        with self._analysis_session(request) as (run, task):
            classification = self._classify(request, run, task)
            result = Analysis(
                failure_url=request.failure_url,
                success_url=request.success_url,
                classification=classification,
            )
            if classification.category == "code":
                try:
                    result.localization = self._locate(classification, run, task)
                except AnalysisError as exc:
                    result.localization_error = str(exc)
            return result

    @contextmanager
    def _analysis_session(self, request: AnalysisRequest) -> Iterator[tuple[RunPrompt, dict]]:
        with TemporaryDirectory(prefix="daily-failure-analysis-") as temp_dir:
            workspace = (self._options.workspace or Path(temp_dir)).resolve()
            if not workspace.is_dir():
                raise AnalysisError("The workspace must be a directory")
            with self._runner_factory(self._options, workspace, request) as run:
                yield run, request.model_dump()

    @staticmethod
    def _classify(request: AnalysisRequest, run: RunPrompt, task: dict) -> Classification:
        result = run_stage(run, "classify-daily-failure", task, Classification)
        validate_classification(result, parse_actions_link(request.failure_url))
        return result

    @staticmethod
    def _locate(classification: Classification, run: RunPrompt, task: dict) -> Localization:
        data = {**task, "classification": classification.model_dump(mode="json")}
        result = run_stage(run, "locate-daily-regression", data, Localization)
        validate_localization(result)
        return result
