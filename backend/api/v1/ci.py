"""
CI 数据 API 路由
Phase 2: 实现数据采集和展示
"""
import asyncio
import csv
import io
import json
import logging
import re
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import yaml
from fastapi import APIRouter, HTTPException, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from sqlalchemy import case, func, or_, select, text

from api.deps import CurrentAdminUser, CurrentSuperAdminUser, CurrentUser, DbSession
from contracts.schemas import (
    CIDailyReport,
    CIJobDetailResponse,
    CIJobResponse,
    CIResultResponse,
    CIStats,
    CISyncResponse,
    CITrend,
    DailyFailureBatchUpdateRequest,
    DailyFailureJob,
    DailyFailureListResponse,
    DailyFailureStats,
    DailyFailureUpdateRequest,
    FailureAnalysisKnowledgeGraphResponse,
    FailureAnalysisListResponse,
    FailureAnalysisResponse,
    NightlyGanttResponse,
    NightlyTestCaseCreate,
    NightlyTestCaseResponse,
    NightlyTestCaseUpdate,
    WorkflowLatestResult,
)
from infrastructure.core.config import settings
from infrastructure.persistence.models import (
    CIJob,
    CIResult,
    DailyFailureRecord,
    JobFailureAnalysis,
    JobLogSummary,
    JobOwner,
    NightlyTestCase,
    WorkflowConfig,
)
from infrastructure.persistence.run_attempts import (
    extract_run_attempt,
    filter_current_run_attempt,
    is_current_run_attempt,
)
from infrastructure.storage.failure_analysis_file_store import FailureAnalysisFileStore
from tooling.ci_filters import build_workflow_time_filter
from tooling.ci_version_snapshot import (
    get_version_snapshot,
    parse_ci_version_snapshot,
    version_snapshot_step_name,
)
from tooling.model_fo_mapping import (
    load_model_fo_mappings,
    lookup_model_fo,
    seed_missing_model_fo_mappings,
    set_model_fo_mapping,
)
from tooling.official_ci_scope import build_official_ci_filter

logger = logging.getLogger(__name__)

router = APIRouter()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _find_metadata_value(payload: Any, aliases: set[str]) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in aliases and isinstance(value, (str, int, float, bool)):
                return value
        for value in payload.values():
            found = _find_metadata_value(value, aliases)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_metadata_value(value, aliases)
            if found is not None:
                return found
    return None


def _failure_stage(steps: list[Any]) -> dict[str, Any]:
    valid_steps = [step for step in steps if isinstance(step, dict)]
    failed_index = next(
        (index for index, step in enumerate(valid_steps) if step.get("conclusion") in {"failure", "timed_out", "cancelled"}),
        None,
    )
    failed = valid_steps[failed_index] if failed_index is not None else None
    successful_before = [
        step for step in valid_steps[:failed_index] if step.get("conclusion") == "success"
    ] if failed_index is not None else [step for step in valid_steps if step.get("conclusion") == "success"]
    return {
        "last_successful_step": successful_before[-1].get("name") if successful_before else None,
        "first_failed_step": failed.get("name") if failed else None,
        "direct_error": None,
        "source": "ci_jobs.steps_data",
    }


async def _fetch_pr_details_bounded(
    client: Any,
    owner: str,
    repo: str,
    pr_numbers: list[int],
    max_concurrency: int = 4,
) -> list[tuple[int, dict[str, Any] | None, str | None]]:
    """Fetch PR evidence concurrently without flooding the GitHub API."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(pr_number: int) -> tuple[int, dict[str, Any] | None, str | None]:
        async with semaphore:
            try:
                detail = await client.get_pr_detail(owner, repo, pr_number)
                return pr_number, detail, None
            except Exception as exc:
                return pr_number, None, str(exc)

    # gather preserves input order, keeping evidence aligned with commit order.
    return await asyncio.gather(*(fetch_one(number) for number in pr_numbers))


def _parse_evidence_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _merged_within_commit_boundary(
    merged_at: Any,
    start_commit_date: Any,
    end_commit_date: Any,
) -> bool:
    """Use PR merged_at as a temporal guard on top of GitHub's commit topology."""
    merged = _parse_evidence_datetime(merged_at)
    if merged is None:
        return False
    start = _parse_evidence_datetime(start_commit_date)
    end = _parse_evidence_datetime(end_commit_date)
    return not ((start is not None and merged <= start) or (end is not None and merged > end))


def _extract_merged_pr_number(title: str) -> int | None:
    """The final ``(#123)`` in a squash title identifies the PR being merged."""
    parenthesized = re.findall(r"\(#(\d+)\)", title)
    if parenthesized:
        return int(parenthesized[-1])
    references = re.findall(r"(?:PR\s*)?#(\d+)", title, flags=re.IGNORECASE)
    return int(references[-1]) if references else None


def _extract_revert_target(
    message: str,
    current_pr: int | None,
    sha_to_pr: dict[str, int],
) -> tuple[int | None, str | None]:
    """Return the original PR/SHA referenced by common GitHub revert formats."""
    sha_match = re.search(r"This reverts commit\s+`?([0-9a-f]{7,40})`?", message, re.I)
    if not sha_match:
        sha_match = re.search(r"Merge commit:\s*`?([0-9a-f]{7,40})`?", message, re.I)
    target_sha = sha_match.group(1) if sha_match else None
    target_pr = None
    if target_sha:
        target_pr = next(
            (number for sha, number in sha_to_pr.items() if sha.lower().startswith(target_sha.lower())),
            None,
        )

    if target_pr is None:
        for pattern in (r"Original PR:\s*#(\d+)", r"Revert of PR\s*#(\d+)"):
            match = re.search(pattern, message, re.I)
            if match:
                target_pr = int(match.group(1))
                break

    if target_pr is None and re.search(r"\brevert\b", message.splitlines()[0], re.I):
        references = [int(value) for value in re.findall(r"#(\d+)", message.splitlines()[0])]
        target_pr = next((number for number in references if number != current_pr), None)
    return target_pr, target_sha


def _extract_log_error_summary(log_text: str) -> str | None:
    """Return one compact actionable line from a GitHub Actions log."""
    ansi_escape = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    candidates: list[tuple[int, str]] = []
    for raw_line in log_text.splitlines():
        line = ansi_escape.sub("", raw_line).strip()
        lowered = line.lower()
        if not line or any(noise in lowered for noise in (
            'echo "::error::', "::error::upload failed", "process completed with exit code"
        )):
            continue
        score = 0
        if any(marker in lowered for marker in (
            "the following aisbench case failed",
            "performance verification failed",
            "accuracy verification failed",
        )):
            score = 10
        elif "traceback" in lowered:
            score = 5
        elif "assertionerror" in lowered or "exception" in lowered:
            score = 4
        elif "error:" in lowered or "[error]" in lowered:
            score = 3
        elif "failed:" in lowered:
            score = 2
        if score:
            candidates.append((score, line))
    if not candidates:
        return None
    best_score = max(item[0] for item in candidates)
    summary = [line for score, line in candidates if score == best_score][-1]
    return summary if len(summary) <= 240 else f"{summary[:237]}..."


def _benchmark_log_sections(log_text: str) -> list[tuple[str, list[str]]]:
    """Return benchmark evidence from each explicit Run Pytest / Stream logs step."""
    lines = log_text.splitlines()
    starts = [
        (index, "run_pytest" if "run pytest" in line.lower() else "stream_logs")
        for index, line in enumerate(lines)
        if "run pytest" in line.lower() or "stream logs" in line.lower()
    ]
    # The Actions log download API sometimes omits the web UI step headings
    # entirely, while retaining the benchmark tables.  In that form there is
    # no section boundary to inspect, so parse the raw job log as Run Pytest
    # evidence instead of silently discarding valid OTT/Accuracy output.
    if not starts:
        return [("run_pytest", lines)]
    sections: list[tuple[str, list[str]]] = []
    for start, source_step in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            lowered = lines[index].lower()
            if "##[group]" in lowered or "post job cleanup" in lowered:
                end = index
                break
        sections.append((source_step, lines[start:end]))
    return sections


def _case_name_near(lines: list[str], index: int, metric_type: str, default_case_name: str | None = None) -> str:
    """Use the nearest aisbench case or dataset as the chart series identity."""
    for line in reversed(lines[max(0, index - 100):index + 1]):
        case_match = re.search(r"[\"']case_name[\"']\s*:\s*[\"']([^\"']+)", line, re.I)
        if case_match:
            return case_match.group(1)
        dataset_match = re.search(r"accuracy\s+of\s+([^,\s]+)", line, re.I)
        if dataset_match:
            return dataset_match.group(1)
    if default_case_name and metric_type == "ott":
        return default_case_name
    return "OTT" if metric_type == "ott" else "Accuracy"


def _parse_benchmark_metrics(log_text: str) -> list[dict[str, Any]]:
    """Parse benchmark metrics from explicit Run Pytest and Stream logs steps."""
    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    case_metadata: dict[str, dict[str, Any]] = {}
    ott_pattern = re.compile(
        r"output\s+token\s+throughput\s*(?:is|:|=|[|│])\s*(?:total\s*[|│]\s*)?([0-9]+(?:\.[0-9]+)?)",
        re.I,
    )
    accuracy_pattern = re.compile(
        r"(?:\baccuracy\s+of\s+[^,\s]+\s+is\s*|[|│]\s*accuracy\s*[|│]\s*(?:total\s*[|│]\s*)?)([0-9]+(?:\.[0-9]+)?)",
        re.I,
    )
    accuracy_markdown_pattern = re.compile(
        r"[|│]\s*[^|│]+\s*[|│]\s*[^|│]+\s*[|│]\s*accuracy\s*[|│]\s*[^|│]+\s*[|│]\s*([0-9]+(?:\.[0-9]+)?)",
        re.I,
    )
    baseline_pattern = re.compile(r"baseline\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", re.I)

    for source_step, lines in _benchmark_log_sections(log_text):
        for index, line in enumerate(lines):
            if "the following aisbench case failed" in line.lower():
                case_type_match = re.search(r"[\"']case_type[\"']\s*:\s*[\"']([^\"']+)", line, re.I)
                if case_type_match:
                    baseline_match = re.search(r"[\"']baseline[\"']\s*:\s*([0-9]+(?:\.[0-9]+)?)", line, re.I)
                    threshold_match = re.search(r"[\"']threshold[\"']\s*:\s*([0-9]+(?:\.[0-9]+)?)", line, re.I)
                    reason = line.partition("reason is")[2].strip() or None
                    case_metadata[case_type_match.group(1).lower()] = {
                        "baseline": float(baseline_match.group(1)) if baseline_match else None,
                        "threshold": float(threshold_match.group(1)) if threshold_match else None,
                        "reason": reason,
                        "source_step": source_step,
                        "source_excerpt": line.strip()[:500],
                    }
            for metric_type, pattern, unit in (
                ("ott", ott_pattern, "token/s"),
                ("accuracy", accuracy_pattern, "%"),
            ):
                match = pattern.search(line)
                if metric_type == "accuracy" and not match:
                    # Successful aisbench output uses a Markdown row such as
                    # ``| gsm8k | 50861a | accuracy | gen | 96.06 |``.
                    match = accuracy_markdown_pattern.search(line)
                if not match:
                    continue
                # Prefer the metric line itself.  Looking backwards can attach
                # an OTT baseline to a following accuracy verdict.
                baseline_match = baseline_pattern.search(line)
                if not baseline_match:
                    baseline_match = baseline_pattern.search(
                        " ".join(lines[index + 1:min(len(lines), index + 3)])
                    )
                # OTT is the same throughput metric across benchmark cases.
                # Keep it in one history series so a case label emitted later
                # in a failed log cannot hide otherwise comparable values.
                case_name = "OTT" if metric_type == "ott" else "Accuracy"
                metrics[(metric_type, case_name)] = {
                    "case_name": case_name,
                    "metric_type": metric_type,
                    "value": float(match.group(1)),
                    "baseline": float(baseline_match.group(1)) if baseline_match else None,
                    "unit": unit,
                    "source_step": source_step,
                    "source_excerpt": line.strip()[:500],
                }

    for metric in metrics.values():
        metadata = case_metadata.get(metric["metric_type"])
        if not metadata:
            continue
        if metric["baseline"] is None:
            metric["baseline"] = metadata["baseline"]
        threshold = metadata["threshold"]
        if threshold is not None and threshold <= 1:
            metric["threshold_ratio"] = threshold
            metric["threshold_tolerance"] = None
        elif threshold is not None:
            metric["threshold_ratio"] = None
            metric["threshold_tolerance"] = threshold
        metric["reason"] = metadata["reason"]

    # Aisbench can fail before calculating accuracy.  Preserve that state as
    # null so the chart does not turn a runtime error into an Accuracy = 0.
    accuracy_metadata = case_metadata.get("accuracy")
    if accuracy_metadata and not any(metric["metric_type"] == "accuracy" for metric in metrics.values()):
        threshold = accuracy_metadata["threshold"]
        metrics[("accuracy", "Accuracy")] = {
            "case_name": "Accuracy",
            "metric_type": "accuracy",
            "value": None,
            "baseline": accuracy_metadata["baseline"],
            "threshold_ratio": threshold if threshold is not None and threshold <= 1 else None,
            "threshold_tolerance": threshold if threshold is not None and threshold > 1 else None,
            "reason": accuracy_metadata["reason"],
            "unit": "%",
            "source_step": accuracy_metadata["source_step"],
            "source_excerpt": accuracy_metadata["source_excerpt"],
        }
    return list(metrics.values())


# Version 10 reads benchmark blocks nested under YAML test cases.
_PERFORMANCE_TRENDS_CACHE_VERSION = 10


def _benchmark_config_reference(log_text: str) -> str | None:
    """Find the case YAML path (or filename) emitted by the nightly matrix."""
    paths = re.findall(r"(tests/e2e/[A-Za-z0-9_./-]+\.ya?ml)", log_text, re.I)
    if paths:
        return paths[-1]
    names = re.findall(r"\b([A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml)\b", log_text, re.I)
    return names[-1] if names else None


def _apply_benchmark_config(metrics: list[dict[str, Any]], yaml_content: str, config_path: str, head_sha: str) -> None:
    """Fill successful log metrics from the exact-run benchmark YAML."""
    try:
        document = yaml.safe_load(yaml_content) or {}
    except yaml.YAMLError as exc:
        logger.warning("Unable to parse benchmark YAML %s: %s", config_path, exc)
        return
    benchmark_groups: list[dict[str, Any]] = []

    def collect_benchmark_groups(value: Any) -> None:
        if isinstance(value, dict):
            benchmarks = value.get("benchmarks")
            if isinstance(benchmarks, dict):
                benchmark_groups.append(benchmarks)
            for key, child in value.items():
                if key != "benchmarks":
                    collect_benchmark_groups(child)
        elif isinstance(value, list):
            for child in value:
                collect_benchmark_groups(child)

    collect_benchmark_groups(document)
    if not benchmark_groups:
        return
    for metric in metrics:
        candidates = [
            (name, value)
            for benchmarks in benchmark_groups
            for name, value in benchmarks.items()
            if isinstance(value, dict) and value.get("case_type") == (
                "performance" if metric["metric_type"] == "ott" else "accuracy"
            )
        ]
        if not candidates:
            continue
        # YAMLs can include a zero-baseline perf_warmup case before the real
        # performance gate; it must not become the benchmark baseline.
        candidates.sort(key=lambda candidate: (
            "warmup" in str(candidate[0]).lower(),
            not isinstance(candidate[1].get("baseline"), (int, float)) or candidate[1]["baseline"] == 0,
        ))
        config = candidates[0][1]
        if metric.get("baseline") is None and isinstance(config.get("baseline"), (int, float)):
            metric["baseline"] = float(config["baseline"])
        threshold = config.get("threshold")
        if isinstance(threshold, (int, float)):
            metric["threshold_ratio"] = float(threshold) if threshold <= 1 else None
            metric["threshold_tolerance"] = float(threshold) if threshold > 1 else None
        metric["config_path"] = config_path
        metric["config_head_sha"] = head_sha


def _performance_trends_cache(job: CIJob) -> dict[str, Any] | None:
    """Return a valid immutable-Job trend cache, if one is available."""
    if job.status != "completed":
        return None
    evidence = _json_object(job.data).get("dashboard_evidence")
    if not isinstance(evidence, dict):
        return None
    cache = evidence.get("performance_trends")
    if not isinstance(cache, dict) or cache.get("version") != _PERFORMANCE_TRENDS_CACHE_VERSION:
        return None
    if not isinstance(cache.get("items"), list):
        return None
    return cache


