"""Persist a GitHub operation before its credentials are used."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, TypeVar

logger = logging.getLogger(__name__)

github_actor: ContextVar[str | None] = ContextVar("github_actor", default=None)
github_feature: ContextVar[str | None] = ContextVar("github_feature", default=None)
CredentialSource = Literal["global", "daily", "actions"]
GITHUB_AUDIT_UNAVAILABLE_MESSAGE = "GitHub 审计暂不可用，请稍后重试"
_AUDIT_DB_TIMEOUT = 5
_T = TypeVar("_T")


class GitHubAuditUnavailable(RuntimeError):
    """GitHub authentication is blocked until its audit record can be committed."""


@dataclass(frozen=True, slots=True)
class _AuditWriter:
    process: str
    loop: asyncio.AbstractEventLoop


@dataclass(frozen=True, slots=True)
class GitHubAuditAttempt:
    row_id: int
    started: float


_writer: _AuditWriter | None = None


def require_github_audit() -> _AuditWriter:
    if _writer is None or not _writer.loop.is_running():
        raise GitHubAuditUnavailable("GitHub audit writer is unavailable")
    return _writer


@asynccontextmanager
async def github_audit_writer(process: str) -> AsyncIterator[None]:
    global _writer
    previous = _writer
    _writer = _AuditWriter(process, asyncio.get_running_loop())
    try:
        yield
    finally:
        _writer = previous


async def begin_github_operation(
    *,
    source: str,
    credential_source: CredentialSource,
    method: str,
    endpoint: str,
    repository: str | None,
) -> GitHubAuditAttempt:
    """Commit an independent transaction before any network I/O with a token."""
    from infrastructure.db.base import SessionLocal
    from infrastructure.persistence.models import GitHubOperation

    writer = require_github_audit()
    record = {
        "occurred_at": datetime.now(UTC).replace(tzinfo=None),
        "process": writer.process,
        "source": (github_feature.get() or source) if source in {"github_client", "github_api"} else source,
        "credential_source": credential_source,
        "actor": github_actor.get(),
        "repository": repository,
        "method": method,
        "operation_type": "read" if method in {"GET", "HEAD", "OPTIONS", "FETCH", "LS-REMOTE"} else "write",
        "endpoint": endpoint,
        "result": "pending",
        "duration_ms": 0,
    }
    try:
        async with asyncio.timeout(_AUDIT_DB_TIMEOUT), SessionLocal() as db:
            inserted = await db.execute(GitHubOperation.__table__.insert().values(record))
            row_id = inserted.lastrowid
            if row_id is None:
                raise RuntimeError("Audit insert did not return an ID")
            await db.commit()
    except Exception as exc:
        logger.error("GitHub request blocked because audit insert failed (%s)", type(exc).__name__)
        raise GitHubAuditUnavailable("GitHub audit record could not be committed") from None
    return GitHubAuditAttempt(row_id=row_id, started=monotonic())


async def finish_github_operation(
    attempt: GitHubAuditAttempt,
    *,
    result: Literal["success", "redirect", "failed"],
    http_status: int | None = None,
    error_type: str | None = None,
    github_request_id: str | None = None,
) -> None:
    """Leave the committed attempt pending if the final outcome cannot be stored."""
    from infrastructure.db.base import SessionLocal
    from infrastructure.persistence.models import GitHubOperation

    table = GitHubOperation.__table__
    update = table.update().where(table.c.id == attempt.row_id, table.c.result == "pending").values(
        result=result,
        http_status=http_status,
        duration_ms=round((monotonic() - attempt.started) * 1000),
        error_type=error_type,
        github_request_id=github_request_id,
    )
    try:
        async with asyncio.timeout(_AUDIT_DB_TIMEOUT), SessionLocal() as db:
            updated = await db.execute(update)
            if updated.rowcount != 1:
                raise RuntimeError("Audit attempt is unavailable for update")
            await db.commit()
    except Exception as exc:
        logger.error("Could not finalize GitHub audit record %s (%s)", attempt.row_id, type(exc).__name__)


def wait_for_github_audit(operation: Coroutine[Any, Any, _T]) -> _T:
    """Submit a Git command's audit write from its worker thread."""
    try:
        writer = require_github_audit()
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is writer.loop:
            raise GitHubAuditUnavailable("Authenticated Git must be run in a worker thread")
        future = asyncio.run_coroutine_threadsafe(operation, writer.loop)
    except RuntimeError:
        operation.close()
        raise
    try:
        return future.result(timeout=_AUDIT_DB_TIMEOUT + 1)
    except TimeoutError:
        future.cancel()
        raise GitHubAuditUnavailable("GitHub audit write timed out") from None
