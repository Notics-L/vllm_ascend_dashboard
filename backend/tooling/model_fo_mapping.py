"""Persistence helpers for the internal model-to-FO mapping.

The mapping is a current-value dictionary used when creating a new Nightly
configuration snapshot.  Existing snapshots deliberately keep the value they
were materialized with and are never rewritten by these helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.persistence.models import ModelFoMapping


def model_key_candidates(model_path: str | None) -> tuple[str, ...]:
    """Return normalized lookup keys for a config path or model identifier."""

    if not model_path:
        return ()
    normalized = model_path.strip().replace("\\", "/")
    if not normalized:
        return ()

    basename = PurePosixPath(normalized).name
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    candidates: list[str] = []
    for value in (normalized, basename, stem):
        key = value.strip().casefold()
        if key and key not in candidates:
            candidates.append(key)
    return tuple(candidates)


def normalize_model_key(model_path: str | None) -> str:
    """Return the canonical database key for a model/config path."""

    candidates = model_key_candidates(model_path)
    return candidates[1] if len(candidates) > 1 else (candidates[0] if candidates else "")


def lookup_model_fo(mapping: Mapping[str, str], model_path: str | None) -> str | None:
    """Resolve a model FO using full-path, basename, then extensionless keys."""

    for key in model_key_candidates(model_path):
        value = mapping.get(key)
        if value and value.strip():
            return value.strip()
    return None


async def load_model_fo_mappings(db: AsyncSession) -> dict[str, str]:
    """Load the current mapping table into a normalized lookup dictionary."""

    result = await db.execute(select(ModelFoMapping))
    return {
        row.model_key: row.model_fo
        for row in result.scalars().all()
        if row.model_key and row.model_fo
    }


async def seed_missing_model_fo_mappings(
    db: AsyncSession,
    file_mapping: Mapping[str, str],
) -> int:
    """Synchronize the versioned JSON mapping into the runtime mapping table.

    The JSON file is the source of truth for its own model keys.  Updating a
    mapped FO therefore repairs an existing stale mapping as well as adding a
    missing one; runtime-only keys are deliberately left untouched.
    """

    normalized: dict[str, str] = {}
    for model_path, model_fo in file_mapping.items():
        if not isinstance(model_fo, str) or not model_fo.strip():
            continue
        model_key = normalize_model_key(model_path)
        if model_key:
            normalized[model_key] = model_fo.strip()
    if not normalized:
        return 0

    result = await db.execute(
        select(ModelFoMapping).where(ModelFoMapping.model_key.in_(normalized))
    )
    existing = {row.model_key: row for row in result.scalars().all()}
    changed = 0
    for model_key, model_fo in normalized.items():
        mapping = existing.get(model_key)
        if mapping is None:
            db.add(ModelFoMapping(model_key=model_key, model_fo=model_fo))
            changed += 1
            continue
        if mapping.model_fo == model_fo:
            continue
        mapping.model_fo = model_fo
        changed += 1
    return changed


async def set_model_fo_mapping(
    db: AsyncSession,
    model_path: str | None,
    model_fo: str | None,
) -> bool:
    """Create/update a manual mapping, or remove it when explicitly cleared."""

    model_key = normalize_model_key(model_path)
    if not model_key:
        return False

    result = await db.execute(
        select(ModelFoMapping).where(ModelFoMapping.model_key == model_key)
    )
    mapping = result.scalar_one_or_none()
    cleaned_fo = model_fo.strip() if isinstance(model_fo, str) else ""

    if not cleaned_fo:
        if mapping is not None:
            await db.delete(mapping)
            return True
        return False

    if mapping is None:
        db.add(ModelFoMapping(model_key=model_key, model_fo=cleaned_fo))
        return True
    if mapping.model_fo != cleaned_fo:
        mapping.model_fo = cleaned_fo
        return True
    return False