def _with_performance_trend_job_context(job: CIJob, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add live Job metadata to cached or freshly parsed benchmark items."""
    github_job_url = (
        f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
        f"/actions/runs/{job.run_id}/job/{job.job_id}"
    )
    return [
        {
            **item,
            "job_id": job.job_id,
            "run_id": job.run_id,
            "started_at": job.started_at,
            "conclusion": job.conclusion,
            "github_job_url": github_job_url,
        }
        for item in items
    ]


def _save_performance_trends_cache(job: CIJob, items: list[dict[str, Any]], reason: str | None) -> None:
    """Store parsed evidence alongside the existing GitHub Job payload."""
    payload = _json_object(job.data)
    evidence = payload.setdefault("dashboard_evidence", {})
    evidence["performance_trends"] = {
        "version": _PERFORMANCE_TRENDS_CACHE_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "items": items,
        "reason": reason,
    }
    job.data = json.dumps(payload)


def _build_log_root_cause_context(log_text: str, max_chars: int = 14000) -> str:
    """Select causal failure neighborhoods while excluding cleanup noise."""
    lines = log_text.splitlines()
    # Business-level verdicts emitted by aisbench outrank orchestration
    # symptoms and generic exceptions and must survive excerpt clipping.
    verdict_markers = (
        "the following aisbench case failed",
        "performance verification failed",
        "accuracy verification failed",
        "current output token throughput",
        "relative to baseline",
    )
    noise_markers = (
        "upload artifact", "upload-artifact", "no files were found with the provided path",
        "benchmark_results", "cleaning up", "post job cleanup",
        "process completed with exit code", "##[warning]",
    )
    scored_markers = (
        (30, "the following aisbench case failed"),
        (30, "performance verification failed"),
        (30, "accuracy verification failed"),
        (28, "current output token throughput"),
        (28, "relative to baseline"),
        (12, "fail_tag_"), (12, "vector core timeout"),
        (12, "failed to start"), (11, "runtimeerror"),
        (10, "traceback"), (10, "assertionerror"),
        (9, "short test summary"), (9, "fatal"),
        (8, "pytest exit code"), (8, " failed "),
        (6, "exception"), (5, "error:"), (5, "[error]"),
    )
    ranked_hits: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(marker in lowered for marker in noise_markers):
            continue
        score = max((score for score, marker in scored_markers if marker in lowered), default=0)
        if score:
            ranked_hits.append((score, index))

    # Apply a quota per verdict type so a burst of performance failures cannot
    # push a later accuracy failure out of the evidence budget.
    verdict_indexes = sorted({
        index
        for marker in verdict_markers
        for index in [
            candidate for candidate, line in enumerate(lines)
            if marker in line.lower()
        ][:6]
    })
    verdict_selected = set(verdict_indexes)
    verdict_excerpt = "\n".join(
        f"{index + 1}: {lines[index][:3000]}" for index in verdict_indexes
    )
    verdict_budget = min(8000, max_chars * 2 // 3)
    if len(verdict_excerpt) > verdict_budget:
        verdict_excerpt = verdict_excerpt[:verdict_budget] + "\n... [验收失败证据已截断]"

    # Prefer causal signals over generic errors, but preserve their original
    # chronological order so the model can reconstruct the failure boundary.
    hit_indexes = sorted(index for _, index in sorted(
        ranked_hits, key=lambda item: (-item[0], item[1])
    )[:14])
    selected: set[int] = set()
    for index in hit_indexes:
        selected.update(range(max(0, index - 16), min(len(lines), index + 17)))
    if not selected:
        selected.update(range(max(0, len(lines) - 180), len(lines)))
    general_excerpt = "\n".join(
        f"{index + 1}: {lines[index]}"
        for index in sorted(selected - verdict_selected)
    )
    prefix = (
        "=== 高优先级：aisbench 验收失败判定 ===\n" + verdict_excerpt
        + "\n\n=== 其他异常上下文 ===\n"
    ) if verdict_excerpt else ""
    remaining = max_chars - len(prefix)
    if len(general_excerpt) <= remaining:
        return prefix + general_excerpt
    half = max(0, remaining // 2)
    return (
        prefix + general_excerpt[:half]
        + "\n... [中间低优先级证据已省略] ...\n"
        + general_excerpt[-half:]
    )


def _serialize_job_log_summary(record: JobLogSummary) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "run_id": record.run_id,
        "status": record.status,
        "summary": record.summary,
        "log_excerpt": record.log_excerpt,
        "llm_provider": record.llm_provider,
        "llm_model": record.llm_model,
        "generation_time_seconds": record.generation_time_seconds,
        "error_message": record.error_message,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


_NIGHTLY_TEST_CASE_EXPORT_FIELDS = (
    "report_date",
    "source_branch",
    "workflow_name",
    "job_name",
    "display_name",
    "test_model",
    "model_fo",
    "owner",
    "deployment_type",
    "notes",
    "enabled",
)


def _serialize_nightly_test_case_export(record: NightlyTestCase) -> dict[str, Any]:
    """Return the import-compatible JSON shape for one Nightly case.

    Database identifiers and timestamps are intentionally omitted. The result
    contains the same mutable fields accepted by ``NightlyTestCaseCreate`` so
    an exported file can be used as a portable configuration snapshot.
    """
    payload: dict[str, Any] = {}
    for field in _NIGHTLY_TEST_CASE_EXPORT_FIELDS:
        value = getattr(record, field, None)
        if field == "report_date" and value is not None and hasattr(value, "isoformat"):
            value = value.isoformat()
        payload[field] = value
    return payload


def _nightly_export_branch(job: CIJob, run: CIResult) -> str:
    """Use the persisted run branch, with the legacy job-name fallback."""
    if run.branch and run.branch.strip():
        return run.branch.strip()
    match = re.match(r"^\S+\s+\(([^,]+),", job.job_name or "")
    return match.group(1).strip() if match else "main"


def _nightly_snapshot_matches_job(snapshot: NightlyTestCase, job: CIJob) -> bool:
    """Match a YAML snapshot to GitHub's decorated Job name."""
    job_name = job.job_name or ""
    return (
        snapshot.job_name == job_name
        or snapshot.job_name in job_name
        or bool(snapshot.test_model and snapshot.test_model in job_name)
    )


def _format_nightly_export_time(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _format_nightly_ai_analysis(analysis: JobFailureAnalysis | None) -> str:
    if analysis is None or analysis.analysis_status not in {"completed", "reused"}:
        return ""
    parts = []
    if analysis.problem_category:
        parts.append(f"分类：{analysis.problem_category}")
    if analysis.root_cause_summary:
        parts.append(f"根因：{analysis.root_cause_summary}")
    if analysis.improvement_measures_summary:
        parts.append(f"建议：{analysis.improvement_measures_summary}")
    return "\n".join(parts)


def _xlsx_column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _build_nightly_runs_xlsx_legacy(rows: list[dict[str, str]]) -> bytes:
    """Build a small dependency-free XLSX file for Nightly run exports.

    Keeping the writer local avoids adding a production dependency solely for
    one seven-column export.  Values are stored as inline strings so long job
    names and AI summaries keep their original text exactly.
    """
    headers = ["时间", "测试内容", "模型", "覆盖机型", "日志链接", "模型 FO", "AI 分析日志"]
    column_widths = [22, 72, 42, 26, 65, 20, 76]
    hyperlink_relationships: list[tuple[str, str]] = []
    hyperlink_cells: list[tuple[str, str]] = []
    rows_xml: list[str] = []

    def cell(ref: str, value: str, *, style: int = 0) -> str:
        attrs = f' r="{ref}"'
        if style:
            attrs += f' s="{style}"'
        escaped = xml_escape(value or "")
        return f'<c{attrs} t="inlineStr"><is><t xml:space="preserve">{escaped}</t></is></c>'

    header_keys = ("time", "test_content", "model", "coverage", "log_url", "model_fo", "ai_analysis")
    for row_index, values in enumerate([dict(zip(header_keys, headers, strict=True)), *rows], start=1):
        cells = []
        for column_index, key in enumerate(
            header_keys,
            start=1,
        ):
            ref = f"{_xlsx_column_name(column_index)}{row_index}"
            value = str(values.get(key) or "")
            relationship_id = None
            style = 0
            if row_index > 1 and key == "log_url" and value:
                relationship_id = f"rId{len(hyperlink_relationships) + 1}"
                hyperlink_relationships.append((relationship_id, value))
                hyperlink_cells.append((ref, relationship_id))
                style = 1
            cells.append(cell(ref, value, style=style))
        rows_xml.append(f'<row r="{row_index}">' + "".join(cells) + "</row>")

    merge_cells: list[str] = []
    start = 2
    while start <= len(rows) + 1:
        end = start
        timestamp = rows[start - 2].get("time", "")
        while end + 1 <= len(rows) + 1 and rows[end - 1].get("time", "") == timestamp:
            end += 1
        if timestamp and end > start:
            merge_cells.append(f'<mergeCell ref="A{start}:A{end}"/>')
        start = end + 1

    columns_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )
    relationship_parts = []
    for relationship_id, url in hyperlink_relationships:
        escaped_url = xml_escape(url, {'"': "&quot;"})
        relationship_parts.append(
            f'<Relationship Id="{relationship_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{escaped_url}" TargetMode="External"/>'
        )
    relationships_xml = "".join(relationship_parts)
    hyperlinks_xml = "".join(
        f'<hyperlink ref="{ref}" r:id="{relationship_id}"/>'
        for ref, relationship_id in hyperlink_cells
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<cols>{columns_xml}</cols><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" '
        'topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
    )
    if merge_cells:
        sheet_xml += f'<mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>'
    if hyperlinks_xml:
        sheet_xml += f'<hyperlinks>{hyperlinks_xml}</hyperlinks>'
    sheet_xml += '</worksheet>'

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>'''
    root_relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="用例运行明细" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_relationships = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><sz val="11"/><color rgb="FF1677FF"/><u/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>'''

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        if relationships_xml:
            archive.writestr(
                "xl/worksheets/_rels/sheet1.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f"{relationships_xml}</Relationships>",
            )
    return output.getvalue()


def _build_nightly_runs_xlsx(rows: list[dict[str, str]]) -> bytes:
    """Build a standards-compliant XLSX report with openpyxl.

    Do not handcraft SpreadsheetML here: Excel validates more than ZIP/XML
    well-formedness, including relationship and schema ordering details.
    """
    headers = ["时间", "测试内容", "模型", "覆盖机型", "日志链接", "模型 FO", "AI 分析日志"]
    widths = [22, 72, 42, 26, 65, 20, 76]
    keys = ("time", "test_content", "model", "coverage", "log_url", "model_fo", "ai_analysis")

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "用例运行明细"
    worksheet.freeze_panes = "A2"
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="center")

    for row in rows:
        worksheet.append([str(row.get(key) or "") for key in keys])
        row_index = worksheet.max_row
        log_url = str(row.get("log_url") or "")
        if log_url:
            log_cell = worksheet.cell(row=row_index, column=5)
            log_cell.hyperlink = log_url
            log_cell.style = "Hyperlink"
        worksheet.cell(row=row_index, column=7).alignment = Alignment(wrap_text=True, vertical="top")

    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width

    start = 2
    while start <= len(rows) + 1:
        end = start
        timestamp = str(rows[start - 2].get("time") or "")
        while end + 1 <= len(rows) + 1 and str(rows[end - 1].get("time") or "") == timestamp:
            end += 1
        if timestamp and end > start:
            worksheet.merge_cells(start_row=start, start_column=1, end_row=end, end_column=1)
            worksheet.cell(row=start, column=1).alignment = Alignment(vertical="center")
        start = end + 1

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@router.get("/workflows", response_model=list[str])
async def list_workflows(
    db: DbSession
):
    """获取 workflow 列表"""
    stmt = select(CIResult.workflow_name).distinct()
    result = await db.execute(stmt)
    workflows = result.all()
    return [w[0] for w in workflows]


@router.get("/runs", response_model=list[CIResultResponse])
async def list_runs(
    db: DbSession,
    workflow_name: str | None = None,
    status: str | None = None,
    hardware: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = Query(100, ge=1, le=5000)
):
    """获取 CI 运行列表（只返回启用的 workflow 的运行记录）"""
    # 获取启用的 workflow 配置（含时间窗口）
    enabled_stmt = select(
        WorkflowConfig.workflow_name,
        WorkflowConfig.stats_start_hour,
        WorkflowConfig.stats_end_hour,
    ).where(WorkflowConfig.enabled)
    enabled_result = await db.execute(enabled_stmt)
    wf_configs = [(row[0], row[1], row[2]) for row in enabled_result.all()]
    enabled_workflows = [wc[0] for wc in wf_configs]

    # 如果没有启用的 workflow，直接返回空列表
    if not enabled_workflows:
        return []

    wf_filter = build_workflow_time_filter(CIResult, wf_configs)
    stmt = select(CIResult).where(wf_filter)

    if workflow_name:
        stmt = stmt.where(CIResult.workflow_name == workflow_name)
    if status:
        stmt = stmt.where(CIResult.status == status)
    if hardware:
        stmt = stmt.where(CIResult.hardware == hardware)

    # Workflow 列表统一按开始时间筛选和排序。
    run_belonging_time = CIResult.started_at
    if start_time:
        stmt = stmt.where(run_belonging_time >= start_time)
    if end_time:
        stmt = stmt.where(run_belonging_time <= end_time)
    stmt = stmt.order_by(run_belonging_time.desc()).limit(limit)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/workflows/latest", response_model=list[WorkflowLatestResult])
async def get_workflows_latest_results(
    db: DbSession,
    workflow_name: str | None = None,
    hardware: str | None = None
):
    """获取每个 workflow 最近一次的 job 结果（只返回启用的 workflow）"""
    # 获取启用的 workflow 名称列表
    enabled_stmt = select(WorkflowConfig).where(WorkflowConfig.enabled)
    if workflow_name:
        enabled_stmt = enabled_stmt.where(WorkflowConfig.workflow_name == workflow_name)
    if hardware:
        enabled_stmt = enabled_stmt.where(WorkflowConfig.hardware == hardware)

    enabled_result = await db.execute(enabled_stmt)
    enabled_workflows = enabled_result.scalars().all()

    # 如果没有启用的 workflow，直接返回空列表
    if not enabled_workflows:
        return []

    results = []
    for config in enabled_workflows:
        wf_name, wf_hardware = config.workflow_name, config.hardware
        # 获取每个 workflow 最近的运行记录（按 completed_at 降序，取最新的）
        stmt = select(CIResult).where(
            build_official_ci_filter(CIResult, [config])
        ).order_by(
            CIResult.completed_at.desc()
        ).limit(1)

        result = await db.execute(stmt)
        latest_run = result.scalar_one_or_none()

        latest_run_data = None
        if latest_run:
            latest_run_data = {
                "run_id": latest_run.run_id,
                "status": latest_run.status,
                "conclusion": latest_run.conclusion,
                "started_at": latest_run.started_at.isoformat() if latest_run.started_at else None,
                "duration_seconds": latest_run.duration_seconds,
                "github_html_url": f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{latest_run.run_id}" if latest_run.run_id else None,
            }

        results.append(WorkflowLatestResult(
            workflow_name=wf_name,
            hardware=wf_hardware,
            latest_run=latest_run_data,
        ))

    return results


