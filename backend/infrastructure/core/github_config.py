"""Shared GitHub runtime configuration for all service processes.

The API, Scheduler, and Collector are separate processes. Environment
variables are a bootstrap source, but a setting changed through the admin UI
must be persisted in the shared MySQL control plane so every process can see
it. The token is encrypted with the application's existing secret helper.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from infrastructure.core.security import decrypt_api_key, encrypt_api_key
from infrastructure.db.base import SessionLocal
from infrastructure.persistence.models import ProjectDashboardConfig

logger = logging.getLogger(__name__)

GITHUB_RUNTIME_CONFIG_KEY = "github_runtime_config"


def _as_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _decrypt_runtime_token(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""

    token = decrypt_api_key(value)
    # Accept a legacy plain token if one was written by an older development
    # build, but never write a new token in plain text.
    if token == value and not value.startswith(("ghp_", "github_pat_")):
        logger.warning("Ignoring an invalid encrypted GitHub runtime token")
        return ""
    return token


def _apply_runtime_config(config: Mapping[str, object]) -> bool:
    """Apply a decoded config document to this process's settings object."""
    owner = config.get("owner")
    repo = config.get("repo")
    token = _decrypt_runtime_token(config.get("token_encrypted"))

    if isinstance(owner, str) and owner.strip():
        settings.GITHUB_OWNER = owner.strip()
    if isinstance(repo, str) and repo.strip():
        settings.GITHUB_REPO = repo.strip()
    if token:
        settings.GITHUB_TOKEN = token

    return bool(token)


async def load_github_runtime_config(db: AsyncSession | None = None) -> bool:
    """Load shared GitHub settings, falling back to environment bootstrap."""
    if db is not None:
        result = await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == GITHUB_RUNTIME_CONFIG_KEY
            )
        )
        row = result.scalar_one_or_none()
        return _apply_runtime_config(_as_mapping(row.config_value) if row else {})

    async with SessionLocal() as session:
        return await load_github_runtime_config(session)


async def clear_github_runtime_token() -> bool:
    """Remove the encrypted token from the control-plane DB.

    Returns True if a token was present and removed.
    """
    async with SessionLocal() as db:
        result = await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == GITHUB_RUNTIME_CONFIG_KEY
            )
        )
        row = result.scalar_one_or_none()
        if row is None or not row.config_value:
            return False
        cfg = _as_mapping(row.config_value)
        if cfg.get("token_encrypted"):
            cfg.pop("token_encrypted", None)
            row.config_value = cfg
            await db.commit()
            logger.info("Cleared GitHub runtime token from control plane")
            return True
    return False


async def persist_github_runtime_config(
    *,
    token: str | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> None:
    """Persist GitHub settings in the shared control-plane database."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(ProjectDashboardConfig).where(
                ProjectDashboardConfig.config_key == GITHUB_RUNTIME_CONFIG_KEY
            )
        )
        row = result.scalar_one_or_none()
        config = _as_mapping(row.config_value) if row else {}

        config["owner"] = (owner or settings.GITHUB_OWNER).strip()
        config["repo"] = (repo or settings.GITHUB_REPO).strip()
        if token:
            config["token_encrypted"] = encrypt_api_key(token)

        if row is None:
            db.add(
                ProjectDashboardConfig(
                    config_key=GITHUB_RUNTIME_CONFIG_KEY,
                    config_value=config,
                    description="Shared GitHub runtime configuration for API, Scheduler, and Collector",
                )
            )
        else:
            row.config_value = config

        await db.commit()
