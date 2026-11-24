"""Release consumed run artifacts after all concurrent analysis readers exit."""
import logging
import shutil
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from infrastructure.core.config import settings

logger = logging.getLogger(__name__)
_used_runs: ContextVar[set[Path] | None] = ContextVar("artifact_runs", default=None)


def track_artifact_run(path: Path) -> None:
    runs = _used_runs.get()
    if runs is not None:
        runs.add(path)


def remove_consumed_artifacts(root: Path) -> None:
    """Caller must hold the exclusive analysis lock; preserve metadata/reports."""
    for run in root.iterdir():
        if run.is_symlink() or not run.is_dir() or not run.name.isdecimal():
            continue
        marker = run / ".consumed"
        if not marker.is_file():
            continue
        try:
            for name in ("artifacts", "extracted"):
                target = run / name
                if target.is_symlink():
                    raise ValueError(f"Refusing artifact symlink: {target}")
                if target.exists():
                    shutil.rmtree(target)
            marker.unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.exception("Unable to clean consumed artifacts in %s", run)


def release_artifacts_after_analysis(method):
    @wraps(method)
    async def wrapped(*args, **kwargs):
        # flock is cross-process and releases automatically if a worker dies.
        # On unsupported platforms retain files rather than delete unsafely.
        try:
            import fcntl
        except ImportError:
            return await method(*args, **kwargs)
        root = Path(settings.DATA_DIR).resolve() / "ci-evidence" / "runs"
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".analysis.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            runs: set[Path] = set()
            token = _used_runs.set(runs)
            try:
                return await method(*args, **kwargs)
            finally:
                for run in runs:
                    try:
                        (run / ".consumed").touch()
                    except OSError:
                        logger.exception("Unable to mark artifacts consumed: %s", run)
                _used_runs.reset(token)
                fcntl.flock(lock, fcntl.LOCK_UN)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass  # The last concurrent reader will perform cleanup.
                else:
                    try:
                        remove_consumed_artifacts(root)
                    except OSError:
                        logger.exception("Unable to scan consumed artifact directories")
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
    return wrapped