@router.get("/stats", response_model=CIStats)
async def get_ci_stats(
    db: DbSession,
    workflow_name: str | None = None,
    hardware: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    """获取 CI 统计数据（只统计启用的 workflow）"""
    # 获取启用的 workflow 配置（含时间窗口）
    enabled_stmt = select(
        WorkflowConfig.workflow_name,
        WorkflowConfig.stats_start_hour,
        WorkflowConfig.stats_end_hour,
    ).where(WorkflowConfig.enabled)
    enabled_result = await db.execute(enabled_stmt)
    wf_configs = [(row[0], row[1], row[2]) for row in enabled_result.all()]

    if not wf_configs:
        return {"total_runs": 0, "passed_runs": 0, "failed_runs": 0,
                "other_runs": 0, "success_rate": 0.0, "avg_duration_seconds": None,
                "last_7_days": {"runs": 0, "success_rate": 0.0, "avg_duration_seconds": None}}

    wf_filter = build_workflow_time_filter(CIResult, wf_configs)

    # All aggregates use the same conditions.  Keeping them in one list avoids
    # silently dropping one filter when workflow_name and hardware are combined.
    base_conditions = [wf_filter]
    if workflow_name:
        base_conditions.append(CIResult.workflow_name == workflow_name)
    if hardware:
        base_conditions.append(CIResult.hardware == hardware)
    run_belonging_time = CIResult.started_at
    if start_time:
        base_conditions.append(run_belonging_time >= start_time)
    if end_time:
        base_conditions.append(run_belonging_time <= end_time)

    totals_stmt = select(
        func.count().label("total_runs"),
        func.sum(case((CIResult.conclusion == "success", 1), else_=0)).label("passed_runs"),
        func.sum(case((CIResult.conclusion == "failure", 1), else_=0)).label("failed_runs"),
        func.avg(CIResult.duration_seconds).label("avg_duration"),
    ).where(*base_conditions)
    totals = (await db.execute(totals_stmt)).one()
    total_runs = int(totals.total_runs or 0)
    passed_runs = int(totals.passed_runs or 0)
    failed_runs = int(totals.failed_runs or 0)
    other_runs = total_runs - passed_runs - failed_runs

    # 成功率
    success_rate = (passed_runs / total_runs * 100) if total_runs > 0 else 0.0
    avg_duration = totals.avg_duration
    avg_duration_seconds = float(avg_duration) if avg_duration else None

    # 最近 7 天统计（使用 completed_at 而不是 created_at）
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)
    recent_stmt = select(
        func.count().label("runs"),
        func.sum(case((CIResult.conclusion == "success", 1), else_=0)).label("success_runs"),
        func.avg(CIResult.duration_seconds).label("avg_duration"),
    ).where(CIResult.started_at >= seven_days_ago, *base_conditions)
    recent = (await db.execute(recent_stmt)).one()
    last_7_days_runs = int(recent.runs or 0)
    last_7_days_success = int(recent.success_runs or 0)
    last_7_days_success_rate = (last_7_days_success / last_7_days_runs * 100) if last_7_days_runs > 0 else 0.0
    last_7_days_avg_duration = recent.avg_duration
    last_7_days_avg_duration_seconds = float(last_7_days_avg_duration) if last_7_days_avg_duration else None

    return {
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "other_runs": other_runs,
        "success_rate": round(success_rate, 2),
        "avg_duration_seconds": avg_duration_seconds,
        "last_7_days": {
            "runs": last_7_days_runs,
            "success_rate": round(last_7_days_success_rate, 2),
            "avg_duration_seconds": last_7_days_avg_duration_seconds,
        }
    }


@router.get("/trends", response_model=list[CITrend])
async def get_ci_trends(
    db: DbSession,
    days: int = Query(7, ge=1, le=30, description="获取多少天的趋势数据"),
    workflow_name: str | None = None,
    hardware: str | None = None,
):
    """
    获取 CI 趋势数据（只统计启用的 workflow）

    按天统计运行次数、成功率等指标
    """
    # 计算起始时间
    end_date = datetime.now(UTC)
    start_date = end_date - timedelta(days=days)

    # 获取启用的 workflow 配置（含时间窗口）
    enabled_stmt = select(
        WorkflowConfig.workflow_name,
        WorkflowConfig.stats_start_hour,
        WorkflowConfig.stats_end_hour,
    ).where(WorkflowConfig.enabled)
    enabled_result = await db.execute(enabled_stmt)
    wf_configs = [(row[0], row[1], row[2]) for row in enabled_result.all()]
    enabled_workflows = [wc[0] for wc in wf_configs]

    # 如果没有启用的 workflow，直接返回空列表
    if not enabled_workflows:
        return []

    wf_filter = build_workflow_time_filter(CIResult, wf_configs)

    # 所属日期按 Workflow 结束时间计算，并转换为北京时间日期；运行中的
    # Workflow 暂无结束时间时回退到开始时间。
    run_belonging_time = func.coalesce(CIResult.completed_at, CIResult.started_at)
    run_belonging_date = func.date(
        func.date_add(run_belonging_time, text("INTERVAL 8 HOUR"))
    )

    # 构建基础查询
    stmt = select(
        run_belonging_date.label('date'),
        func.count().label('total_runs'),
        func.sum(
            case(
                (CIResult.conclusion == "success", 1),
                else_=0
            )
        ).label('success_runs'),
        func.avg(
            case(
                (CIResult.duration_seconds.isnot(None), CIResult.duration_seconds),
                else_=None
            )
        ).label('avg_duration'),
        func.max(
            case(
                (CIResult.duration_seconds.isnot(None), CIResult.duration_seconds),
                else_=None
            )
        ).label('max_duration'),
    ).where(
        run_belonging_time >= start_date,
        run_belonging_time <= end_date,
        wf_filter,
    )

    if workflow_name:
        stmt = stmt.where(CIResult.workflow_name == workflow_name)
    if hardware:
        stmt = stmt.where(CIResult.hardware == hardware)

    stmt = stmt.group_by(run_belonging_date).order_by(run_belonging_date)

    result = await db.execute(stmt)
    rows = result.all()

    # 转换为响应格式
    trends = []
    for row in rows:
        trends.append(CITrend(
            date=str(row.date),
            total_runs=row.total_runs,
            success_runs=row.success_runs,
            success_rate=round(row.success_runs / row.total_runs * 100, 2) if row.total_runs > 0 else 0.0,
            avg_duration_seconds=float(row.avg_duration) if row.avg_duration else None,
            max_duration_seconds=float(row.max_duration) if row.max_duration else None,
        ))

    return trends


@router.post("/sync", response_model=CISyncResponse)
async def trigger_sync(
    current_user: CurrentSuperAdminUser,
    days_back: int = Query(default=7, ge=1, le=90),
    max_runs_per_workflow: int = Query(default=100, ge=1, le=1000),
    force_full_refresh: bool = Query(default=False),
):
    """Enqueue a durable CI sync task for a Collector worker."""
    from infrastructure.db.base import SessionLocal
    from infrastructure.tasks.task_manager import TaskManager

    async with SessionLocal() as db:
        task_id = await TaskManager.create_task(
            db,
            "ci_sync",
            {
                "days_back": days_back,
                "max_runs": max_runs_per_workflow,
                "force_full_refresh": force_full_refresh,
            },
            "ci_sync:active",
            required_capability="python",
            priority=10,
        )
        await db.commit()

    if task_id is None:
        return CISyncResponse(success=False, message="An equivalent CI sync task is already queued", collected_count=0)
    return CISyncResponse(
        success=True,
        message=f"CI sync task {task_id} queued; check progress via the task status API",
        collected_count=0,
    )


@router.get("/sync/status")
async def get_sync_status():
    """Return the scheduler heartbeat in the shape expected by the CI UI."""
    from datetime import timedelta

    from infrastructure.db.base import SessionLocal
    from infrastructure.persistence.models import SchedulerHeartbeat

    async with SessionLocal() as db:
        heartbeat = await db.get(SchedulerHeartbeat, 1)

    now = datetime.now(UTC)
    running = False
    jobs: list[dict[str, Any]] = []
    if heartbeat is not None:
        updated_at = heartbeat.updated_at
        if updated_at is not None and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        running = bool(heartbeat.running) and updated_at is not None and now - updated_at < timedelta(seconds=90)
        for job_id, payload in (heartbeat.jobs or {}).items():
            if not isinstance(payload, dict):
                payload = {}
            jobs.append(
                {
                    "id": str(job_id),
                    "name": payload.get("name") or str(job_id),
                    "next_run_time": payload.get("next_run"),
                }
            )

    return {
        "scheduler_running": running,
        "jobs": sorted(jobs, key=lambda job: job["id"]),
        "error": None if running else "scheduler heartbeat unavailable",
    }


@router.get("/sync/progress")
async def get_sync_progress_info():
    """Return the latest durable CI sync task status."""
    from sqlalchemy import text

    from infrastructure.db.base import SessionLocal

    async with SessionLocal() as db:
        row = (await db.execute(text("""
            SELECT
                id,
                status,
                checkpoint_data,
                last_error,
                created_at,
                CASE
                    WHEN status IN ('completed', 'failed', 'dead') THEN updated_at
                    ELSE NULL
                END AS completed_at
            FROM collection_tasks
            WHERE task_type = 'ci_sync'
            ORDER BY id DESC
            LIMIT 1
        """))).mappings().first()
    if not row:
        return {"status": "idle", "task_id": None, "error_message": None}
    checkpoint = row["checkpoint_data"] or {}
    if isinstance(checkpoint, str):
        try:
            checkpoint = json.loads(checkpoint)
        except json.JSONDecodeError:
            checkpoint = {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    return {
        "status": row["status"],
        "task_id": row["id"],
        "checkpoint": checkpoint,
        "progress_percentage": checkpoint.get("progress_percentage", 0),
        "total_workflows": checkpoint.get("total_workflows", 0),
        "completed_workflows": checkpoint.get("completed_workflows", 0),
        "current_workflow": checkpoint.get("current_workflow"),
        "total_collected": checkpoint.get("total_collected", 0),
        "workflow_details": checkpoint.get("workflow_details", {}),
        "error_message": row["last_error"] or checkpoint.get("error_message"),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


@router.get("/debug/workflows")
async def debug_workflows():
    """
    调试接口：获取 GitHub 上实际的 workflow 列表

    用于排查 workflow 文件名不匹配问题
    """
    try:
        from infrastructure.clients.github_client import GitHubClient
        from infrastructure.core.config import settings

        if not settings.GITHUB_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GITHUB_TOKEN not configured",
            )

        client = GitHubClient(
            token=settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        )

        try:
            # 获取所有 workflows
            url = f"/repos/{client.owner}/{client.repo}/actions/workflows"
            result = await client._request("GET", url)
            workflows = result.get("workflows", [])

            # 检查配置的 workflow 是否存在
            from sqlalchemy import select

            from infrastructure.db.base import SessionLocal
            from infrastructure.persistence.models import WorkflowConfig

            async with SessionLocal() as db:
                configured_workflows = list(
                    (
                        await db.execute(
                            select(WorkflowConfig.workflow_file).where(
                                WorkflowConfig.enabled
                            )
                        )
                    ).scalars()
                )

            workflow_names = [w["path"].split("/")[-1] for w in workflows]

            return {
                "configured_workflows": configured_workflows,
                "github_workflows": workflow_names,
                "match_status": {
                    name: name in workflow_names
                    for name in configured_workflows
                },
                "total_workflows": len(workflows),
            }
        finally:
            await client.close()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Debug failed: {str(e)}",
        )


@router.get("/runs/{run_id}/jobs", response_model=list[CIJobResponse])
async def list_jobs_by_run(
    run_id: int,
    db: DbSession
):
    """获取指定 workflow run 的所有 jobs"""
    run_result = await db.execute(select(CIResult).where(CIResult.run_id == run_id))
    run = run_result.scalar_one_or_none()
    stmt = select(CIJob).where(CIJob.run_id == run_id).order_by(CIJob.id)
    result = await db.execute(stmt)
    jobs = result.scalars().all()
    current_attempt = extract_run_attempt(run.data) if run else None
    jobs = filter_current_run_attempt(jobs, current_attempt)

    response = []
    for job in jobs:
        # 解析 steps_summary 和 runner_labels
        steps_summary = []
        if job.steps_data:
            try:
                steps_summary = json.loads(job.steps_data)
            except Exception:
                pass

        runner_labels = []
        if job.runner_labels:
            try:
                runner_labels = json.loads(job.runner_labels)
            except Exception:
                pass

        response.append(CIJobResponse(
            id=job.id,
            job_id=job.job_id,
            run_id=job.run_id,
            workflow_name=job.workflow_name,
            job_name=job.job_name,
            status=job.status,
            conclusion=job.conclusion,
            hardware=job.hardware,
            runner_name=job.runner_name,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=job.duration_seconds,
            runner_labels=runner_labels,
            steps_summary=steps_summary,
            created_at=job.created_at,
        ))

    return response


@router.get("/jobs", response_model=list[CIJobResponse])
async def list_jobs(
    db: DbSession,
    days: int = Query(30, ge=1, le=90),
    workflow_name: str | None = None,
    search: str | None = Query(None, min_length=1, max_length=200),
    job_status: str | None = None,
    conclusion: str | None = None,
    hardware: str | None = None,
    limit: int = Query(500, ge=1, le=1000),
):
    """获取最近的 Job 运行记录，供 Workflow 运行看板直接平铺展示。"""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = select(CIJob).where(func.coalesce(CIJob.started_at, CIJob.created_at) >= cutoff)

    if workflow_name:
        stmt = stmt.where(CIJob.workflow_name == workflow_name)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                CIJob.workflow_name.ilike(pattern),
                CIJob.job_name.ilike(pattern),
                CIJob.runner_name.ilike(pattern),
            )
        )
    if job_status:
        stmt = stmt.where(CIJob.status == job_status)
    if conclusion:
        stmt = stmt.where(CIJob.conclusion == conclusion)
    if hardware:
        stmt = stmt.where(CIJob.hardware == hardware)

    jobs = (await db.execute(
        stmt.order_by(CIJob.started_at.desc(), CIJob.id.desc()).limit(limit)
    )).scalars().all()

    # Keep the flat Job view consistent with the per-run view. A re-run uses
    # the same workflow run ID, so rows from an earlier attempt must not leak
    # into the current result set.
    run_ids = {job.run_id for job in jobs if job.run_id is not None}
    if run_ids:
        run_result = await db.execute(
            select(CIResult.run_id, CIResult.data).where(CIResult.run_id.in_(run_ids))
        )
        run_attempts = {
            run_id: extract_run_attempt(run_data)
            for run_id, run_data in run_result.all()
        }
        jobs = [
            job
            for job in jobs
            if is_current_run_attempt(job.data, run_attempts.get(job.run_id))
        ]

    response = []
    for job in jobs:
        steps_summary = []
        if job.steps_data:
            try:
                steps_summary = json.loads(job.steps_data)
            except Exception:
                pass

        runner_labels = []
        if job.runner_labels:
            try:
                runner_labels = json.loads(job.runner_labels)
            except Exception:
                pass

        response.append(CIJobResponse(
            id=job.id,
            job_id=job.job_id,
            run_id=job.run_id,
            workflow_name=job.workflow_name,
            job_name=job.job_name,
            status=job.status,
            conclusion=job.conclusion,
            hardware=job.hardware,
            runner_name=job.runner_name,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_seconds=job.duration_seconds,
            runner_labels=runner_labels,
            steps_summary=steps_summary,
            created_at=job.created_at,
        ))

    return response


