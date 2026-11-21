"""统一通知服务。

Phase 0：先提供邮件通道的统一入口，后续在此包内扩展短信、语音电话、
通知策略、联系人、投递记录与语音 IVR 回调。
"""
from notifications.service import NotificationDispatcher

__all__ = ["NotificationDispatcher"]
