"""Daily pipeline log files for the API and the dedicated scheduler."""
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 7


class DownloadUrlFilter(logging.Filter):
    """Keep temporary signed download credentials out of HTTP request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            values = []
            for value in record.args:
                if isinstance(value, str) or hasattr(value, "raw_path"):
                    url = urlsplit(str(value))
                    if url.scheme in ("https", "http") and url.query and url.hostname != "api.github.com":
                        value = urlunsplit((url.scheme, url.netloc, url.path, "<redacted>", ""))
                values.append(value)
            record.args = tuple(values)
        return True


def setup_daily_file_logging(data_dir: str, component: Literal["api", "scheduler"]) -> Path:
    http_logger = logging.getLogger("httpx")
    if not any(isinstance(item, DownloadUrlFilter) for item in http_logger.filters):
        http_logger.addFilter(DownloadUrlFilter())
    directory = Path(data_dir).expanduser().resolve() / "logs" / "daily_pipeline"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{component}.log"
    daily_logger = logging.getLogger("daily_pipeline")
    for handler in daily_logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == str(path):
            return path

    handler = RotatingFileHandler(
        path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    daily_logger.setLevel(logging.INFO)
    daily_logger.addHandler(handler)
    if component == "scheduler":
        # This process owns the APScheduler instance and its execution messages.
        apscheduler_logger = logging.getLogger("apscheduler")
        apscheduler_logger.setLevel(logging.INFO)
        apscheduler_logger.addHandler(handler)

    daily_logger.info("Daily pipeline file logging ready component=%s path=%s", component, path)
    return path