@router.get("/runs/{run_id}/version-snapshot")
async def get_run_version_snapshot(run_id: int, db: DbSession):
    result = await db.execute(select(CIResult).where(CIResult.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return get_version_snapshot(run.data)


@router.post("/runs/{run_id}/version-snapshot/refresh")
async def refresh_run_version_snapshot(run_id: int, current_user: CurrentAdminUser, db: DbSession):
    run_result = await db.execute(select(CIResult).where(CIResult.run_id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    jobs_result = await db.execute(select(CIJob).where(CIJob.run_id == run_id))
    candidates: list[tuple[CIJob, str]] = []
    for job in jobs_result.scalars().all():
        steps = _json_list(job.steps_data)
        source_step = version_snapshot_step_name(steps)
        if source_step:
            candidates.append((job, source_step))
    if not candidates:
        raise HTTPException(status_code=404, detail="No Job with a version-reporting step")
    if not settings.GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GitHub Token is not configured")

    from infrastructure.clients.github_client import GitHubClient
    async with GitHubClient(
        token=settings.GITHUB_TOKEN,
        owner=settings.GITHUB_OWNER,
        repo=settings.GITHUB_REPO,
    ) as client:
        for job, source_step in candidates:
            try:
                snapshot = parse_ci_version_snapshot(
                    await client.get_job_logs(job.job_id),
                    source_job_id=job.job_id,
                    source_step=source_step,
                )
            except Exception as exc:
                logger.warning("Version snapshot refresh failed for job %s: %s", job.job_id, exc)
                continue
            if snapshot:
                snapshot["collected_at"] = datetime.now(UTC).isoformat()
                payload = _json_object(run.data)
                payload.setdefault("dashboard_evidence", {})["version_snapshot"] = snapshot
                run.data = json.dumps(payload)
                await db.commit()
                return snapshot
    raise HTTPException(status_code=422, detail="Version information was not found in available version-reporting logs")


@router.get("/job-performance-trends")
async def get_job_performance_trends(
    db: DbSession,
    job_ids: list[int] = Query(..., min_length=1, max_length=30),
):
    """Read benchmark metrics from a Job cache, then GitHub logs on cache miss."""
    unique_job_ids = list(dict.fromkeys(job_ids))
    result = await db.execute(
        select(CIJob, CIResult)
        .outerjoin(CIResult, CIResult.run_id == CIJob.run_id)
        .where(CIJob.job_id.in_(unique_job_ids))
    )
    job_rows = result.all()
    jobs_by_id = {job.job_id: job for job, _ in job_rows}
    runs_by_job = {job.job_id: run for job, run in job_rows}
    results: list[dict[str, Any]] = []
    cache_misses: list[tuple[CIJob, CIResult | None]] = []
    for job_id in unique_job_ids:
        job = jobs_by_id.get(job_id)
        if job is None:
            results.append({"job_id": job_id, "items": [], "reason": "Job not found"})
            continue
        cache = _performance_trends_cache(job)
        if cache is not None:
            results.append({
                "job_id": job_id,
                "items": _with_performance_trend_job_context(job, cache["items"]),
                "reason": cache.get("reason"),
            })
            continue
        cache_misses.append((job, runs_by_job.get(job.job_id)))

    if not cache_misses:
        return {
            "items": [item for parsed in results for item in parsed["items"]],
            "unavailable": [
                {"job_id": parsed["job_id"], "reason": parsed["reason"]}
                for parsed in results if parsed["reason"]
            ],
        }
    if not settings.GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GitHub Token is not configured")

    from infrastructure.clients.github_client import GitHubClient
    semaphore = asyncio.Semaphore(3)

    async def parse_one(job: CIJob, run: CIResult | None) -> dict[str, Any]:
        try:
            async with semaphore:
                async with GitHubClient(
                    token=settings.GITHUB_TOKEN,
                    owner=settings.GITHUB_OWNER,
                    repo=settings.GITHUB_REPO,
                ) as client:
                    log_text = await client.get_job_logs(job.job_id)
            items = _parse_benchmark_metrics(log_text)
            # Successful raw log downloads can omit the matrix/YAML line even
            # though CIJob.name preserves the filename selected by the matrix.
            config_reference = _benchmark_config_reference(log_text) or _benchmark_config_reference(job.job_name)
            if items and config_reference and run and run.head_sha:
                try:
                    async with GitHubClient(
                        token=settings.GITHUB_TOKEN,
                        owner=settings.GITHUB_OWNER,
                        repo=settings.GITHUB_REPO,
                    ) as client:
                        config_path = config_reference
                        if not config_path.startswith("tests/"):
                            candidates = await client.find_repository_paths_by_filename(
                                config_reference, run.head_sha
                            )
                            config_path = next(
                                (path for path in candidates if "/configs/" in path),
                                candidates[0] if candidates else "",
                            )
                        if config_path:
                            _apply_benchmark_config(
                                items,
                                await client.get_repository_file_content(config_path, run.head_sha),
                                config_path,
                                run.head_sha,
                            )
                except Exception as exc:
                    logger.warning(
                        "Failed to read benchmark YAML %s at %s for job %s: %s",
                        config_reference, run.head_sha, job.job_id, exc,
                    )
            reason = None if items else "No benchmark metric found in Run Pytest or Stream logs"
            if job.status == "completed":
                _save_performance_trends_cache(job, items, reason)
            return {
                "job_id": job.job_id,
                "items": _with_performance_trend_job_context(job, items),
                "reason": reason,
            }
        except Exception as exc:
            logger.warning("Failed to read benchmark metrics for job %s: %s", job.job_id, exc)
            return {"job_id": job.job_id, "items": [], "reason": "GitHub logs unavailable or expired"}

    results.extend(await asyncio.gather(*(parse_one(job, run) for job, run in cache_misses)))
    await db.commit()
    return {
        "items": [item for parsed in results for item in parsed["items"]],
        "unavailable": [
            {"job_id": parsed["job_id"], "reason": parsed["reason"]}
            for parsed in results if parsed["reason"]
        ],
    }


@router.get("/job-comparison")
async def compare_ci_jobs(
    db: DbSession,
    start_job_id: int = Query(..., gt=0),
    end_job_id: int = Query(..., gt=0),
):
    """Build a lightweight, evidence-sourced comparison for two Job runs."""
    jobs_result = await db.execute(
        select(CIJob).where(CIJob.job_id.in_([start_job_id, end_job_id]))
    )
    jobs_by_id = {item.job_id: item for item in jobs_result.scalars().all()}
    start_job = jobs_by_id.get(start_job_id)
    end_job = jobs_by_id.get(end_job_id)
    if not start_job or not end_job:
        raise HTTPException(status_code=404, detail="Start or End Job not found")
    if start_job.workflow_name != end_job.workflow_name or start_job.job_name != end_job.job_name:
        raise HTTPException(status_code=400, detail="Start and End must represent the same Workflow Job")
    if start_job.started_at and end_job.started_at and start_job.started_at >= end_job.started_at:
        raise HTTPException(status_code=400, detail="Start must be earlier than End")

    runs_result = await db.execute(
        select(CIResult).where(CIResult.run_id.in_([start_job.run_id, end_job.run_id]))
    )
    runs_by_id = {item.run_id: item for item in runs_result.scalars().all()}
    start_run = runs_by_id.get(start_job.run_id)
    end_run = runs_by_id.get(end_job.run_id)

    def snapshot(job: CIJob, run: CIResult | None) -> dict[str, Any]:
        job_data = _json_object(job.data)
        run_data = _json_object(run.data) if run else {}
        version = get_version_snapshot(run_data)
        metadata = {"job": job_data, "run": run_data, "version_snapshot": version}
        return {
            "job_id": job.job_id,
            "run_id": job.run_id,
            "started_at": job.started_at,
            "conclusion": job.conclusion,
            "duration_seconds": job.duration_seconds,
            "workflow_head_sha": run.head_sha if run else None,
            "vllm_ascend_commit": version.get("vllm_ascend_commit"),
            "vllm_ascend_commit_date": version.get("vllm_ascend_commit_date"),
            "vllm_ascend_commit_message": version.get("vllm_ascend_commit_message"),
            "vllm_commit": version.get("vllm_commit"),
            "vllm_version": version.get("vllm_version"),
            "version_evidence_status": version.get("status") or "unknown",
            "branch": run.branch if run else None,
            "event": run.event if run else None,
            "hardware": job.hardware,
            "runner_name": job.runner_name,
            "runner_labels": _json_list(job.runner_labels),
            "steps": _json_list(job.steps_data),
            "metadata": metadata,
        }

    start = snapshot(start_job, start_run)
    end = snapshot(end_job, end_run)
    version_aliases = {
        "vllm_ascend": {"vllm_ascend_version", "vllm_ascend_commit", "vllm_ascend_sha"},
        "vLLM": {"vllm_version", "vllm_commit", "vllm_sha"},
        "torch": {"torch_version"},
        "torch_npu": {"torch_npu_version"},
        "CANN": {"cann_version", "ascend_toolkit_version"},
        "image": {"image", "image_tag", "image_digest", "container_image"},
    }

    version_differences = []
    for label, aliases in version_aliases.items():
        start_value = _find_metadata_value(start["metadata"], aliases)
        end_value = _find_metadata_value(end["metadata"], aliases)
        version_differences.append({
            "field": label,
            "start": start_value,
            "end": end_value,
            "changed": start_value != end_value if start_value is not None and end_value is not None else None,
            "availability": "available" if start_value is not None and end_value is not None else "unknown",
            "source": "ci_results.data / ci_jobs.data",
        })

    config_fields = {
        "hardware": (start["hardware"], end["hardware"]),
        "runner": (start["runner_name"], end["runner_name"]),
        "runner_labels": (start["runner_labels"], end["runner_labels"]),
        "branch": (start["branch"], end["branch"]),
        "event": (start["event"], end["event"]),
    }
    configuration_differences = [
        {"field": field, "start": values[0], "end": values[1], "changed": values[0] != values[1], "source": "ci_jobs / ci_results"}
        for field, values in config_fields.items()
    ]

    compare_url = None
    compared_commits: list[dict[str, Any]] = []
    reverted_prs: list[dict[str, Any]] = []
    revert_prs: list[dict[str, Any]] = []
    revert_commits: list[dict[str, Any]] = []
    time_filtered_pr_count = 0
    compare_error = None
    if start.get("vllm_ascend_commit") and end.get("vllm_ascend_commit"):
        compare_url = (
            f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/compare/"
            f"{start['vllm_ascend_commit']}...{end['vllm_ascend_commit']}"
        )
        if settings.GITHUB_TOKEN:
            try:
                from infrastructure.clients.github_client import GitHubClient

                async with GitHubClient(
                    token=settings.GITHUB_TOKEN,
                    owner=settings.GITHUB_OWNER,
                    repo=settings.GITHUB_REPO,
                ) as client:
                    compare = await client.get_compare_commits(
                        start["vllm_ascend_commit"], end["vllm_ascend_commit"]
                    )
                    commits = compare.get("commits", [])
                    sha_to_pr: dict[str, int] = {}
                    candidates: dict[int, dict[str, Any]] = {}
                    for commit in commits:
                        message = commit.get("commit", {}).get("message", "")
                        title = message.splitlines()[0] if message else ""
                        pr_number = _extract_merged_pr_number(title)
                        if pr_number is None:
                            continue
                        sha = commit.get("sha", "")
                        sha_to_pr[sha] = pr_number
                        candidates.setdefault(pr_number, {
                            "number": pr_number,
                            "sha": sha,
                            "title": title,
                            "author": (commit.get("author") or {}).get("login")
                                or commit.get("commit", {}).get("author", {}).get("name"),
                            "url": f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/pull/{pr_number}",
                            "commit_url": commit.get("html_url"),
                            "matched_files": [],
                        })

                    for commit in commits:
                        message = commit.get("commit", {}).get("message", "")
                        title = message.splitlines()[0] if message else ""
                        current_pr = _extract_merged_pr_number(title)
                        target_pr, target_sha = _extract_revert_target(
                            message, current_pr, sha_to_pr
                        )
                        if target_pr is None:
                            continue
                        revert_commits.append({
                            "sha": commit.get("sha"),
                            "target_sha": target_sha,
                            "revert_pr": current_pr,
                            "reverted_pr": target_pr,
                            "title": title,
                            "url": commit.get("html_url"),
                        })

                    detail_results = await _fetch_pr_details_bounded(
                        client,
                        settings.GITHUB_OWNER,
                        settings.GITHUB_REPO,
                        list(candidates),
                        max_concurrency=4,
                    )
                    detail_errors: list[str] = []
                    in_window_pr_numbers: set[int] = set()
                    for pr_number, detail, detail_error in detail_results:
                        item = candidates[pr_number]
                        if detail_error or detail is None:
                            detail_errors.append(f"PR #{pr_number}: {detail_error or 'empty response'}")
                            continue
                        item["merged_at"] = detail.get("merged_at")
                        item["title"] = detail.get("title") or item["title"]
                        item["author"] = (detail.get("user") or {}).get("login") or item["author"]
                        # Only merged PRs qualify. Explicitly reverted PRs remain
                        # visible as evidence but are excluded from the net set.
                        if not item["merged_at"]:
                            continue
                        if _merged_within_commit_boundary(
                            item["merged_at"],
                            start.get("vllm_ascend_commit_date"),
                            end.get("vllm_ascend_commit_date"),
                        ):
                            in_window_pr_numbers.add(pr_number)
                        else:
                            time_filtered_pr_count += 1

                    cancelled_original_prs: set[int] = set()
                    cancelled_revert_prs: set[int] = set()
                    for relation in revert_commits:
                        original_in_window = relation["reverted_pr"] in in_window_pr_numbers
                        revert_in_window = relation.get("revert_pr") in in_window_pr_numbers
                        relation["original_in_window"] = original_in_window
                        relation["revert_in_window"] = revert_in_window
                        relation["cancellation_status"] = (
                            "cancelled" if original_in_window and revert_in_window else "not_cancelled"
                        )
                        if original_in_window and revert_in_window:
                            cancelled_original_prs.add(relation["reverted_pr"])
                            cancelled_revert_prs.add(relation["revert_pr"])

                    for pr_number, item in candidates.items():
                        if pr_number not in in_window_pr_numbers:
                            continue
                        if pr_number in cancelled_original_prs:
                            item["net_status"] = "fully_reverted"
                            reverted_prs.append(item)
                        elif pr_number in cancelled_revert_prs:
                            item["net_status"] = "revert_commit"
                            revert_prs.append(item)
                        else:
                            item["net_status"] = "effective"
                            compared_commits.append(item)
                    if detail_errors:
                        compare_error = "；".join(detail_errors)
            except Exception as exc:
                logger.warning("Failed to compare selected CI boundary SHAs: %s", exc)
                compare_error = str(exc)
        else:
            compare_error = "GitHub Token 未配置"

    return {
        "start": {key: value for key, value in start.items() if key not in {"metadata", "steps"}},
        "end": {key: value for key, value in end.items() if key not in {"metadata", "steps"}},
        "pr_changes": {
            "source": "GitHub compare over the actual vllm-ascend commit range",
            "precision": "commit_range",
            "compare_url": compare_url,
            "error": compare_error,
            "items": compared_commits,
            "effective_prs": compared_commits,
            "reverted_prs": reverted_prs,
            "revert_prs": revert_prs,
            "revert_commits": revert_commits,
            "partial": bool(compare_error),
        },
        "version_differences": version_differences,
        "failure_stage_difference": {
            "start": _failure_stage(start["steps"]),
            "end": _failure_stage(end["steps"]),
        },
        "test_difference": {
            "status": "unknown",
            "summary": "当前仅确认两端 Job 名相同；尚无完整 Case/测试集合快照，不能宣称无 Test 差异。",
            "source": "workflow_name + job_name identity",
        },
        "configuration_differences": configuration_differences,
        "warnings": [
            "展示 Start/End 实际 vllm-ascend commit 区间内全部可识别的合入 PR。",
            f"已按 PR merged_at 收拢到版本提交时间窗，排除 {time_filtered_pr_count} 个窗外 PR。",
            "区间 PR 是变更全集，不等于每个 PR 都是当前故障嫌疑。",
        ],
    }


@router.get("/jobs/{job_id}/failure-summary")
async def get_job_failure_summary(
    job_id: int,
    db: DbSession,
):
    """Fetch a compact failure excerpt lazily for one timeline node."""
    result = await db.execute(select(CIJob).where(CIJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    stage = _failure_stage(_json_list(job.steps_data))
    fallback = stage["first_failed_step"]
    if job.conclusion == "success":
        return {"summary": None, "failed_step": None, "source": "success"}
    if not settings.GITHUB_TOKEN:
        return {"summary": fallback, "failed_step": fallback, "source": "steps", "reason": "GitHub Token 未配置"}

    try:
        from infrastructure.clients.github_client import GitHubClient

        async with GitHubClient(
            token=settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        ) as client:
            log_text = await client.get_job_logs(job.job_id)
        excerpt = _extract_log_error_summary(log_text)
        return {
            "summary": excerpt or fallback,
            "failed_step": fallback,
            "source": "github_log" if excerpt else "steps",
            "reason": None if excerpt else "日志中未识别到简短错误行",
        }
    except Exception as exc:
        logger.warning("Failed to fetch log summary for job %s: %s", job_id, exc)
        return {
            "summary": fallback,
            "failed_step": fallback,
            "source": "steps",
            "reason": "GitHub 日志不可用或已过期",
        }


@router.get("/jobs/{job_id}/log-root-cause")
async def get_job_log_root_cause(job_id: int, db: DbSession):
    """Return the permanently stored lightweight log summary, if generated."""
    result = await db.execute(select(JobLogSummary).where(JobLogSummary.job_id == job_id))
    record = result.scalar_one_or_none()
    return _serialize_job_log_summary(record) if record else None


@router.post("/jobs/{job_id}/log-root-cause")
async def create_job_log_root_cause(
    job_id: int,
    current_user: CurrentAdminUser,
    db: DbSession,
    regenerate: bool = Query(False, description="Regenerate an existing completed summary"),
):
    """Download logs once, ask one LLM for root-cause summary only, and persist it."""
    existing_result = await db.execute(
        select(JobLogSummary).where(JobLogSummary.job_id == job_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing and existing.status == "completed" and not regenerate:
        return _serialize_job_log_summary(existing)
    if existing and existing.status == "analyzing":
        updated_at = existing.updated_at
        if updated_at and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        # Browser timeouts or container restarts can strand this marker.
        # Keep a short concurrency guard, then permit an explicit retry.
        if updated_at and datetime.now(UTC) - updated_at < timedelta(minutes=5):
            return _serialize_job_log_summary(existing)

    job_result = await db.execute(select(CIJob).where(CIJob.job_id == job_id))
    job = job_result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.conclusion == "success":
        raise HTTPException(status_code=400, detail="成功 Job 不需要根因摘要")

    record = existing or JobLogSummary(job_id=job.job_id, run_id=job.run_id)
    record.status = "analyzing"
    record.error_message = None
    if not existing:
        db.add(record)
    await db.commit()
    await db.refresh(record)

    try:
        from infrastructure.clients.github_client import GitHubClient
        from infrastructure.clients.llm_client import LLMClient
        from infrastructure.persistence.models.daily_summary import LLMProviderConfig

        if not settings.GITHUB_TOKEN:
            raise RuntimeError("GitHub Token 未配置")
        async with GitHubClient(
            token=settings.GITHUB_TOKEN,
            owner=settings.GITHUB_OWNER,
            repo=settings.GITHUB_REPO,
        ) as client:
            log_text = await client.get_job_logs(job.job_id)
        if not log_text:
            raise RuntimeError("GitHub 日志不可用或已过期")
        excerpt = _build_log_root_cause_context(log_text)

        config_result = await db.execute(
            select(LLMProviderConfig).where(LLMProviderConfig.is_active).limit(1)
        )
        config = config_result.scalar_one_or_none()
        if not config:
            raise RuntimeError("没有已激活的模型提供商")

        step_stage = _failure_stage(_json_list(job.steps_data))
        llm_result = await LLMClient().generate(
            provider=config.provider,
            model=config.default_model,
            api_key=config.decrypted_api_key,
            api_base=config.api_base_url,
            system_prompt=(
                "你是 CI 日志关键阶段定位器。只依据日志和 step 元数据，按时间顺序定位故障边界。"
                "忽略 artifact 上传、清理动作、文件缺失 warning 等失败后的二次噪声。"
                "`Stream logs` 是正常的日志收集/展示阶段；进入该阶段或 step 名称本身绝不是异常，禁止将其写成失败原因。"
                "如果日志包含 aisbench 的 performance/accuracy verification failed，应把该验收判定、实际值、baseline 和 threshold 作为最高优先级直接错误。"
                "不要提出修复方案，不执行工具，不分析代码，不虚构。输出必须使用以下格式：\n"
                "关键阶段定位：\n"
                "- 进入阶段：[时间/日志行] 开始发生阻塞或异常的阶段\n"
                "- 首次异常：[时间/日志行] 最早的关键异常\n"
                "- 最终失败：[时间/日志行] 导致 Job 失败的错误\n"
                "根因摘要：用 1-2 句说明直接原因；证据不足时明确写出。"
            ),
            user_prompt=(
                f"Workflow: {job.workflow_name}\nJob: {job.job_name}\n"
                f"Conclusion: {job.conclusion}\n"
                f"失败 Step 元数据：{json.dumps({'first_failed_step': step_stage['first_failed_step']}, ensure_ascii=False)}\n\n"
                f"按因果优先级选取的日志证据（保留原始行号和时间）：\n{excerpt}"
            ),
            temperature=0.1,
            max_tokens=500,
        )
        record.status = "completed"
        record.summary = llm_result.content.strip()
        record.log_excerpt = excerpt
        record.llm_provider = config.provider
        record.llm_model = config.default_model
        record.prompt_tokens = llm_result.prompt_tokens
        record.completion_tokens = llm_result.completion_tokens
        record.generation_time_seconds = llm_result.generation_time
        record.error_message = None
    except Exception as exc:
        logger.warning("Lightweight log summary failed for job %s: %s", job_id, exc)
        record.status = "failed"
        record.error_message = str(exc)[:500]
    await db.commit()
    await db.refresh(record)
    return _serialize_job_log_summary(record)
    r"""
    Legacy duplicate comparison body retained temporarily as inert text after
    restoring compare_ci_jobs above. Remove after the second-version rollout.
    version_differences = []
    for label, aliases in version_aliases.items():
        start_value = _find_metadata_value(start["metadata"], aliases)
        end_value = _find_metadata_value(end["metadata"], aliases)
        version_differences.append({
            "field": label,
            "start": start_value,
            "end": end_value,
            "changed": start_value != end_value if start_value is not None and end_value is not None else None,
            "availability": "available" if start_value is not None and end_value is not None else "unknown",
            "source": "ci_results.data / ci_jobs.data",
        })

    config_fields = {
        "hardware": (start["hardware"], end["hardware"]),
        "runner": (start["runner_name"], end["runner_name"]),
        "runner_labels": (start["runner_labels"], end["runner_labels"]),
        "branch": (start["branch"], end["branch"]),
        "event": (start["event"], end["event"]),
    }
    configuration_differences = [
        {"field": field, "start": values[0], "end": values[1], "changed": values[0] != values[1], "source": "ci_jobs / ci_results"}
        for field, values in config_fields.items()
    ]

    compare_url = None
    compared_commits: list[dict[str, Any]] = []
    compare_error = None
    workflow_config_result = await db.execute(
        select(WorkflowConfig.workflow_file).where(
            WorkflowConfig.workflow_name == start_job.workflow_name
        )
    )
    workflow_file = workflow_config_result.scalar_one_or_none()
    referenced_yaml_names = {
        name.lower() for name in re.findall(
            r"[A-Za-z0-9_.-]+\.ya?ml", start_job.job_name or "", flags=re.IGNORECASE
        )
    }
    if workflow_file:
        referenced_yaml_names.add(workflow_file.split("/")[-1].lower())
    if start.get("workflow_head_sha") and end.get("workflow_head_sha"):
        compare_url = (
            f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/compare/"
            f"{start['workflow_head_sha']}...{end['workflow_head_sha']}"
        )
        if settings.GITHUB_TOKEN:
            try:
                from infrastructure.clients.github_client import GitHubClient

                async with GitHubClient(
                    token=settings.GITHUB_TOKEN,
                    owner=settings.GITHUB_OWNER,
                    repo=settings.GITHUB_REPO,
                ) as client:
                    compare = await client.get_compare_commits(
                        start["workflow_head_sha"], end["workflow_head_sha"]
                    )
                    for commit in compare.get("commits", []):
                        message = commit.get("commit", {}).get("message", "")
                        title = message.splitlines()[0] if message else ""
                        pr_match = re.search(r"\(#(\d+)\)", title) or re.search(
                            r"(?:PR\s*)?#(\d+)", title, flags=re.IGNORECASE
                        )
                        if not pr_match:
                            continue
                        pr_number = int(pr_match.group(1))
                        title_lower = title.lower()
                        # File lookups are reserved for likely CI/test changes;
                        # final inclusion still requires an exact referenced YAML hit.
                        if not any(word in title_lower for word in (
                            "test", "ci", "nightly", "workflow", "config", "multi-node"
                        )):
                            continue
                        files = await client.get_pr_files(
                            settings.GITHUB_OWNER, settings.GITHUB_REPO, pr_number
                        )
                        changed_paths = [
                            item.get("filename", "") for item in files if item.get("filename")
                        ]
                        matched_paths = [
                            path for path in changed_paths
                            if path.split("/")[-1].lower() in referenced_yaml_names
                        ]
                        if not matched_paths:
                            continue
                        sha = commit.get("sha", "")
                        compared_commits.append({
                            "number": pr_number,
                            "sha": sha,
                            "title": title,
                            "author": (commit.get("author") or {}).get("login")
                                or commit.get("commit", {}).get("author", {}).get("name"),
                            "url": f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/pull/{pr_number}",
                            "commit_url": commit.get("html_url"),
                            "matched_files": matched_paths,
                        })
            except Exception as exc:
                logger.warning("Failed to compare selected CI boundary SHAs: %s", exc)
                compare_error = str(exc)
        else:
            compare_error = "GitHub Token 未配置"

    return {
        "start": {key: value for key, value in start.items() if key not in {"metadata", "steps"}},
        "end": {key: value for key, value in end.items() if key not in {"metadata", "steps"}},
        "pr_changes": {
            "source": "GitHub compare commit range + exact referenced YAML file intersection",
            "precision": "job_file_intersection",
            "compare_url": compare_url,
            "error": compare_error,
            "items": compared_commits,
        },
        "version_differences": version_differences,
        "failure_stage_difference": {
            "start": _failure_stage(start["steps"]),
            "end": _failure_stage(end["steps"]),
        },
        "test_difference": {
            "status": "unknown",
            "summary": "当前仅确认两端 Job 名相同；尚无完整 Case/测试集合快照，不能宣称无 Test 差异。",
            "source": "workflow_name + job_name identity",
        },
        "configuration_differences": configuration_differences,
        "warnings": [
            "Workflow head SHA 不一定是实际被测 vllm-ascend SHA。",
            "这里只展示 Start/End SHA 范围内、且直接修改当前 Job 引用 YAML/Workflow 文件的 PR。",
        ],
    }
    """


@router.get("/jobs/{job_id}", response_model=CIJobDetailResponse)
async def get_job_detail(
    job_id: int,
    db: DbSession
):
    """获取 job 详细信息"""
    stmt = select(CIJob).where(CIJob.job_id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )

    # 解析数据
    steps_data = []
    if job.steps_data:
        try:
            steps_data = json.loads(job.steps_data)
        except Exception:
            pass

    runner_labels = []
    if job.runner_labels:
        try:
            runner_labels = json.loads(job.runner_labels)
        except Exception:
            pass

    return CIJobDetailResponse(
        id=job.id,
        job_id=job.job_id,
        run_id=job.run_id,
        workflow_name=job.workflow_name,
        job_name=job.job_name,
        status=job.status,
        conclusion=job.conclusion,
        hardware=job.hardware,
        runner_name=job.runner_name,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
        runner_labels=runner_labels,
        steps_summary=steps_data,
        steps_data=steps_data,
        logs_url=job.logs_url,
        created_at=job.created_at,
    )


@router.get("/reports/daily/{date}", response_model=CIDailyReport)
async def get_daily_report(
    date: str,
    db: DbSession
):
    """
    获取指定日期的 CI 每日报告

    Args:
        date: 日期，格式 YYYY-MM-DD (北京时间)

    Returns:
        CIDailyReport: 包含统计数据、workflow 结果和 markdown 报告
    """
    try:
        # 解析日期 (北京时间)
        try:
            report_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD"
            )

        # 使用北京时间 (CST/UTC+8)
        from datetime import timezone as tz
        beijing_tz = tz(timedelta(hours=8))

        # 获取当天的起始和结束时间 (北京时间)
        start_datetime = datetime.combine(report_date, datetime.min.time()).replace(tzinfo=beijing_tz)
        end_datetime = start_datetime + timedelta(days=1)

        # 转换为 UTC 时间用于数据库查询 (数据库存储的是 UTC)
        start_datetime_utc = start_datetime.astimezone(UTC)
        end_datetime_utc = end_datetime.astimezone(UTC)

        # 获取启用的 workflow 配置（含时间窗口）
        enabled_stmt = select(
            WorkflowConfig.workflow_name,
            WorkflowConfig.stats_start_hour,
            WorkflowConfig.stats_end_hour,
        ).where(WorkflowConfig.enabled)
        enabled_result = await db.execute(enabled_stmt)
        wf_configs = [(row[0], row[1], row[2]) for row in enabled_result.all()]
        enabled_workflows = [wc[0] for wc in wf_configs]

        # 如果没有启用的 workflow，返回空报告
        if not enabled_workflows:
            return CIDailyReport(
                date=date,
                summary={
                    "total_runs": 0,
                    "success_runs": 0,
                    "failure_runs": 0,
                    "success_rate": 0.0,
                    "avg_duration_seconds": None,
                },
                workflow_results=[],
                job_stats=[],
                markdown_report=f"# CI 每日报告\n\n## {date}\n\n暂无数据"
            )

        # 查询当天的 CI 结果 (使用 UTC 时间查询)
        stmt = select(CIResult).where(
            CIResult.started_at >= start_datetime_utc,
            CIResult.started_at < end_datetime_utc,
            CIResult.workflow_name.in_([wc[0] for wc in wf_configs])
        ).order_by(CIResult.started_at.desc())

        result = await db.execute(stmt)
        ci_results = result.scalars().all()

        # 统计数据
        total_runs = len(ci_results)
        success_runs = sum(1 for r in ci_results if r.conclusion == "success")
        failure_runs = sum(1 for r in ci_results if r.conclusion in ["failure", "cancelled"])
        success_rate = round((success_runs / total_runs * 100) if total_runs > 0 else 0.0, 2)

        # 平均时长
        durations = [r.duration_seconds for r in ci_results if r.duration_seconds]
        avg_duration = round(sum(durations) / len(durations)) if durations else None

        # 按 workflow 分组统计
        workflow_stats = {}
        for r in ci_results:
            if r.workflow_name not in workflow_stats:
                workflow_stats[r.workflow_name] = {
                    "workflow_name": r.workflow_name,
                    "total_runs": 0,
                    "success_runs": 0,
                    "failure_runs": 0,
                    "avg_duration": None,
                    "latest_run": None,
                    "hardware": r.hardware,
                    "failed_jobs": [],  # 添加失败 job 列表
                    "total_jobs": 0,  # 总 job 数
                    "passed_jobs": 0,  # 通过 job 数
                }
            stats = workflow_stats[r.workflow_name]
            stats["total_runs"] += 1
            if r.conclusion == "success":
                stats["success_runs"] += 1
            elif r.conclusion in ["failure", "cancelled"]:
                stats["failure_runs"] += 1
            # 更新最新运行记录
            if not stats["latest_run"] or (r.started_at and r.started_at > stats["latest_run"]["started_at"]):
                # 转换为北京时间 (UTC+8)
                # 数据库存储的是 UTC 时间 (naive datetime),需要显式添加 UTC 时区信息后转换
                beijing_started_at = None
                if r.started_at:
                    # 如果 started_at 是 naive datetime，假设它是 UTC
                    if r.started_at.tzinfo is None:
                        utc_started_at = r.started_at.replace(tzinfo=UTC)
                    else:
                        utc_started_at = r.started_at
                    # 转换为北京时间
                    beijing_tz = timezone(timedelta(hours=8))
                    beijing_started_at = utc_started_at.astimezone(beijing_tz)

                stats["latest_run"] = {
                    "run_id": r.run_id,
                    "status": r.status,
                    "conclusion": r.conclusion,
                    "started_at": beijing_started_at.isoformat() if beijing_started_at else None,
                    "duration_seconds": r.duration_seconds,
                }

        # 查询失败 workflow 的 jobs 详情
        failed_run_ids = [r.run_id for r in ci_results if r.conclusion in ["failure", "cancelled"]]
        if failed_run_ids:
            jobs_stmt = select(CIJob).where(
                CIJob.run_id.in_(failed_run_ids),
                CIJob.conclusion.in_(["failure", "cancelled"])
            ).order_by(CIJob.workflow_name, CIJob.job_name)
            jobs_result = await db.execute(jobs_stmt)
            failed_jobs = jobs_result.scalars().all()

            # 获取 job 责任人
            job_names = list({f"{job.workflow_name}|||{job.job_name}" for job in failed_jobs})
            owners_stmt = select(JobOwner).where(
                JobOwner.workflow_name.in_([name.split("|||")[0] for name in job_names]),
                JobOwner.job_name.in_([name.split("|||")[1] for name in job_names])
            )
            owners_result = await db.execute(owners_stmt)
            job_owners_map = {}
            for owner in owners_result.scalars().all():
                key = f"{owner.workflow_name}|||{owner.job_name}"
                job_owners_map[key] = owner

            # 将失败 job 添加到对应的 workflow，并计算连续失败次数
            for job in failed_jobs:
                owner_key = f"{job.workflow_name}|||{job.job_name}"
                owner = job_owners_map.get(owner_key)

                # 查询该 job 的历史运行记录，计算连续失败次数
                consecutive_failures = 0
                job_history_stmt = select(CIJob).where(
                    CIJob.workflow_name == job.workflow_name,
                    CIJob.job_name == job.job_name
                ).order_by(CIJob.started_at.desc()).limit(20)  # 最多查最近 20 次
                job_history_result = await db.execute(job_history_stmt)
                job_history = job_history_result.scalars().all()

                # 计算连续失败次数（从最近一次开始，直到遇到成功为止）
                for hist_job in job_history:
                    if hist_job.conclusion in ["failure", "cancelled"]:
                        consecutive_failures += 1
                    else:
                        break

                failed_job_info = {
                    "job_name": job.job_name,
                    "conclusion": job.conclusion,
                    "duration_seconds": job.duration_seconds,
                    "github_url": f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}/actions/runs/{job.run_id}/job/{job.job_id}" if job.job_id else None,
                    "owner": owner.owner if owner else None,
                    "owner_email": owner.email if owner else None,
                    "consecutive_failures": consecutive_failures,  # 连续失败次数
                }
                workflow_stats[job.workflow_name]["failed_jobs"].append(failed_job_info)

        # 查询所有 job 以计算每个 workflow 的 job 通过率
        all_run_ids = [r.run_id for r in ci_results]
        if all_run_ids:
            all_jobs_stmt = select(CIJob).where(
                CIJob.run_id.in_(all_run_ids)
            )
            all_jobs_result = await db.execute(all_jobs_stmt)
            all_jobs = all_jobs_result.scalars().all()

            # 统计每个 workflow 的 job 数量
            for job in all_jobs:
                if job.workflow_name in workflow_stats:
                    workflow_stats[job.workflow_name]["total_jobs"] += 1
                    if job.conclusion == "success":
                        workflow_stats[job.workflow_name]["passed_jobs"] += 1

        # 计算每个 workflow 的平均时长
        workflow_durations = {}
        for r in ci_results:
            if r.duration_seconds:
                if r.workflow_name not in workflow_durations:
                    workflow_durations[r.workflow_name] = []
                workflow_durations[r.workflow_name].append(r.duration_seconds)

        for wf_name, durations in workflow_durations.items():
            if wf_name in workflow_stats:
                workflow_stats[wf_name]["avg_duration"] = round(sum(durations) / len(durations))

        # 转换为列表
        workflow_results = list(workflow_stats.values())

        # 生成 Markdown 报告
        markdown_lines = [
            "# CI 每日运行报告",
            "",
            f"## 📅 日期：{date}",
            "",
            "## 📊 总体统计",
            "",
            f"- **总运行次数**: {total_runs}",
            f"- **成功次数**: {success_runs}",
            f"- **失败次数**: {failure_runs}",
            f"- **成功率**: {success_rate}%",
            f"- **平均时长**: {avg_duration // 60}分{avg_duration % 60}秒" if avg_duration else "- **平均时长**: 无数据",
            "",
        ]

        if workflow_results:
            markdown_lines.extend([
                "## 🔧 Workflow 详情",
                "",
            ])

            for wf in sorted(workflow_results, key=lambda x: x["workflow_name"]):
                hw_tag = f" ({wf['hardware']})" if wf.get('hardware') and wf['hardware'] != 'unknown' else ""
                # 使用 job 通过率计算成功率
                job_success_rate = round((wf["passed_jobs"] / wf["total_jobs"] * 100) if wf["total_jobs"] > 0 else 0.0, 2)
                avg_dur_str = f"{wf['avg_duration'] // 60}分{wf['avg_duration'] % 60}秒" if wf['avg_duration'] else "无数据"

                markdown_lines.extend([
                    f"### {wf['workflow_name']}{hw_tag}",
                    "",
                    f"- 运行次数：{wf['total_runs']}",
                    f"- Job 通过率：{job_success_rate}% ({wf['passed_jobs']}/{wf['total_jobs']})",
                    f"- 平均时长：{avg_dur_str}",
                ])

                # 添加失败 job 详情
                if wf.get('failed_jobs') and len(wf['failed_jobs']) > 0:
                    markdown_lines.extend([
                        "",
                        "**❌ 失败 Job 详情:**",
                        "",
                    ])
                    for job in wf['failed_jobs']:
                        duration_str = f"{job['duration_seconds'] // 60}分{job['duration_seconds'] % 60}秒" if job['duration_seconds'] else "N/A"
                        owner_info = f"**{job['owner']}**" if job.get('owner') else "未配置责任人"
                        if job.get('owner_email'):
                            owner_info += f" ({job['owner_email']})"

                        # 连续失败次数标记
                        consecutive_failures = job.get('consecutive_failures', 0)
                        if consecutive_failures > 0:
                            if consecutive_failures >= 5:
                                failure_flag = f"🔥 **连续失败 {consecutive_failures} 次**"
                            elif consecutive_failures >= 3:
                                failure_flag = f"⚠️ 连续失败 {consecutive_failures} 次"
                            else:
                                failure_flag = f"连续失败 {consecutive_failures} 次"
                        else:
                            failure_flag = ""

                        github_link = f"[查看 Job]({job['github_url']})" if job.get('github_url') else "无链接"

                        markdown_lines.extend([
                            f"- **{job['job_name']}**: {job['conclusion']} | 时长：{duration_str} | 责任人：{owner_info} | {failure_flag + ' | ' if failure_flag else ''}{github_link}",
                        ])

                markdown_lines.append("")

        # 添加总结
        markdown_lines.extend([
            "## 📝 总结",
            "",
        ])

        if total_runs == 0:
            markdown_lines.append("当日暂无 CI 运行记录。")

        # 添加失败 job 汇总
        total_failed_jobs = sum(len(wf.get('failed_jobs', [])) for wf in workflow_results)
        if total_failed_jobs > 0:
            markdown_lines.extend([
                "",
                f"当日共有 **{total_failed_jobs}** 个失败 Job，请相关责任人及时跟进处理。",
            ])

        markdown_report = "\n".join(markdown_lines)

        return CIDailyReport(
            date=date,
            summary={
                "total_runs": total_runs,
                "success_runs": success_runs,
                "failure_runs": failure_runs,
                "success_rate": success_rate,
                "avg_duration_seconds": avg_duration,
            },
            workflow_results=workflow_results,
            markdown_report=markdown_report,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate daily report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate report: {str(e)}"
        )


# ============ Failure Analysis API ============

@router.post("/failure-analysis/analyze/{job_id}")
async def analyze_failed_job(
    job_id: int,
    current_user: CurrentAdminUser,
    db: DbSession,
    force: bool = Query(default=False, description="强制重新分析"),
):
    """触发失败分析（异步后台执行，立即返回，前端轮询 GET 接口获取结果）"""
    from failure_analysis.failure_analysis import FailureAnalysisService
    from infrastructure.persistence.models import CIJob, JobFailureAnalysis

    # 1. 验证 job 存在
    stmt = select(CIJob).where(CIJob.job_id == job_id)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"CIJob with job_id={job_id} not found")
    if job.conclusion not in (
        "failure",
        "timed_out",
        "startup_failure",
        "cancelled",
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Job conclusion is '{job.conclusion}', not a failed job")

    run_result = await db.execute(select(CIResult.workflow_name).where(CIResult.run_id == job.run_id))
    canonical_workflow_name = run_result.scalar_one_or_none() or job.workflow_name

    # 2. 检查已有记录
    existing_stmt = select(JobFailureAnalysis).where(
        JobFailureAnalysis.job_id == job_id
    ).order_by(JobFailureAnalysis.id.desc()).limit(1)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing and existing.workflow_name != canonical_workflow_name:
        existing.workflow_name = canonical_workflow_name
    service = FailureAnalysisService()

    # 2a. 分析进行中 → 直接返回，让前端轮询（force 也不允许重复提交）
    if existing and existing.analysis_status == "analyzing":
        return existing

    # 2b. 已完成且非 force → 直接返回缓存
    if existing and existing.analysis_status in ("completed", "reused") and not force:
        changed = await service._sync_problem_category_to_daily_failure(
            db,
            job_id=job_id,
            run_id=job.run_id,
            workflow_name=canonical_workflow_name,
            job_name=job.job_name,
            problem_category=existing.problem_category,
        )
        if changed:
            await db.commit()
        return existing

    # 3. 创建或复用 "analyzing" 占位记录，立即返回。强制重跑时
    # 保留同一行和 share URL，避免 DELETE 锁冲突及前端轮询旧 ID。
    fingerprint = service.compute_failure_fingerprint(job)
    if existing:
        placeholder = existing
        placeholder.analysis_status = "analyzing"
        placeholder.analysis_phase = "queued"
        placeholder.error_message = None
        placeholder.failure_fingerprint = fingerprint
        placeholder.agent_trace = []
        placeholder.agent_steps = 0
        placeholder.evidence_ledger = None
        placeholder.validation_result = None
        placeholder.reused_analysis_id = None
        placeholder.report_file_path = None
        placeholder.pdf_file_path = None
        placeholder.problem_category = None
        placeholder.root_cause_summary = None
        placeholder.improvement_measures_summary = None
    else:
        placeholder = JobFailureAnalysis(
            job_id=job_id,
            run_id=job.run_id,
            workflow_name=canonical_workflow_name,
            job_name=job.job_name,
            failure_date=job.completed_at or datetime.now(UTC),
            failure_fingerprint=fingerprint,
            analysis_status="analyzing",
            analysis_phase="queued",
        )
        db.add(placeholder)
    await db.commit()
    await db.refresh(placeholder)

    # 4. Enqueue durable analysis work for a Collector worker.  The job ID is
    # the stable dedupe identity; a random suffix allowed concurrent clicks to
    # enqueue duplicate analyses for the same GitHub job and race on one row.
    from infrastructure.tasks.task_manager import TaskManager

    await TaskManager.create_task(
        db,
        "failure_analysis",
        {"job_id": job_id, "force": force, "triggered_by": "manual"},
        f"failure_analysis:{job_id}",
        required_capability="python",
        priority=20,
    )
    await db.commit()
    # Preserve the existing API contract: the caller receives the durable
    # placeholder and can poll the normal analysis endpoint for completion.
    return placeholder


@router.post("/daily-failures/batch-analyze")
async def batch_analyze_daily_failures(
    current_user: CurrentAdminUser,
    db: DbSession,
    ids: list[int] = Query(..., description="要分析的每日失败记录 ID"),
):
    """Queue failure analysis for explicitly selected daily failure rows."""
    if not ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids 不能为空",
        )
    unique_ids = list(dict.fromkeys(ids))
    if len(unique_ids) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="单次最多分析 50 条记录",
        )

    result = await db.execute(
        select(DailyFailureRecord).where(DailyFailureRecord.id.in_(unique_ids))
    )
    records = result.scalars().all()
    if len(records) != len(unique_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="部分每日失败记录不存在",
        )

    queued = 0
    analyzing = 0
    completed = 0
    skipped = 0
    errors: list[str] = []
    seen_job_ids: set[int] = set()
    for record in records:
        if not record.job_id or record.job_id in seen_job_ids:
            skipped += 1
            continue
        seen_job_ids.add(record.job_id)
        try:
            analysis = await analyze_failed_job(
                record.job_id,
                current_user,
                db,
                False,
            )
            if analysis.analysis_status in ("completed", "reused"):
                completed += 1
            elif analysis.analysis_status == "analyzing" and analysis.analysis_phase != "queued":
                analyzing += 1
            else:
                queued += 1
        except HTTPException as exc:
            skipped += 1
            errors.append(f"{record.job_name}: {exc.detail}")

    return {
        "success": True,
        "selected": len(unique_ids),
        "queued": queued,
        "analyzing": analyzing,
        "completed": completed,
        "skipped": skipped,
        "errors": errors[:10],
    }


@router.post("/failure-analysis/cancel/{job_id}")
async def cancel_analysis(
    job_id: int,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """取消正在进行的失败分析"""
    from infrastructure.persistence.models import JobFailureAnalysis

    stmt = select(JobFailureAnalysis).where(
        JobFailureAnalysis.job_id == job_id,
        JobFailureAnalysis.analysis_status == "analyzing",
    )
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()

    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="没有正在进行的分析任务",
        )

    analysis.analysis_status = "cancelled"
    analysis.analysis_phase = "cancelled"
    analysis.error_message = "用户手动取消"
    await db.commit()

    logger.info(f"Analysis cancelled for job {job_id}")
    return {"success": True, "message": "分析已取消"}


@router.post("/failure-analysis/analyze-batch")
async def analyze_batch(
    current_user: CurrentAdminUser,
    db: DbSession,
    days_back: int = Query(default=7, description="分析最近N天的失败Job"),
):
    from failure_analysis.failure_analysis import FailureAnalysisService
    service = FailureAnalysisService()
    try:
        queued_job_ids = await service.analyze_batch(days_back=days_back, db=db)
        return {
            "success": True,
            "message": f"已排队 {len(queued_job_ids)} 个失败 Job，后台最多同时分析 3 个",
            "count": len(queued_job_ids),
        }
    except Exception as e:
        logger.error(f"Failed to analyze batch: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"批量分析失败: {str(e)}")


@router.get("/failure-analysis/list", response_model=FailureAnalysisListResponse)
async def list_failure_analyses(
    problem_category: str | None = Query(default=None),
    analysis_status_filter: str | None = Query(default=None, alias="analysis_status"),
    workflow_name: str | None = Query(default=None),
    days_back: int | None = Query(default=7),
    db: DbSession = None,
):
    from failure_analysis.failure_analysis import FailureAnalysisService
    service = FailureAnalysisService()
    filters = {}
    if problem_category:
        filters["problem_category"] = problem_category
    if analysis_status_filter:
        filters["analysis_status"] = analysis_status_filter
    if workflow_name:
        filters["workflow_name"] = workflow_name
    if days_back:
        filters["days_back"] = days_back
    return await service.list_analyses(filters=filters, db=db)


@router.get("/failure-analysis/{analysis_id}", response_model=FailureAnalysisResponse)
async def get_failure_analysis(
    analysis_id: int,
    db: DbSession,
):
    from failure_analysis.failure_analysis import FailureAnalysisService
    service = FailureAnalysisService()
    analysis = await service.get_analysis(analysis_id=analysis_id, db=db)
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析记录不存在")
    return analysis


@router.get("/failure-analysis/{analysis_id}/knowledge-graph", response_model=FailureAnalysisKnowledgeGraphResponse)
async def get_failure_analysis_knowledge_graph(
    analysis_id: int,
    db: DbSession,
):
    stmt = select(JobFailureAnalysis).where(JobFailureAnalysis.id == analysis_id)
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析记录不存在")

    from failure_analysis.failure_analysis_knowledge_graph import (
        build_failure_analysis_knowledge_graph,
    )

    return build_failure_analysis_knowledge_graph(analysis)


@router.get("/failure-analysis/{analysis_id}/report")
async def get_failure_analysis_report(
    analysis_id: int,
    db: DbSession,
):
    from failure_analysis.failure_analysis import FailureAnalysisService
    service = FailureAnalysisService()
    analysis = await service.get_analysis(analysis_id=analysis_id, db=db)
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析记录不存在")
    if not analysis.report_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件不存在")
    from infrastructure.storage.failure_analysis_file_store import FailureAnalysisFileStore
    file_store = FailureAnalysisFileStore()
    content = await file_store.read_report_by_path(analysis.report_file_path)
    if not content:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件读取失败")
    return {"content": content, "analysis_id": analysis_id}


@router.get("/jobs/{job_id}/failure-analysis", response_model=FailureAnalysisResponse | None)
async def get_job_failure_analysis(
    job_id: int,
    db: DbSession,
):
    from failure_analysis.failure_analysis import FailureAnalysisService
    service = FailureAnalysisService()
    analysis = await service.get_analysis_by_job_id(job_id=job_id, db=db)
    if not analysis:
        return None
    return analysis


# ============ Claude Code CLI 日志 ============


@router.get("/claude-logs")
async def list_claude_logs(
    current_user: CurrentAdminUser,
    days: int = Query(default=7, ge=1, le=30),
):
    """列出最近 N 天的 Claude Code CLI 调用日志（需 admin）"""
    from infrastructure.clients.claude_code_cli import _CLI_LOG_DIR

    logs: list[dict] = []
    if not _CLI_LOG_DIR.exists():
        return {"logs": [], "message": "No logs yet"}

    cutoff = datetime.now(UTC).date()
    for date_dir in sorted(_CLI_LOG_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        dir_date = date_dir.name[:10]
        if (cutoff - datetime.strptime(dir_date, "%Y-%m-%d").date()).days > days:
            continue
        for log_file in sorted(date_dir.iterdir(), reverse=True):
            if log_file.suffix == ".log":
                stat = log_file.stat()
                logs.append({
                    "date": dir_date,
                    "filename": log_file.name,
                    "path": str(log_file.relative_to(_CLI_LOG_DIR)),
                    "size": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
                })

    return {"total": len(logs), "logs": logs}


@router.get("/claude-logs/{date}/{filename}")
async def read_claude_log(
    date: str,
    filename: str,
    current_user: CurrentAdminUser,
):
    """读取指定的 Claude Code CLI 日志文件内容"""
    from infrastructure.clients.claude_code_cli import _CLI_LOG_DIR

    filepath = _CLI_LOG_DIR / date / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


# ============ Failure Analysis Agent Config ============

@router.get("/agent-config")
@router.get("/claude-cli-config", include_in_schema=False)
async def get_failure_analysis_agent_config(
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """获取失败分析 Agent 的最大步骤数和总超时。"""
    from infrastructure.persistence.models import ProjectDashboardConfig

    stmt = select(ProjectDashboardConfig).where(
        ProjectDashboardConfig.config_key == "failure_analysis_agent_config"
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row and row.config_value:
        return {"runtime": "claude_cli", **dict(row.config_value)}
    return {"runtime": "claude_cli", "max_turns": 80, "timeout_seconds": 1800}


@router.put("/agent-config")
@router.put("/claude-cli-config", include_in_schema=False)
async def update_failure_analysis_agent_config(
    data: dict,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """更新失败分析 Agent 运行限制。"""
    from infrastructure.persistence.models import ProjectDashboardConfig

    try:
        runtime = str(data.get("runtime", "claude_cli")).strip().lower()
        max_turns = int(data.get("max_turns", 80))
        timeout_seconds = int(data.get("timeout_seconds", 1800))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="轮次和超时必须是整数") from exc
    if runtime not in {"claude_cli", "custom_agent"}:
        raise HTTPException(status_code=422, detail="运行方式必须是 claude_cli 或 custom_agent")
    if not 3 <= max_turns <= 1000:
        raise HTTPException(status_code=422, detail="最大轮次必须在 3 到 1000 之间")
    if not 60 <= timeout_seconds <= 7200:
        raise HTTPException(status_code=422, detail="超时必须在 60 到 7200 秒之间")
    config_value = {
        "runtime": runtime,
        "max_turns": max_turns,
        "timeout_seconds": timeout_seconds,
    }

    stmt = select(ProjectDashboardConfig).where(
        ProjectDashboardConfig.config_key == "failure_analysis_agent_config"
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row:
        row.config_value = config_value
    else:
        row = ProjectDashboardConfig(
            config_key="failure_analysis_agent_config",
            config_value=config_value,
        )
        db.add(row)
    await db.commit()
    return config_value


# ============ Public Share API (no auth) ============

@router.get("/public/analysis/{share_token}")
async def get_public_analysis(
    share_token: str,
    db: DbSession,
):
    """通过分享链接查看分析报告（无需登录）"""
    from infrastructure.persistence.models import JobFailureAnalysis

    stmt = select(JobFailureAnalysis).where(JobFailureAnalysis.share_token == share_token)
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
    return analysis


@router.get("/public/analysis/{share_token}/report")
async def get_public_analysis_report(
    share_token: str,
    db: DbSession,
    download: bool = Query(default=False),
):
    """通过分享链接查看/下载分析报告全文（无需登录）"""
    from infrastructure.persistence.models import JobFailureAnalysis

    stmt = select(JobFailureAnalysis).where(JobFailureAnalysis.share_token == share_token)
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()
    if not analysis or not analysis.report_file_path:
        raise HTTPException(status_code=404, detail="报告不存在")
    from infrastructure.storage.failure_analysis_file_store import FailureAnalysisFileStore

    file_store = FailureAnalysisFileStore()
    content = await file_store.read_report_by_path(analysis.report_file_path) or ""
    if download:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=report_{analysis.job_id}.md"})
    return {"content": content, "analysis": analysis}


@router.get("/public/analysis/{share_token}/pdf")
async def download_public_analysis_pdf(
    share_token: str,
    db: DbSession,
):
    """下载分析报告 PDF（无需登录）"""


    from infrastructure.persistence.models import JobFailureAnalysis

    stmt = select(JobFailureAnalysis).where(JobFailureAnalysis.share_token == share_token)
    result = await db.execute(stmt)
    analysis = result.scalar_one_or_none()
    if not analysis:
        raise HTTPException(status_code=404, detail="报告不存在")

    # 优先使用预生成的 PDF 缓存
    if analysis.pdf_file_path:
        from pathlib import Path
        pdf_path = Path("/app/data") / analysis.pdf_file_path
        if pdf_path.exists():
            from fastapi.responses import FileResponse
            return FileResponse(str(pdf_path), media_type="application/pdf",
                filename=f"report_{analysis.job_id}.pdf")

    # 无缓存时现场生成（稍慢但保证可用）
    if not analysis.report_file_path:
        raise HTTPException(status_code=404, detail="报告文件不存在")

    from infrastructure.storage.failure_analysis_file_store import FailureAnalysisFileStore
    file_store = FailureAnalysisFileStore()
    md_content = await file_store.read_report_by_path(analysis.report_file_path) or ""

    from failure_analysis.failure_analysis import FailureAnalysisService
    svc = FailureAnalysisService()
    meta = {
        "category": analysis.problem_category or "-",
        "status": "已完成" if analysis.analysis_status == "completed" else analysis.analysis_status,
        "summary": analysis.root_cause_summary or "-",
        "measures": analysis.improvement_measures_summary or "-",
        "provider": analysis.llm_provider or "",
        "model": analysis.llm_model or "",
        "duration": analysis.generation_time_seconds,
    }
    pdf_path = await svc._generate_pdf(md_content, analysis.workflow_name, "", analysis.job_id, metadata=meta)
    if pdf_path:
        analysis.pdf_file_path = pdf_path
        await db.commit()
        from fastapi.responses import FileResponse
        return FileResponse(pdf_path, media_type="application/pdf",
            filename=f"report_{analysis.job_id}.pdf")

    raise HTTPException(status_code=500, detail="PDF 生成失败")


# ============ Daily Failure Tracking ============

_DAILY_FAILURE_CONCLUSION_LABELS = {
    "success": "成功",
    "failure": "失败",
    "cancelled": "已取消",
    "timed_out": "超时",
    "skipped": "已跳过",
}
_BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _daily_failure_query(
    *,
    start_date: str | None,
    end_date: str | None,
    workflow_name: str | None,
    processing_status: str | None,
    notes_search: str | None,
):
    """Build the shared filtered query used by the page and CSV export."""
    stmt = select(DailyFailureRecord)
    if start_date:
        try:
            parsed_start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date 格式须为 YYYY-MM-DD",
            ) from None
        stmt = stmt.where(DailyFailureRecord.report_date >= parsed_start_date)
    if end_date:
        try:
            parsed_end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date 格式须为 YYYY-MM-DD",
            ) from None
        stmt = stmt.where(DailyFailureRecord.report_date <= parsed_end_date)
    if workflow_name:
        stmt = stmt.where(DailyFailureRecord.workflow_name == workflow_name)
    if processing_status:
        stmt = stmt.where(DailyFailureRecord.processing_status == processing_status)
    if notes_search:
        stmt = stmt.where(DailyFailureRecord.notes.like(f"%{notes_search}%"))
    return stmt


def _format_daily_failure_export_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.astimezone(_BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _safe_daily_failure_csv_cell(value: Any) -> Any:
    """Prevent spreadsheet formula execution for user-controlled text cells."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _serialize_daily_failures_csv(records: list[DailyFailureRecord]) -> bytes:
    """Serialize records as an Excel-friendly UTF-8 CSV payload."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "日期", "来源分支", "Workflow", "运行结果", "Job", "用例名称",
        "硬件", "责任人", "模型FO", "测试模型", "部署类型", "问题分类",
        "关联PR", "处理状态", "备注", "处理时间(北京时间)", "闭环时间(北京时间)",
        "开始时间(北京时间)", "结束时间(北京时间)", "耗时(秒)", "Run ID", "Job ID", "GitHub链接",
        "最后更新人", "状态更新时间",
    ])
    for record in records:
        writer.writerow(map(_safe_daily_failure_csv_cell, [
            str(record.report_date),
            record.source_branch or "",
            record.workflow_name,
            _DAILY_FAILURE_CONCLUSION_LABELS.get(
                record.conclusion or "", record.conclusion or ""
            ),
            record.job_name,
            record.display_name or "",
            record.hardware or "",
            record.owner or "",
            record.model_fo or "",
            record.test_model or "",
            record.deployment_type or "",
            record.problem_category or "",
            record.related_pr or "",
            record.processing_status or "未处理",
            record.notes or "",
            _format_daily_failure_export_datetime(record.processing_time),
            _format_daily_failure_export_datetime(record.closure_time),
            _format_daily_failure_export_datetime(record.started_at),
            _format_daily_failure_export_datetime(record.completed_at),
            record.duration_seconds if record.duration_seconds is not None else "",
            record.run_id,
            record.job_id or "",
            record.github_job_url or "",
            record.updated_by or "",
            _format_daily_failure_export_datetime(record.status_updated_at),
        ]))
    return ("\ufeff" + output.getvalue()).encode("utf-8")


@router.get("/daily-failures", response_model=list[DailyFailureListResponse])
async def list_daily_failures(
    db: DbSession,
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD（北京时间），不传则查全部"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD（北京时间），不传则查全部"),
    workflow_name: str | None = Query(None, description="按 workflow 名称筛选"),
    processing_status: str | None = Query(None, description="按处理状态筛选: 未处理/处理中/已修复/已关闭"),
    notes_search: str | None = Query(None, description="按备注文本模糊搜索"),
):
    """查询每日失败记录（从物化表 daily_failure_records 直查）"""
    stmt = _daily_failure_query(
        start_date=start_date,
        end_date=end_date,
        workflow_name=workflow_name,
        processing_status=processing_status,
        notes_search=notes_search,
    ).order_by(
        DailyFailureRecord.report_date.desc(),
        DailyFailureRecord.workflow_name.asc(),
        DailyFailureRecord.job_name.asc(),
    )
    if not start_date and not end_date:
        stmt = stmt.limit(10000)

    result = await db.execute(stmt)
    records = result.scalars().all()

    # 按日期分组
    grouped: dict[str, list[DailyFailureJob]] = {}
    for rec in records:
        date_str = str(rec.report_date)
        grouped.setdefault(date_str, []).append(DailyFailureJob(
            id=rec.id,
            job_id=rec.job_id or 0,
            run_id=rec.run_id,
            workflow_name=rec.workflow_name,
            job_name=rec.job_name,
            conclusion=rec.conclusion,
            started_at=rec.started_at,
            completed_at=rec.completed_at,
            duration_seconds=rec.duration_seconds,
            hardware=rec.hardware,
            owner=rec.owner,
            owner_email=None,
            display_name=rec.display_name,
            test_model=rec.test_model,
            model_fo=rec.model_fo,
            deployment_type=rec.deployment_type,
            processing_time=rec.processing_time,
            closure_time=rec.closure_time,
            processing_status=rec.processing_status or "未处理",
            problem_category=rec.problem_category,
            related_pr=rec.related_pr,
            notes=rec.notes,
            updated_by=rec.updated_by,
            status_updated_at=rec.status_updated_at,
            github_job_url=rec.github_job_url,
        ))

    response: list[DailyFailureListResponse] = []
    for date_str in sorted(grouped.keys(), reverse=True):
        jobs = grouped[date_str]
        response.append(DailyFailureListResponse(
            date=date_str,
            stats=DailyFailureStats(
                date=date_str,
                total_failed_jobs=len(jobs),
                cancelled=sum(1 for j in jobs if j.conclusion == "cancelled"),
                unprocessed=sum(1 for j in jobs if j.processing_status == "未处理"),
                processing=sum(1 for j in jobs if j.processing_status == "处理中"),
                fixed=sum(1 for j in jobs if j.processing_status == "已修复"),
                closed=sum(1 for j in jobs if j.processing_status == "已关闭"),
            ),
            jobs=jobs,
        ))

    return response


@router.get("/daily-failures/export", summary="导出每日失败追踪数据")
async def export_daily_failures(
    _current_user: CurrentUser,
    db: DbSession,
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD（北京时间）"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD（北京时间）"),
    workflow_name: str | None = Query(None, description="按 workflow 名称筛选"),
    processing_status: str | None = Query(None, description="按处理状态筛选"),
    notes_search: str | None = Query(None, description="按备注文本模糊搜索"),
):
    """按页面当前筛选条件导出全部匹配记录，不受列表默认上限影响。"""
    stmt = _daily_failure_query(
        start_date=start_date,
        end_date=end_date,
        workflow_name=workflow_name,
        processing_status=processing_status,
        notes_search=notes_search,
    ).order_by(
        DailyFailureRecord.report_date.desc(),
        DailyFailureRecord.workflow_name.asc(),
        DailyFailureRecord.job_name.asc(),
    )
    result = await db.execute(stmt)
    records = list(result.scalars().all())
    filename = f"daily_failures_{start_date or 'all'}_{end_date or 'all'}.csv"
    return Response(
        content=_serialize_daily_failures_csv(records),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Row-Count": str(len(records)),
        },
    )


@router.put("/daily-failures/{record_id}/status", response_model=DailyFailureJob)
async def update_failure_status(
    record_id: int,
    update: DailyFailureUpdateRequest,
    current_user: CurrentUser,
    db: DbSession,
):
    """更新失败记录的责任人、处理状态和处理信息（责任人 + 管理员可操作）"""
    stmt = select(DailyFailureRecord).where(DailyFailureRecord.id == record_id)
    result = await db.execute(stmt)
    rec = result.scalar_one_or_none()

    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记录不存在")

    # 权限检查：责任人 或 admin/super_admin
    if current_user.role not in ("admin", "super_admin"):
        if not rec.owner or rec.owner != current_user.username:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有责任人、管理员或超级管理员可以更新处理状态",
            )

    rec.processing_status = update.processing_status
    if "owner" in update.model_fields_set:
        rec.owner = update.owner.strip() if update.owner and update.owner.strip() else None
    rec.problem_category = update.problem_category
    rec.related_pr = update.related_pr
    rec.notes = update.notes
    rec.processing_time = update.processing_time
    rec.closure_time = update.closure_time
    rec.updated_by = current_user.username
    rec.status_updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(rec)

    return DailyFailureJob(
        id=rec.id,
        job_id=rec.job_id or 0,
        run_id=rec.run_id,
        workflow_name=rec.workflow_name,
        job_name=rec.job_name,
        conclusion=rec.conclusion,
        started_at=rec.started_at,
        completed_at=rec.completed_at,
        duration_seconds=rec.duration_seconds,
        hardware=rec.hardware,
        owner=rec.owner,
        owner_email=None,
        display_name=rec.display_name,
        test_model=rec.test_model,
        model_fo=rec.model_fo,
        deployment_type=rec.deployment_type,
        processing_time=rec.processing_time,
        closure_time=rec.closure_time,
        processing_status=rec.processing_status or "未处理",
        problem_category=rec.problem_category,
        related_pr=rec.related_pr,
        notes=rec.notes,
        updated_by=rec.updated_by,
        status_updated_at=rec.status_updated_at,
        github_job_url=rec.github_job_url,
    )


@router.put("/daily-failures/batch-status")
async def batch_update_failure_status(
    update: DailyFailureBatchUpdateRequest,
    current_user: CurrentUser,
    db: DbSession,
    ids: list[int] = Query(..., description="要更新的每日失败记录 ID"),
):
    """批量更新失败记录中明确提交的字段。"""
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids 不能为空")
    if len(ids) > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="单次最多 200 条")
    update_fields = update.model_fields_set
    if not update_fields:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="至少选择一个更新字段")

    stmt = select(DailyFailureRecord).where(DailyFailureRecord.id.in_(ids))
    result = await db.execute(stmt)
    records = result.scalars().all()

    if len(records) != len(ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="部分记录不存在")

    now = datetime.now(UTC)
    for rec in records:
        if current_user.role not in ("admin", "super_admin"):
            if not rec.owner or rec.owner != current_user.username:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"没有权限更新 {rec.workflow_name}/{rec.job_name}（责任人：{rec.owner or '未配置'}）",
                )
        if "processing_status" in update_fields:
            rec.processing_status = update.processing_status
        if "owner" in update_fields:
            rec.owner = update.owner.strip() if update.owner and update.owner.strip() else None
        if "problem_category" in update_fields:
            rec.problem_category = update.problem_category
        if "related_pr" in update_fields:
            rec.related_pr = update.related_pr
        if "notes" in update_fields:
            rec.notes = update.notes
        if "processing_time" in update_fields:
            rec.processing_time = update.processing_time
        if "closure_time" in update_fields:
            rec.closure_time = update.closure_time
        rec.updated_by = current_user.username
        rec.status_updated_at = now

    await db.commit()
    return {"message": f"已更新 {len(records)} 条记录", "count": len(records)}


