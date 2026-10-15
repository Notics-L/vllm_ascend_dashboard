"""Stable GitHub Actions Run presentation metadata shared by NPU read models."""
from __future__ import annotations

import json
import re
from typing import Any

_PR_REF = re.compile(r"refs/pull/(\d+)/merge(?:$|[/?])")
_HEAD_REF = re.compile(r"refs/heads/([^@/?]+)")


def parse_workflow_ref(workflow_ref: str | None) -> tuple[int | None, str | None]:
    """Return the PR number or branch scope encoded by a GitHub workflow ref."""
    if not workflow_ref:
        return None, None
    if match := _PR_REF.search(workflow_ref):
        return int(match.group(1)), None
    if match := _HEAD_REF.search(workflow_ref):
        return None, f"branch/{match.group(1)}"
    return None, None


def workflow_name_from_ref(workflow_ref: str | None) -> str | None:
    filename = workflow_file_from_ref(workflow_ref)
    return filename.rsplit(".", 1)[0] if filename else None


def workflow_file_from_ref(workflow_ref: str | None) -> str | None:
    if not workflow_ref or ".github/workflows/" not in workflow_ref:
        return None
    return workflow_ref.split(".github/workflows/", 1)[1].split("@", 1)[0].rsplit("/", 1)[-1] or None


def _raw_run(result: Any | None) -> dict[str, Any]:
    if result is None or not getattr(result, "data", None):
        return {}
    try:
        payload = json.loads(result.data)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_run_metadata(
    *,
    organization: str | None,
    repository: str | None,
    workflow_run_id: int | None,
    workflow_ref: str | None,
    job_display_name: str | None,
    ci_result: Any | None = None,
    pr_number: int | None = None,
    fallback_workflow_name: str | None = None,
    fallback_display_title: str | None = None,
    workflow_config_match: bool = False,
) -> dict[str, Any]:
    """Build API-safe Run metadata without making external GitHub requests."""
    raw_run = _raw_run(ci_result)
    raw_repository = raw_run.get("repository") if isinstance(raw_run.get("repository"), dict) else {}
    full_name = raw_repository.get("full_name")
    if full_name and "/" in str(full_name):
        inferred_organization, inferred_repository = str(full_name).split("/", 1)
        organization = organization or inferred_organization
        repository = repository or inferred_repository
    owner = raw_repository.get("owner") if isinstance(raw_repository.get("owner"), dict) else {}
    organization = organization or owner.get("login")
    repository = repository or raw_repository.get("name")
    parsed_pr, trigger_scope = parse_workflow_ref(workflow_ref)
    pr_number = pr_number if pr_number is not None else parsed_pr
    # `CIResult.workflow_name` is normalized by the collector and can have
    # originated from a workflow configuration.  The GitHub workflow-run
    # payload is the authoritative metadata for this exact run, so take its
    # `name` first; only then use the persisted normalized value and the
    # lower-priority local fallbacks.
    workflow_name = raw_run.get("name") or getattr(ci_result, "workflow_name", None) or fallback_workflow_name
    display_title = raw_run.get("display_title") or getattr(ci_result, "display_title", None) or fallback_display_title
    run_name = raw_run.get("name")
    run_number = getattr(ci_result, "run_number", None) or raw_run.get("run_number")
    # Keep the machine fields separate.  A display title is a PR/run title,
    # never a workflow name.
    workflow_file = workflow_file_from_ref(workflow_ref)
    base_name = display_title or workflow_name or run_name
    if base_name and run_number and not re.search(r"#\d+", str(base_name)):
        suffix = f"#{run_number}"
        run_display_name = base_name if str(base_name).rstrip().endswith(suffix) else f"{base_name} {suffix}"
    else:
        run_display_name = base_name or (str(workflow_run_id) if workflow_run_id else job_display_name or "未知 Run")
    event = getattr(ci_result, "event", None) or raw_run.get("event")
    run_url = raw_run.get("html_url") or getattr(ci_result, "html_url", None) or (
        f"https://github.com/{organization}/{repository}/actions/runs/{workflow_run_id}"
        if organization and repository and workflow_run_id else None
    )
    return {
        "workflow_run_id": workflow_run_id,
        "workflow_name": workflow_name,
        "workflow_file": workflow_file,
        "run_display_title": display_title,
        "run_number": run_number,
        "run_display_name": str(run_display_name),
        "workflow_ref": workflow_ref,
        "pr_number": pr_number,
        "event": event,
        "trigger_scope": trigger_scope,
        "run_url": run_url,
        "github_url": run_url,
        "workflow_config_match": workflow_config_match,
    }
