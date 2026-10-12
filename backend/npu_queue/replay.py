"""Opt-in, development-only replay of a captured NPU dashboard response."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from infrastructure.core.config import settings


@lru_cache(maxsize=1)
def load_dashboard_replay() -> dict[str, Any] | None:
    """Return the explicitly configured local snapshot, never in production."""
    configured_path = settings.NPU_DASHBOARD_REPLAY_SNAPSHOT.strip()
    if not configured_path or settings.is_production:
        return None

    path = Path(configured_path)
    if not path.is_file():
        raise RuntimeError(f"NPU dashboard replay snapshot not found: {path}")

    with path.open("r", encoding="utf-8") as snapshot_file:
        snapshot = json.load(snapshot_file)

    required_sections = {
        "clusterConfig",
        "resourceSummary",
        "queueDashboard",
        "queueDiagnostics",
    }
    missing_sections = required_sections.difference(snapshot)
    if missing_sections:
        missing = ", ".join(sorted(missing_sections))
        raise RuntimeError(f"Invalid NPU dashboard replay snapshot; missing: {missing}")
    return snapshot