# ============ Nightly Gantt ============

_NIGHTLY_GANTT_CACHE: dict[tuple[int, str], tuple[float, dict]] = {}
_NIGHTLY_GANTT_CACHE_TTL = 600  # 10 分钟，降低 GitHub API 配额消耗


@router.get("/nightly-gantt/{run_number}", response_model=NightlyGanttResponse)
async def get_nightly_gantt(
    run_number: int,
    db: DbSession,
    hardware: str = Query("a3", description="硬件类型：a2 或 a3，默认 a3"),
):
    """获取 nightly 测试用例执行甘特图数据

    按 run_number 定位 workflow run（优先查数据库 CIResult，未命中回退 GitHub API 翻页），
    拉取该 run 的全部 jobs，过滤基础设施 Job 并按 Multi-node/Double-node/Single-node 分类，
    返回北京时间起止时间、耗时、状态等结构化数据，供前端渲染甘特图。

    数据来源：GitHub Actions API（vllm-project/vllm-ascend 仓库的 nightly workflow）。
    结果按 (run_number, hardware) 缓存 10 分钟，降低 GitHub API 配额消耗。
    """
    from infrastructure.clients.github_client import GitHubClient
    from nightly_gantt.nightly_gantt_service import NightlyGanttService

    cache_key = (run_number, hardware)
    now_ts = datetime.now(UTC).timestamp()
    cached = _NIGHTLY_GANTT_CACHE.get(cache_key)
    if cached is not None and (now_ts - cached[0]) < _NIGHTLY_GANTT_CACHE_TTL:
        return cached[1]

    if not settings.GITHUB_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GITHUB_TOKEN not configured",
        )

    client = GitHubClient(
        token=settings.GITHUB_TOKEN,
        owner=settings.GITHUB_OWNER,
        repo=settings.GITHUB_REPO,
    )
    svc = NightlyGanttService(db=db, github_client=client)
    try:
        data = await svc.get_gantt_data(run_number=run_number, hardware=hardware)
        _NIGHTLY_GANTT_CACHE[cache_key] = (now_ts, data)
        return data
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to build nightly gantt for run #{run_number}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build nightly gantt: {str(e)}",
        ) from None
    finally:
        await client.close()


