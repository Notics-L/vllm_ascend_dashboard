"""Batch GitHub audit records outside the caller's database transaction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

logger = logging.getLogger(__name__)

github_actor: ContextVar[str | None] = ContextVar("github_actor", default=None)
github_feature: ContextVar[str | None] = ContextVar("github_feature", default=None)
CredentialSource = Literal["global", "daily", "actions"]
_writer: _GitHubAuditWriter | None = None


class _GitHubAuditWriter:
    def __init__(self, process: str):
        self.process = process
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4096)
        self.dropped = 0
        self.accepting = True

    def enqueue(self, record: dict[str, Any]) -> None:
        if not self.accepting:
            return
        try:
            self.queue.put_nowait(record)
        except asyncio.QueueFull:
            self.dropped += 1

    async def run(self) -> None:
        from infrastructure.db.base import SessionLocal
        from infrastructure.persistence.models import GitHubOperation

        while True:
            batch = [await self.queue.get()]
            try:
                if self.accepting:
                    await asyncio.sleep(0.25)
                while len(batch) < 200 and not self.queue.empty():
                    batch.append(self.queue.get_nowait())
                async with SessionLocal() as db:
                    await db.execute(GitHubOperation.__table__.insert(), batch)
                    await db.commit()
            except Exception as exc:
                logger.warning("Failed to save %d GitHub audit records (%s)", len(batch), type(exc).__name__)
            finally:
                for _ in batch:
                    self.queue.task_done()
                if self.dropped:
                    logger.warning("GitHub audit queue was full; dropped %d records", self.dropped)
                    self.dropped = 0


@asynccontextmanager
async def github_audit_writer(process: str) -> AsyncIterator[None]:
    global _writer
    writer = _GitHubAuditWriter(process)
    _writer = writer
    task = asyncio.create_task(writer.run(), name="github-audit-writer")
    try:
        yield
    finally:
        writer.accepting = False
        _writer = None
        try:
            await asyncio.wait_for(writer.queue.join(), timeout=5)
        except TimeoutError:
            logger.warning("Timed out flushing GitHub audit records for %s", process)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def record_github_operation(
    *,
    source: str,
    credential_source: CredentialSource,
    method: str,
    endpoint: str,
    repository: str | None,
    started: float,
    result: str,
    http_status: int | None = None,
    error_type: str | None = None,
    github_request_id: str | None = None,
) -> None:
    writer = _writer
    if writer is None:
        return
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
        "result": result,
        "http_status": http_status,
        "duration_ms": round((monotonic() - started) * 1000),
        "error_type": error_type,
        "github_request_id": github_request_id,
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is writer.loop:
        writer.enqueue(record)
    else:
        try:
            writer.loop.call_soon_threadsafe(writer.enqueue, record)
        except RuntimeError:
            # The process has already closed its audit loop.
            pass
