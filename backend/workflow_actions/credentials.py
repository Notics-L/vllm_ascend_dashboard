"""Actions 专用 GitHub Token 的加密存储。"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.config import settings
from infrastructure.persistence.models import ProjectDashboardConfig

CREDENTIAL_CONFIG_KEY = "workflow_actions_github_token"


def _fernet() -> Fernet:
    secret = settings.WORKFLOW_ACTIONS_ENCRYPTION_KEY
    if not secret:
        raise ValueError("未配置 WORKFLOW_ACTIONS_ENCRYPTION_KEY")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Actions Token 解密失败，请重新配置") from exc


async def _get_row(db: AsyncSession) -> ProjectDashboardConfig | None:
    result = await db.execute(
        select(ProjectDashboardConfig).where(
            ProjectDashboardConfig.config_key == CREDENTIAL_CONFIG_KEY
        )
    )
    return result.scalar_one_or_none()


async def get_credential_status(db: AsyncSession) -> dict:
    row = await _get_row(db)
    config = dict(row.config_value) if row and row.config_value else {}
    return {
        "configured": bool(config.get("token_encrypted")),
        "updated_at": config.get("updated_at"),
        "updated_by": config.get("updated_by"),
    }


async def save_github_token(db: AsyncSession, token: str, username: str) -> None:
    token = token.strip()
    if len(token) < 10:
        raise ValueError("GitHub Token 长度不足")
    row = await _get_row(db)
    config = dict(row.config_value) if row and row.config_value else {}
    config.update(
        {
            "token_encrypted": encrypt_token(token),
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": username,
        }
    )
    if row is None:
        db.add(
            ProjectDashboardConfig(
                config_key=CREDENTIAL_CONFIG_KEY,
                config_value=config,
                description="Actions 专用 GitHub Token（Fernet 加密）",
            )
        )
    else:
        row.config_value = config  # type: ignore[assignment]
    await db.commit()


async def get_github_token(db: AsyncSession) -> str:
    row = await _get_row(db)
    config = dict(row.config_value) if row and row.config_value else {}
    ciphertext = config.get("token_encrypted")
    if not isinstance(ciphertext, str) or not ciphertext:
        raise ValueError("未配置 Actions 专用 GitHub Token")
    return decrypt_token(ciphertext)


async def clear_github_token(db: AsyncSession, username: str) -> None:
    row = await _get_row(db)
    if row and row.config_value:
        config = dict(row.config_value)
        config.pop("token_encrypted", None)
        config["updated_at"] = datetime.now(UTC).isoformat()
        config["updated_by"] = username
        row.config_value = config  # type: ignore[assignment]
        await db.commit()
