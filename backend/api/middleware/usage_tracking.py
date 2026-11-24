import asyncio
import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from infrastructure.core.github_audit import github_actor, github_feature
from infrastructure.core.security import decode_token
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import FeatureUsageLog

logger = logging.getLogger(__name__)

_USAGE_QUEUE_MAXSIZE = 1024
_USAGE_BATCH_SIZE = 100
_USAGE_FLUSH_INTERVAL_SECONDS = 0.5
_usage_queue: asyncio.Queue[dict[str, object]] | None = None
_usage_worker_task: asyncio.Task[None] | None = None
_usage_stop_event: asyncio.Event | None = None

# The test board fans out several read-only requests when a tab is opened. Logging
# each of those requests with a second database session can exhaust the small
# application pool and leave the board stuck in its loading state.
EXCLUDED_PATHS = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/test-board",
)

FEATURE_NAME_MAP = {
    "/api/v1/ci": "CI看板", "/api/v1/models": "模型看板", "/api/v1/performance": "性能数据",
    "/api/v1/workflows": "Workflow配置", "/api/v1/model-sync-configs": "模型同步配置",
    "/api/v1/auth/me": "用户信息", "/api/v1/auth/change-password": "修改密码",
    "/api/v1/users": "用户管理", "/api/v1/project-dashboard": "项目看板",
    "/api/v1/resource-dashboard": "资源看板", "/api/v1/job-owners": "Job责任人",
    "/api/v1/issue-diagnosis": "问题定位", "/api/v1/alert": "告警规则",
    "/api/v1/pr-pipeline": "PR流水线", "/api/v1/commit-analysis": "Commit分析",
    "/api/v1/daily-summary": "每日总结", "/api/v1/stats": "统计信息",
}


def _feature_name(path: str) -> str:
    for prefix, name in FEATURE_NAME_MAP.items():
        if path.startswith(prefix):
            return name
    return path


async def _persist_usage_batch(batch: list[dict[str, object]]) -> None:
    """Persist usage records in one short transaction.

    Usage tracking is deliberately decoupled from request handling.  A page can
    issue many API calls at once; opening a database session for every response
    would compete with the session used by the actual endpoint.
    """
    if not batch:
        return

    try:
        async with SessionLocal() as db:
            db.add_all(
                FeatureUsageLog(
                    user_id=int(item["user_id"]),
                    feature_name=str(item["feature_name"]),
                    request_path=str(item["request_path"]),
                    metadata_json=dict(item["metadata_json"]),
                )
                for item in batch
            )
            await db.commit()
    except Exception as exc:
        # Usage analytics must never make the API unavailable.  The worker
        # drops a failed batch after logging the error and continues draining.
        logger.warning("Failed to persist feature usage batch (%d records): %s", len(batch), exc)


async def _usage_worker() -> None:
    """Drain the bounded usage queue with at most one DB session at a time."""
    assert _usage_queue is not None
    assert _usage_stop_event is not None

    while not (_usage_stop_event.is_set() and _usage_queue.empty()):
        try:
            first = await asyncio.wait_for(
                _usage_queue.get(), timeout=_USAGE_FLUSH_INTERVAL_SECONDS
            )
        except TimeoutError:
            continue

        batch = [first]
        while len(batch) < _USAGE_BATCH_SIZE:
            try:
                batch.append(_usage_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            await _persist_usage_batch(batch)
        finally:
            for _ in batch:
                _usage_queue.task_done()


async def start_usage_tracking_worker() -> None:
    """Start the per-process usage writer during the API lifespan."""
    global _usage_queue, _usage_stop_event, _usage_worker_task

    if _usage_worker_task and not _usage_worker_task.done():
        return

    _usage_queue = asyncio.Queue(maxsize=_USAGE_QUEUE_MAXSIZE)
    _usage_stop_event = asyncio.Event()
    _usage_worker_task = asyncio.create_task(_usage_worker(), name="feature-usage-writer")


async def stop_usage_tracking_worker() -> None:
    """Flush queued usage records and stop the writer before engine disposal."""
    global _usage_queue, _usage_stop_event, _usage_worker_task

    if not _usage_queue or not _usage_worker_task:
        return

    try:
        try:
            await asyncio.wait_for(_usage_queue.join(), timeout=5)
        except TimeoutError:
            logger.warning("Timed out flushing feature usage queue during shutdown")
        assert _usage_stop_event is not None
        _usage_stop_event.set()
        if not _usage_worker_task.done():
            try:
                await _usage_worker_task
            except asyncio.CancelledError:
                pass
        else:
            # Retrieve a failed task exception without allowing it to break
            # application shutdown.
            try:
                _usage_worker_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("Feature usage worker stopped unexpectedly: %s", exc)
    finally:
        _usage_queue = None
        _usage_stop_event = None
        _usage_worker_task = None


def _enqueue_usage_record(record: dict[str, object]) -> None:
    """Enqueue without blocking the response path; drop only on saturation."""
    if _usage_queue is None:
        return
    try:
        _usage_queue.put_nowait(record)
    except asyncio.QueueFull:
        logger.warning("Feature usage queue is full; dropping one usage record")


class UsageTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        actor_context = github_actor.set(None)
        feature_context = github_feature.set(None)
        try:
            response: Response = await call_next(request)
        finally:
            github_actor.reset(actor_context)
            github_feature.reset(feature_context)
        path = request.url.path
        if any(path.startswith(e) for e in EXCLUDED_PATHS) or request.method == "OPTIONS":
            return response

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return response

        payload = decode_token(auth_header.split(" ")[1])
        if not payload:
            return response

        raw_user_id = payload.get("user_id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            return response

        _enqueue_usage_record(
            {
                "user_id": user_id,
                "feature_name": _feature_name(path),
                "request_path": path,
                "metadata_json": {
                    "method": request.method,
                    "status_code": response.status_code,
                },
            }
        )
        return response
