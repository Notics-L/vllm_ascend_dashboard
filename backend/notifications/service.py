"""通知调度器。

当前阶段（Phase 0）仅收敛邮件发送入口，业务模块不再直接依赖 SMTP 实现。
后续阶段将在这里接入短信、语音电话、通知策略、降级与投递记录。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.core.email import get_smtp_config, send_email


class NotificationDispatcher:
    """统一通知调度器。

    Phase 0 只暴露邮件能力：
    - `get_email_config`：读取统一 SMTP 配置
    - `send_email`：使用统一配置发送邮件

    业务调用方只需依赖本类，不直接感知 SMTP 字段。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_email_config(self) -> dict[str, Any]:
        return await get_smtp_config(self.db)

    async def send_email(
        self,
        *,
        subject: str,
        html_content: str,
        recipients: list[str],
        cc_recipients: list[str] | None = None,
        images: dict[str, bytes] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过统一配置发送 HTML 邮件。"""
        smtp_config = config if config is not None else await self.get_email_config()
        return await send_email(
            subject=subject,
            html_content=html_content,
            recipients=recipients,
            cc_recipients=cc_recipients,
            smtp_host=smtp_config.get("smtp_host", ""),
            smtp_port=int(smtp_config.get("smtp_port", 587)),
            smtp_username=smtp_config.get("smtp_username", ""),
            smtp_password=smtp_config.get("smtp_password", ""),
            smtp_use_tls=bool(smtp_config.get("smtp_use_tls", True)),
            from_email=smtp_config.get("from_email", ""),
            images=images,
        )