# ============ Nightly Test Case CRUD ============

@router.get("/nightly-test-cases", response_model=list[NightlyTestCaseResponse])
async def list_nightly_test_cases(
    db: DbSession,
    report_date: str | None = Query(None, description="快照日期 YYYY-MM-DD，默认最新"),
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD（含）"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD（含）"),
    source_branch: str | None = Query(None, description="来源分支，默认 main"),
    workflow_name: str | None = Query(None, description="按 workflow 筛选"),
    enabled: bool | None = Query(None, description="按启用状态筛选"),
):
    """列出 Nightly 用例，支持按日期和分支筛选"""
    stmt = select(NightlyTestCase).order_by(
        NightlyTestCase.report_date.desc(),
        NightlyTestCase.workflow_name,
        NightlyTestCase.job_name,
    )
    if report_date and (start_date or end_date):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report_date 不能与 start_date/end_date 同时使用",
        )
    if report_date:
        stmt = stmt.where(NightlyTestCase.report_date == report_date)
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date 格式须为 YYYY-MM-DD")
        stmt = stmt.where(NightlyTestCase.report_date >= start_date)
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date 格式须为 YYYY-MM-DD")
        stmt = stmt.where(NightlyTestCase.report_date <= end_date)
    if source_branch:
        stmt = stmt.where(NightlyTestCase.source_branch == source_branch)
    if workflow_name:
        stmt = stmt.where(NightlyTestCase.workflow_name == workflow_name)
    if enabled is not None:
        stmt = stmt.where(NightlyTestCase.enabled == enabled)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/nightly-test-cases/export")
