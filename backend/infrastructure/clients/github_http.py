"""Record authenticated calls to the GitHub API at the HTTP client boundary."""

from __future__ import annotations

import asyncio
import re
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

import aiohttp
import httpx

from infrastructure.core.github_audit import CredentialSource, record_github_operation

_TOKEN = re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", re.IGNORECASE)
_REPOSITORY = re.compile(r"/repos?/([A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100})(?:/|$)")
_REQUEST_ID = re.compile(r"[A-Fa-f0-9:]{1,64}")
_CURRENT_REQUEST: ContextVar[tuple[httpx.Request, float] | None] = ContextVar(
    "github_http_request", default=None
)


def _redact(value: str, token: str) -> str:
    return _TOKEN.sub("[redacted]", value.replace(token, "[redacted]"))


def record_github_http(
    source: str,
    credential_source: CredentialSource,
    request: httpx.Request,
    started: float,
    *,
    response: httpx.Response | None = None,
    error: BaseException | None = None,
) -> None:
    url = request.url
    if url.scheme != "https" or url.host != "api.github.com" or url.port not in (None, 443):
        return
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() not in {"bearer", "token"} or not token:
        return

    endpoint = _redact(url.path, token)
    repo_match = _REPOSITORY.match(endpoint)
    status = response.status_code if response is not None else None
    request_id = response.headers.get("x-github-request-id", "") if response is not None else ""
    result = "failed"
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

    record_github_operation(
        source=source,
        credential_source=credential_source,
        method=request.method[:12],
        endpoint=endpoint[:512],
        repository=repo_match[1] if repo_match else None,
        started=started,
        result=result,
        http_status=status,
        error_type=error_type,
        github_request_id=request_id if _REQUEST_ID.fullmatch(request_id) and token not in request_id else None,
    )


def _request_started(request: httpx.Request) -> None:
    _CURRENT_REQUEST.set((request, monotonic()))


def _response_received(source: str, credential_source: CredentialSource, response: httpx.Response) -> None:
    current = _CURRENT_REQUEST.get()
    _CURRENT_REQUEST.set(None)
    if current is not None:
        record_github_http(source, credential_source, current[0], current[1], response=response)


@contextmanager
def _track_request(source: str, credential_source: CredentialSource):
    context = _CURRENT_REQUEST.set(None)
    try:
        yield
    except (httpx.RequestError, asyncio.CancelledError) as exc:
        if (current := _CURRENT_REQUEST.get()) is not None:
            record_github_http(source, credential_source, current[0], current[1], error=exc)
        raise
    finally:
        _CURRENT_REQUEST.reset(context)


class GitHubAsyncClient(httpx.AsyncClient):
    def __init__(self, *, audit_source: str, credential_source: CredentialSource, **kwargs):
        super().__init__(**kwargs)
        self.audit_source = audit_source
        self.credential_source = credential_source
        self.event_hooks["request"].insert(0, self._request_started)
        self.event_hooks["response"].insert(0, self._response_received)

    async def _request_started(self, request: httpx.Request) -> None:
        _request_started(request)

    async def _response_received(self, response: httpx.Response) -> None:
        _response_received(self.audit_source, self.credential_source, response)

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        with _track_request(self.audit_source, self.credential_source):
            return await super().send(request, **kwargs)


def github_aiohttp_trace(source: str, credential_source: CredentialSource) -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()

    async def start(_session, context, _params):
        context.started = monotonic()

    async def response(_session, context, params):
        request = httpx.Request(
            params.method, str(params.url), headers={"Authorization": params.headers.get("Authorization", "")}
        )
        http_response = httpx.Response(
            params.response.status,
            headers={"x-github-request-id": params.response.headers.get("x-github-request-id", "")},
        )
        record_github_http(source, credential_source, request, context.started, response=http_response)
        context.started = monotonic()

    async def failed(_session, context, params):
        request = httpx.Request(
            params.method, str(params.url), headers={"Authorization": params.headers.get("Authorization", "")}
        )
        record_github_http(source, credential_source, request, context.started, error=params.exception)

    trace.on_request_start.append(start)
    trace.on_request_redirect.append(response)
    trace.on_request_end.append(response)
    trace.on_request_exception.append(failed)
    return trace


@contextmanager
def audit_github_git(
    source: str, repository: str, token: str, command: str, *, credential_source: CredentialSource
):
    started = monotonic()
    result = "success"
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
        if token:
            target = _redact(repository, token)
            record_github_operation(
                source=source,
                credential_source=credential_source,
                method=command.upper(),
                endpoint=f"/{target}.git"[:512],
                repository=target if _REPOSITORY.fullmatch(f"/repos/{target}") else None,
                started=started,
                result=result,
                error_type=error,
            )
