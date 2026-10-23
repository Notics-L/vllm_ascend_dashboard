"""Bounded retention for dated artifacts stored under ``DATA_DIR``."""
from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _dated_directories(root: Path) -> list[tuple[date, Path]]:
    if not root.exists():
        return []
    dated: list[tuple[date, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            dated.append((date.fromisoformat(child.name), child))
        except ValueError:
            continue
    return sorted(dated)


def remove_all_but_latest_date(root: Path) -> list[Path]:
    """Keep only the newest ISO-date directory under ``root``."""
    dated = _dated_directories(root)
    if len(dated) <= 1:
        return []
    latest = dated[-1][0]
    return _remove([path for day, path in dated if day < latest])


def remove_older_than_days(root: Path, *, retention_days: int, today: date | None = None) -> list[Path]:
    """Keep calendar-date folders for the most recent ``retention_days`` days."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    cutoff = (today or date.today()) - timedelta(days=retention_days - 1)
    return _remove([path for day, path in _dated_directories(root) if day < cutoff])


def _remove(paths: list[Path]) -> list[Path]:
    removed: list[Path] = []
    for path in paths:
        try:
            shutil.rmtree(path)
            removed.append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Unable to remove expired artifact directory %s: %s", path, exc)
    return removed