async def export_nightly_test_cases(
    current_user: CurrentAdminUser,
    db: DbSession,
    report_date: str | None = Query(None, description="快照日期 YYYY-MM-DD"),
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD（含）"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD（含）"),
    source_branch: str | None = Query(None, description="来源分支"),
    workflow_name: str | None = Query(None, description="按 workflow 筛选"),
    enabled: bool | None = Query(None, description="按启用状态筛选"),
):
    """Download filtered Nightly cases as an import-compatible JSON file."""
    del current_user  # authorization is enforced by CurrentAdminUser

    stmt = select(NightlyTestCase).order_by(
        NightlyTestCase.report_date.desc(),
        NightlyTestCase.workflow_name,
        NightlyTestCase.job_name,
    )
    if report_date and (start_date or end_date):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report_date 不能与 start_date/end_date 同时使用",
        )
    if report_date:
        try:
            datetime.strptime(report_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="report_date 格式须为 YYYY-MM-DD",
            ) from None
        stmt = stmt.where(NightlyTestCase.report_date == report_date)
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date 格式须为 YYYY-MM-DD",
            ) from None
        stmt = stmt.where(NightlyTestCase.report_date >= start_date)
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date 格式须为 YYYY-MM-DD",
            ) from None
        stmt = stmt.where(NightlyTestCase.report_date <= end_date)
    if source_branch:
        stmt = stmt.where(NightlyTestCase.source_branch == source_branch)
    if workflow_name:
        stmt = stmt.where(NightlyTestCase.workflow_name == workflow_name)
    if enabled is not None:
        stmt = stmt.where(NightlyTestCase.enabled == enabled)

    result = await db.execute(stmt)
    records = result.scalars().all()
    payload = [_serialize_nightly_test_case_export(record) for record in records]
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"nightly_test_cases_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/nightly-test-cases/run-export")
async def export_nightly_test_case_runs(
    current_user: CurrentAdminUser,
    db: DbSession,
    format: str = Query("json", pattern="^(json|xlsx)$"),
    scope: str = Query("filtered", pattern="^(filtered|all)$"),
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD（含）"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD（含）"),
    source_branch: str | None = Query(None, description="来源分支"),
    workflow_names: list[str] | None = Query(None, description="可重复传入的 Workflow 名称"),
):
    """Export executed Nightly cases, enriched from snapshots and failure analysis.

    The legacy endpoint above deliberately remains an import-compatible
    configuration snapshot export.  This endpoint is for the user-facing
    run-history report, so it can include GitHub links and persisted AI
    analysis without changing any database table.
    """
    del current_user
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date 格式须为 YYYY-MM-DD") from None
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date 格式须为 YYYY-MM-DD") from None

    snapshots_stmt = select(NightlyTestCase).order_by(NightlyTestCase.report_date.desc())
    if scope == "filtered":
        if start_date:
            snapshots_stmt = snapshots_stmt.where(NightlyTestCase.report_date >= start_date)
        if end_date:
            snapshots_stmt = snapshots_stmt.where(NightlyTestCase.report_date <= end_date)
        if source_branch:
            snapshots_stmt = snapshots_stmt.where(NightlyTestCase.source_branch == source_branch)
        if workflow_names:
            snapshots_stmt = snapshots_stmt.where(NightlyTestCase.workflow_name.in_(workflow_names))
    snapshots = list((await db.execute(snapshots_stmt)).scalars().all())
    if not snapshots:
        rows: list[dict[str, str]] = []
    else:
        workflows = {item.workflow_name for item in snapshots}
        jobs_stmt = (
            select(CIJob, CIResult)
            .join(CIResult, CIResult.run_id == CIJob.run_id)
            .where(CIJob.workflow_name.in_(workflows))
            .order_by(CIResult.started_at.asc(), CIJob.started_at.asc(), CIJob.id.asc())
            .limit(10_000)
        )
        if scope == "filtered" and start_date:
            jobs_stmt = jobs_stmt.where(CIResult.started_at >= datetime.fromisoformat(start_date))
        if scope == "filtered" and end_date:
            jobs_stmt = jobs_stmt.where(CIResult.started_at < datetime.fromisoformat(end_date) + timedelta(days=1))

        jobs_and_runs = (await db.execute(jobs_stmt)).all()
        snapshots_by_workflow: dict[str, list[NightlyTestCase]] = {}
        for snapshot in snapshots:
            snapshots_by_workflow.setdefault(snapshot.workflow_name, []).append(snapshot)

        matched: list[tuple[CIJob, CIResult, NightlyTestCase]] = []
        for job, run in jobs_and_runs:
            if not is_current_run_attempt(job.data, extract_run_attempt(run.data)):
                continue
            branch = _nightly_export_branch(job, run)
            branch_aliases = {branch, branch.replace("releases-", "releases/", 1)}
            candidates = snapshots_by_workflow.get(job.workflow_name, [])
            candidate = next(
                (
                    item
                    for item in candidates
                    if item.source_branch in branch_aliases and _nightly_snapshot_matches_job(item, job)
                ),
                None,
            )
            if candidate is not None:
                matched.append((job, run, candidate))

        analysis_by_job_id: dict[int, JobFailureAnalysis] = {}
        job_ids = [job.job_id for job, _, _ in matched if job.job_id is not None]
        if job_ids:
            analyses = (
                await db.execute(select(JobFailureAnalysis).where(JobFailureAnalysis.job_id.in_(job_ids)))
            ).scalars().all()
            analysis_by_job_id = {analysis.job_id: analysis for analysis in analyses}

        ai_analysis_by_job_id: dict[int, str] = {}
        file_store = FailureAnalysisFileStore()
        for job_id, analysis in analysis_by_job_id.items():
            report = None
            if analysis.report_file_path:
                report = await file_store.read_report_by_path(analysis.report_file_path)
            ai_analysis_by_job_id[job_id] = report or _format_nightly_ai_analysis(analysis)

        def coverage_label(job: CIJob, snapshot: NightlyTestCase) -> str:
            deployment = snapshot.deployment_type or ""
            deployment = deployment.replace("multi-node-2", "双机").replace("single-node", "单机")
            return " / ".join(part for part in (job.hardware, deployment) if part)

        rows = []
        for job, run, snapshot in matched:
            rows.append({
                "time": _format_nightly_export_time(run.started_at or job.started_at),
                "test_content": job.job_name,
                "model": snapshot.test_model or "",
                "coverage": coverage_label(job, snapshot),
                # CIJob.logs_url is GitHub's authenticated API download URL.
                # Exports must use the browser-facing Actions Job page instead.
                "log_url": (
                    f"https://github.com/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
                    f"/actions/runs/{job.run_id}/job/{job.job_id}"
                    if job.job_id else ""
                ),
                "model_fo": snapshot.model_fo or "",
                "ai_analysis": ai_analysis_by_job_id.get(job.job_id, ""),
            }
            )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if format == "xlsx":
        return Response(
            content=_build_nightly_runs_xlsx(rows),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="nightly_run_details_{timestamp}.xlsx"'},
        )
    content = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="nightly_run_details_{timestamp}.json"'},
    )


