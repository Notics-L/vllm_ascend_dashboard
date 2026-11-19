"""
Database-backed logging handler.

Routes Python logging records into the app_logs table via a non-blocking
queue so that database writes never block the calling thread.
"""
import asyncio
import logging
import os
import queue
import traceback
from datetime import UTC, datetime

from sqlalchemy import text

from infrastructure.db.base import SessionLocal

logger = logging.getLogger(__name__)

# Worker 专用 logger：不传播到 root，避免 flush 失败的 warning 经 DBLogHandler
# 回流队列形成反馈循环。
_worker_logger = logging.getLogger("infrastructure.core.logging._worker")
_worker_logger.propagate = False
if not _worker_logger.handlers:
    _worker_logger.addHandler(logging.StreamHandler())
    _worker_logger.setLevel(logging.WARNING)

# Shared queue between the log handler (producer) and the DB worker (consumer).
_log_queue: queue.Queue = queue.Queue(maxsize=10000)

# Tracks whether setup_db_logging() has already been called.
_worker_started: bool = False


class DBLogHandler(logging.Handler):
    """Logging handler that inserts records into app_logs via a queue."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": datetime.fromtimestamp(
                    record.created, tz=UTC
                ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "level": record.levelname,
                "module": record.name,
                "function_name": record.funcName,
                "line_number": record.lineno,
                "message": self.format(record),
                "traceback": (
                    "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
                ),
            }
            try:
                _log_queue.put_nowait(entry)
            except queue.Full:
                pass
        except Exception:
            self.handleError(record)


async def _db_log_worker() -> None:
    """Background worker: drain the queue and batch-insert into app_logs.

    注意：queue.Queue.get(timeout=1) 是阻塞同步调用，不能直接在事件循环
    线程中执行，否则会阻塞 uvicorn 事件循环（启动时卡在 create_all）。
    通过 run_in_executor 将阻塞 get 放到线程池执行，事件循环保持畅通。
    """
    loop = asyncio.get_running_loop()
    batch: list[dict] = []

    while True:
        try:
            entry = await loop.run_in_executor(None, lambda: _log_queue.get(timeout=1))
            batch.append(entry)

            for _ in range(99):
                try:
                    entry = _log_queue.get_nowait()
                    batch.append(entry)
                except queue.Empty:
                    break

            if batch:
                await _flush_batch(batch)
                batch = []
        except queue.Empty:
            if batch:
                await _flush_batch(batch)
                batch = []
        except Exception as e:
            # 用不传播到 root 的 worker logger，避免失败日志回流队列形成反馈循环
            _worker_logger.warning("DB log worker error: %s", e)
            await asyncio.sleep(1)


async def _flush_batch(entries: list[dict]) -> None:
    """Insert a batch of log entries into app_logs."""
    if not entries:
        return
    async with SessionLocal() as db:
        try:
            for entry in entries:
                await db.execute(
                    text(
                        "INSERT INTO app_logs "
                        "(timestamp, level, module, function_name, "
                        "line_number, message, traceback) "
                        "VALUES "
                        "(:timestamp, :level, :module, :function_name, "
                        ":line_number, :message, :traceback)"
                    ),
                    entry,
                )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


def setup_db_logging(*, level: int | None = None, logger_prefixes: tuple[str, ...] = ()) -> None:
    """Install the DB log handler and start the background worker.

    Safe to call multiple times — subsequent calls are no-ops.
    Dedicated workers can set a level and logger prefixes for their own events.

    DB 端日志级别默认 WARNING：INFO/DEBUG 仍会输出到 stdout（docker logs），
    但不写库。历史教训：DEBUG 级入库曾让 app_logs 膨胀到 780 万行 / 2GB，
    并持续放大 binlog（每日数十 GB）。需要临时排障可用 DB_LOG_LEVEL=INFO。
    """
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    handler = DBLogHandler()
    db_log_level = os.environ.get("DB_LOG_LEVEL", "WARNING").upper()
    handler.setLevel(level if level is not None else getattr(logging, db_log_level, logging.WARNING))
    if logger_prefixes:
        handler.addFilter(lambda record: record.name.startswith(logger_prefixes))
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.addHandler(handler)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_db_log_worker())
        logger.info("DB log handler started")
    except RuntimeError:
        # No running event loop — worker will not start until one exists.
        # In practice setup_db_logging() is called during lifespan startup
        # when the event loop is already running.
        pass
