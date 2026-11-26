"""Record authenticated calls to the GitHub API at the HTTP client boundary."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import subprocess
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Literal

import httpx

from infrastructure.core.github_audit import (
    GitHubAuditAttempt,
    GitHubAuditUnavailable,
    begin_github_operation,
    finish_github_operation,
    wait_for_github_audit,
)
from infrastructure.core.github_credentials import GitHubCredential

GITHUB_API_URL = "https://api.github.com"
logger = logging.getLogger(__name__)
_TOKEN = re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", re.IGNORECASE)
_REPOSITORY = re.compile(r"/repos?/([A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100})(?:/|$)")
_REQUEST_ID = re.compile(r"[A-Fa-f0-9:]{1,64}")
_CURRENT_REQUEST: ContextVar[tuple[httpx.Request, GitHubAuditAttempt] | None] = ContextVar(
    "github_http_request", default=None
)


def _redact(value: str, token: str) -> str:
    return _TOKEN.sub("[redacted]", value.replace(token, "[redacted]"))


def _is_github_api(url: httpx.URL) -> bool:
    return (
        url.scheme == "https" and url.host == "api.github.com" and url.port in (None, 443)
        and not (url.username or url.password)
    )


async def _finish_github_http(
    request: httpx.Request,
    attempt: GitHubAuditAttempt,
    *,
    response: httpx.Response | None = None,
    error: BaseException | None = None,
) -> None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    status = response.status_code if response is not None else None
    request_id = response.headers.get("x-github-request-id", "") if response is not None else ""
    result: Literal["success", "redirect", "failed"] = "failed"
    error_type = None
    if status is not None:
        if 200 <= status < 300 or status == 304:
            result = "success"
        elif 300 <= status < 400:
            result = "redirect"
    elif isinstance(error, (httpx.TimeoutException, TimeoutError)):
        error_type = "timeout"
    elif isinstance(error, asyncio.CancelledError):
        error_type = "cancelled"
    else:
        error_type = "network"

    await finish_github_operation(
        attempt,
        result=result,
        http_status=status,
        error_type=error_type,
        github_request_id=request_id if _REQUEST_ID.fullmatch(request_id) and token not in request_id else None,
    )


class GitHubAsyncClient(httpx.AsyncClient):
    def __init__(self, *, audit_source: str, credential: GitHubCredential, **kwargs):
        if kwargs.get("verify", True) is not True:
            raise ValueError("GitHub API requests require TLS certificate verification")
        kwargs.setdefault("base_url", GITHUB_API_URL)
        super().__init__(**kwargs)
        self.audit_source = audit_source
        self._credential = credential
        self.event_hooks["request"].append(self._request_started)
        self.event_hooks["response"].insert(0, self._response_received)

    async def _request_started(self, request: httpx.Request) -> None:
        request.headers.pop("Authorization", None)
        if self._credential and _is_github_api(request.url):
            self._credential.validate()
            endpoint = _redact(request.url.path, self._credential._github_token)
            repo_match = _REPOSITORY.match(endpoint)
            attempt = await begin_github_operation(
                source=self.audit_source,
                credential_source=self._credential.source,
                method=request.method[:12],
                endpoint=endpoint[:512],
                repository=repo_match[1] if repo_match else None,
            )
            _CURRENT_REQUEST.set((request, attempt))
            request.headers["Authorization"] = f"Bearer {self._credential._github_token}"

    async def _response_received(self, response: httpx.Response) -> None:
        current = _CURRENT_REQUEST.get()
        _CURRENT_REQUEST.set(None)
        if current is not None:
            request, attempt = current
            try:
                await _finish_github_http(request, attempt, response=response)
            finally:
                request.headers.pop("Authorization", None)

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        context = _CURRENT_REQUEST.set(None)
        try:
            return await super().send(request, **kwargs)
        except (httpx.RequestError, asyncio.CancelledError) as exc:
            if (current := _CURRENT_REQUEST.get()) is not None:
                await _finish_github_http(*current, error=exc)
            if isinstance(exc, httpx.RequestError):
                # Transport messages may contain headers or signed URLs.
                raise type(exc)(f"GitHub request failed ({type(exc).__name__})", request=request) from None
            raise
        finally:
            if (current := _CURRENT_REQUEST.get()) is not None:
                current[0].headers.pop("Authorization", None)
            _CURRENT_REQUEST.reset(context)


@contextmanager
def _audit_github_git(source: str, repository: str, credential: GitHubCredential, command: str):
    target = _redact(repository, credential._github_token)
    attempt = wait_for_github_audit(begin_github_operation(
        source=source,
        credential_source=credential.source,
        method=command.upper(),
        endpoint=f"/{target}.git"[:512],
        repository=target if _REPOSITORY.fullmatch(f"/repos/{target}") else None,
    ))
    result: Literal["success", "failed"] = "success"
    error = None
    try:
        yield
    except subprocess.TimeoutExpired:
        result, error = "failed", "timeout"
        raise
    except (subprocess.CalledProcessError, OSError):
        result, error = "failed", "git"
        raise
    finally:
        try:
            wait_for_github_audit(finish_github_operation(attempt, result=result, error_type=error))
        except GitHubAuditUnavailable:
            logger.error("Could not finalize GitHub Git audit record %s", attempt.row_id)


def github_git_environment() -> dict[str, str]:
    """Start Git without inherited GitHub tokens or credential helpers."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("GIT_", "GH_", "GITHUB_"))
    }
    env.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="credential.helper",
        GIT_CONFIG_VALUE_0="",
        GIT_CONFIG_KEY_1="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_1="",
    )
    return env


def run_github_git(
    credential: GitHubCredential, repository: str, *args: str,
    audit_source: str, cwd: str, name: str, email: str, stdin: str | None = None,
) -> str:
    command = args[0]
    remote = command in {"fetch", "ls-remote", "push"}
    env = github_git_environment()
    env["LC_ALL"] = "C"
    audit = nullcontext()
    if remote:
        if not _REPOSITORY.fullmatch(f"/repos/{repository}") or f"https://github.com/{repository}.git" not in args:
            raise ValueError("GitHub remote must match the configured repository")
        if credential:
            credential.validate()
            auth = base64.b64encode(f"x-access-token:{credential._github_token}".encode()).decode()
            env["GIT_CONFIG_VALUE_1"] = f"Authorization: Basic {auth}"
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
            audit = _audit_github_git(audit_source, repository, credential, command)
    with audit:
        return subprocess.run(
            ["git", "-c", "credential.helper=", "-c", "http.followRedirects=false",
             "-c", f"user.name={name}", "-c", f"user.email={email}", *args],
            cwd=cwd, env=env, input=stdin, encoding="utf-8", errors="replace",
            capture_output=True, check=True, timeout=120,
        ).stdout
