"""受限 GitHub Actions 操作。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx
import yaml  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.clients.github_http import GitHubAsyncClient
from infrastructure.core.config import settings
from infrastructure.core.github_credentials import GitHubCredential
from infrastructure.persistence.models import User, WorkflowActionLog, WorkflowActionPermission
from workflow_actions.credentials import get_github_credential
from workflow_actions.schemas import WorkflowActionRequest

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key|credential|private[_-]?key)"
)
_TOKEN_VALUE_RE = re.compile(r"(?:github_pat_[A-Za-z0-9_]+|ghp_[A-Za-z0-9]+)")
logger = logging.getLogger(__name__)
_MAX_RETRY_WAIT_SECONDS = 600
_UNCERTAIN_SUBMISSION = "提交状态未确认，GitHub 可能已接收请求"
_RUN_ENDPOINTS = {
    "rerun": "rerun",
    "rerun-failed": "rerun-failed-jobs",
    "cancel": "cancel",
    "force-cancel": "force-cancel",
}


@dataclass(frozen=True)
class _UncertainSubmission:
    message: str


def parse_workflow_inputs(source: str) -> dict[str, Any]:
    if not source.strip():
        return {}
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ValueError(f"Inputs YAML 格式错误：{exc}") from exc
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ValueError("Inputs YAML 必须是参数映射")
    definitions = document.get("inputs", document)
    if not isinstance(definitions, dict):
        raise ValueError("Inputs YAML 必须是参数映射")

    inputs: dict[str, Any] = {}
    for name, definition in definitions.items():
        if _SENSITIVE_KEY_RE.search(str(name)):
            raise ValueError(f"参数 {name} 涉及敏感信息，请改用 GitHub Secrets")
        if not isinstance(definition, dict):
            raise ValueError(f"参数 {name} 必须是包含 default 的映射")
        if "default" in definition:
            value = definition["default"]
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f"参数 {name} 的 default 必须是字符串、数字或布尔值")
            inputs[str(name)] = value
    return inputs


def redact_workflow_data(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY_RE.search(key):
        return "******"
    if isinstance(value, dict):
        return {
            str(item_key): redact_workflow_data(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_workflow_data(item) for item in value]
    if isinstance(value, str):
        return _TOKEN_VALUE_RE.sub("******", value)
    return value


def _redact_text(value: str) -> str:
    return _TOKEN_VALUE_RE.sub("******", value)


def build_command(request: WorkflowActionRequest, inputs: dict[str, Any]) -> str:
    if request.action == "dispatch":
        input_keys = json.dumps(
            {"input_keys": sorted(inputs)}, ensure_ascii=False, separators=(",", ":")
        )
        return f"dispatch {request.workflow_path} {request.ref} {input_keys}"
    return f"{request.action} {request.workflow_path} {request.run_id}"


def _rate_limit_delay(response: httpx.Response, attempt: int) -> float | None:
    remaining = response.headers.get("x-ratelimit-remaining")
    retry_after = response.headers.get("retry-after")
    rate_limit_message = False
    if response.status_code == 403:
        try:
            body = response.json()
            rate_limit_message = isinstance(body, dict) and "rate limit" in str(
                body.get("message", "")
            ).lower()
        except ValueError:
            pass
    if response.status_code != 429 and not (
        response.status_code == 403
        and (retry_after is not None or remaining == "0" or rate_limit_message)
    ):
        return None

    now = datetime.now(UTC)
    delays: list[float] = []
    if retry_after is not None:
        try:
            delays.append(float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                delays.append((retry_at - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    if remaining == "0":
        try:
            delays.append(float(response.headers["x-ratelimit-reset"]) - now.timestamp())
        except (KeyError, ValueError):
            pass
    delays = [delay for delay in delays if math.isfinite(delay)]
    return max(0.0, *delays) if delays else float(60 * 2**attempt)


def _format_output(status_code: int | None, body: Any, error: str = "") -> str:
    if error:
        return f"执行失败：{_redact_text(error)}"
    if body in (None, "", {}):
        if status_code is not None and 200 <= status_code < 300:
            return f"GitHub 返回：{status_code}，请求已提交。"
        return f"GitHub 返回：{status_code}，请求失败。"
    if isinstance(body, (dict, list)):
        content = json.dumps(redact_workflow_data(body), ensure_ascii=False, indent=2)
    else:
        content = _redact_text(str(body))
    return f"GitHub 返回：{status_code}\n{content}"


async def _github_request(
    credential: GitHubCredential,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    *,
    retry_delays: tuple[float, ...] = (),
    audit_source: str = "workflow_actions",
) -> tuple[int | None, Any, str | _UncertainSubmission]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vllm-ascend-dashboard/workflow-actions",
    }
    total_wait = 0.0
    async with GitHubAsyncClient(
        audit_source=audit_source,
        credential=credential,
        headers=headers,
        timeout=30.0,
        verify=True,
    ) as client:
        for attempt in range(len(retry_delays) + 1):
            try:
                response = await client.request(method, endpoint, json=payload)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                error = str(exc) or type(exc).__name__
                if attempt == len(retry_delays) or (
                    total_wait + retry_delays[attempt] > _MAX_RETRY_WAIT_SECONDS
                ):
                    return None, None, error
                delay = retry_delays[attempt]
                logger.warning("GitHub request failed, retrying attempt %d: %s", attempt + 1, error)
            except httpx.HTTPError as exc:
                # A request that reached GitHub could already have started a workflow.
                error = str(exc) or type(exc).__name__
                if method == "POST":
                    return None, None, _UncertainSubmission(error)
                return None, None, error
            else:
                delay = _rate_limit_delay(response, attempt)
                if delay is None or attempt == len(retry_delays):
                    break
                if total_wait + delay > _MAX_RETRY_WAIT_SECONDS:
                    break
                logger.warning(
                    "GitHub request returned %d, retrying attempt %d",
                    response.status_code,
                    attempt + 1,
                )
            total_wait += delay
            await asyncio.sleep(delay)

    if not response.content:
        body: Any = None
    else:
        try:
            body = response.json()
        except ValueError:
            body = response.text
    return response.status_code, body, ""


def _result(
    status_code: int | None, body: Any, error: str | _UncertainSubmission = ""
) -> dict[str, Any]:
    uncertain = isinstance(error, _UncertainSubmission)
    if isinstance(error, _UncertainSubmission):
        error = f"{error.message}。{_UNCERTAIN_SUBMISSION}"
    success = status_code is not None and 200 <= status_code < 300 and not error
    result: dict[str, Any] = {
        "success": success,
        "status_code": status_code,
        "output": _format_output(status_code, body, error),
        "run_url": None,
    }
    if isinstance(body, dict):
        result["run_url"] = body.get("html_url") or body.get("run_url")
    if uncertain:
        result["submission_status"] = "unknown"
    return result


async def dispatch_workflow(
    credential: GitHubCredential,
    *,
    repository: str,
    workflow_path: str,
    ref: str,
    inputs: dict[str, Any],
    return_run_details: bool = False,
    retry_delays: tuple[float, ...] = (),
    audit_source: str = "workflow_actions",
) -> dict[str, Any]:
    workflow_file = quote(workflow_path.rsplit("/", 1)[-1], safe="")
    endpoint = f"/repos/{repository}/actions/workflows/{workflow_file}/dispatches"
    payload: dict[str, Any] = {"ref": ref, "inputs": inputs}
    if return_run_details:
        payload["return_run_details"] = True
    status_code, body, error = await _github_request(
        credential, "POST", endpoint, payload, retry_delays=retry_delays, audit_source=audit_source,
    )
    result = _result(status_code, body, error)
    if status_code is not None and 500 <= status_code < 600:
        result["output"] += f"\n{_UNCERTAIN_SUBMISSION}。"
        result["submission_status"] = "unknown"
    if return_run_details and result["success"] and isinstance(body, dict):
        run_id = body.get("workflow_run_id")
        if type(run_id) is int and run_id > 0:
            result["run_id"] = run_id
    return result


async def execute_workflow_action(
    db: AsyncSession,
    user: User,
    request: WorkflowActionRequest,
    *,
    require_workflow_permission: bool = True,
    log_source: str = "",
    retry_delays: tuple[float, ...] = (),
) -> dict[str, Any]:
    if require_workflow_permission:
        permission = await db.execute(
            select(WorkflowActionPermission).where(
                WorkflowActionPermission.user_id == user.id,
                WorkflowActionPermission.workflow_path == request.workflow_path,
            )
        )
        granted = permission.scalar_one_or_none()
        if granted is None or granted.workflow_path != request.workflow_path:
            raise PermissionError("无该工作流的操作权限")

    inputs = parse_workflow_inputs(request.inputs_yaml) if request.action == "dispatch" else {}
    db.add(
        WorkflowActionLog(
            username=user.username,
            action=request.action,
            workflow_path=request.workflow_path,
            command_text=f"{log_source}{build_command(request, inputs)}",
        )
    )
    await db.commit()

    try:
        token = await get_github_credential(db)
    except ValueError as exc:
        return _result(None, None, str(exc))

    repository = f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
    if request.action == "dispatch":
        return await dispatch_workflow(
            token,
            repository=repository,
            workflow_path=request.workflow_path,
            ref=request.ref,
            inputs=inputs,
            retry_delays=retry_delays,
        )

    repository = f"/repos/{repository}"
    run_result = await _github_request(token, "GET", f"{repository}/actions/runs/{request.run_id}")
    status_code, run, error = run_result
    if error or status_code != 200:
        return _result(status_code, run, error)
    if not isinstance(run, dict) or run.get("path") != request.workflow_path:
        return {
            "success": False,
            "status_code": 403,
            "output": "执行失败：该 Run 不属于所选工作流，已拒绝操作。",
            "run_url": run.get("html_url") if isinstance(run, dict) else None,
        }

    status_code, body, error = await _github_request(
        token,
        "POST",
        f"{repository}/actions/runs/{request.run_id}/{_RUN_ENDPOINTS[request.action]}",
    )
    return _result(status_code, body, error)


async def test_github_credential(db: AsyncSession) -> dict[str, Any]:
    try:
        token = await get_github_credential(db)
    except ValueError as exc:
        return _result(None, None, str(exc))
    repository = f"/repos/{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
    status_code, body, error = await _github_request(
        token, "GET", f"{repository}/actions/workflows"
    )
    result = _result(status_code, body, error)
    if result["success"]:
        result["output"] = (
            f"GitHub 返回：{status_code}，Token 有效，"
            f"可访问 {settings.GITHUB_OWNER}/{settings.GITHUB_REPO} 的 Actions。"
        )
    return result