@router.post("/nightly-test-cases", response_model=NightlyTestCaseResponse, status_code=status.HTTP_201_CREATED)
async def create_nightly_test_case(
    data: NightlyTestCaseCreate,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """创建 Nightly 用例（需 admin）"""
    existing = await db.execute(
        select(NightlyTestCase).where(
            NightlyTestCase.workflow_name == data.workflow_name,
            NightlyTestCase.job_name == data.job_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该用例已存在")
    tc = NightlyTestCase(**data.model_dump())
    db.add(tc)
    if data.model_fo:
        await set_model_fo_mapping(db, data.test_model, data.model_fo)
    await db.commit()
    await db.refresh(tc)
    return tc


@router.put("/nightly-test-cases/{tc_id}", response_model=NightlyTestCaseResponse)
async def update_nightly_test_case(
    tc_id: int,
    data: NightlyTestCaseUpdate,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """更新 Nightly 用例（需 admin）"""
    stmt = select(NightlyTestCase).where(NightlyTestCase.id == tc_id)
    result = await db.execute(stmt)
    tc = result.scalar_one_or_none()
    if not tc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(tc, field, value)
    if "model_fo" in changes or "test_model" in changes:
        await set_model_fo_mapping(
            db,
            changes.get("test_model", tc.test_model),
            changes.get("model_fo", tc.model_fo),
        )
    await db.commit()
    await db.refresh(tc)
    return tc


@router.delete("/nightly-test-cases/{tc_id}")
async def delete_nightly_test_case(
    tc_id: int,
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """删除 Nightly 用例（需 admin）"""
    stmt = select(NightlyTestCase).where(NightlyTestCase.id == tc_id)
    result = await db.execute(stmt)
    tc = result.scalar_one_or_none()
    if not tc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用例不存在")
    await db.delete(tc)
    await db.commit()
    return {"message": "已删除"}


@router.post("/nightly-test-cases/sync-from-yaml")
async def sync_test_cases_from_yaml(
    current_user: CurrentAdminUser,
    db: DbSession,
):
    """从本地 vllm-ascend 仓库的 nightly YAML 配置同步用例到静态表"""
    from infrastructure.clients.github_cache import get_github_cache_for_repo
    from infrastructure.core.config import settings
    from tooling.parsers.nightly_config_parser import (
        CONFIG_PATH,
        NightlyConfigParser,
        load_model_fo_map,
    )

    seed_count = await seed_missing_model_fo_mappings(db, load_model_fo_map())
    fo_map = await load_model_fo_mappings(db)
    cache = get_github_cache_for_repo(settings.GITHUB_OWNER, settings.GITHUB_REPO)
    if not cache._is_repo_cloned():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "vllm-ascend Git 镜像尚未就绪，请先在系统配置中触发 "
                "Git 缓存同步，并等待 Collector 完成"
            ),
        )
    parser = NightlyConfigParser(repo_path=str(cache.cache_dir))
    info = parser.get_repo_info()

    if not info["available"]:
        info["nightly_dir"] = info.get("config_file", "")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"vllm-ascend 仓库未找到，nightly_dir={info['nightly_dir']}。请设置 VLLM_ASCEND_REPO_PATH 环境变量"
        )

    from datetime import date as date_type

    today = str(date_type.today())

    # 先做快照：从各活跃分支拉取 YAML
    branches = cache.get_remote_branches("releases/")
    target_branches = ["main"] + [b for b in branches if b.startswith("releases/")]
    total_created = 0
    total_updated = 0

    for branch in target_branches:
        content = cache.get_file_content(CONFIG_PATH, branch)
        if content is None:
            continue
        cases = NightlyConfigParser.parse_content(
            content, report_date=today, source_branch=branch
        )
        if not cases:
            continue

        for c in cases:
            # 检查当天该分支是否已有
            existing = await db.execute(
                select(NightlyTestCase).where(
                    NightlyTestCase.report_date == today,
                    NightlyTestCase.source_branch == branch,
                    NightlyTestCase.workflow_name == c.workflow,
                    NightlyTestCase.job_name == c.name,
                )
            )
            existing = existing.scalar_one_or_none()

            if existing:
                existing.test_model = c.model_path
                existing.deployment_type = c.deployment
                total_updated += 1
            else:
                db.add(NightlyTestCase(
                    report_date=today,
                    source_branch=branch,
                    workflow_name=c.workflow,
                    job_name=c.name,
                    display_name=c.name,
                    test_model=c.model_path,
                    model_fo=lookup_model_fo(fo_map, c.model_path),
                    deployment_type=c.deployment,
                ))
                total_created += 1

    if total_created > 0 or total_updated > 0 or seed_count:
        await db.commit()

    return {
        "message": f"YAML 同步完成（{today}，{len(target_branches)} 个分支）",
        "repo_info": info,
        "branches": target_branches,
        "created": total_created,
        "updated": total_updated,
    }
