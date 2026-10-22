"""Shared LLM provider lookup for contributor_board modules."""
from __future__ import annotations

from typing import Any


async def get_active_llm_config(db: Any, model: str | None = None) -> Any | None:
    """从数据库获取启用的 LLM 配置。

    优先级:
    1. is_active=True 的 provider（用于 AI 总结）
    2. enabled=True 且配置了 API Key 的 provider（兜底）
    """
    from sqlalchemy import select as sa_select
    from infrastructure.persistence.models.daily_summary import LLMProviderConfig

    # 优先查找 is_active=True 的 provider
    stmt = sa_select(LLMProviderConfig).where(LLMProviderConfig.is_active == True)  # noqa: E712
    if model:
        stmt = stmt.where(LLMProviderConfig.default_model == model)
    stmt = stmt.limit(1)

    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if config:
        return config

    # Fallback: 查找 enabled=True 且配置了 API Key 的 provider
    stmt2 = sa_select(LLMProviderConfig).where(
        LLMProviderConfig.enabled == True,  # noqa: E712
        LLMProviderConfig.api_key.isnot(None),
        LLMProviderConfig.api_key != "",
    )
    if model:
        stmt2 = stmt2.where(LLMProviderConfig.default_model == model)
    stmt2 = stmt2.order_by(LLMProviderConfig.display_order).limit(1)

    result2 = await db.execute(stmt2)
    return result2.scalar_one_or_none()
