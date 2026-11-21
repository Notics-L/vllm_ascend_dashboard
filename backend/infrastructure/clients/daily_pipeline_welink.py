"""Async adapter for the supplied Xiaoluban / WeLink JSON contract."""

import json

import httpx

XIAOLUBAN_API_URL = "http://xiaoluban.rnd.huawei.com:80/"
SEND_TIMEOUT_SECONDS = 10
MAX_RESPONSE_LENGTH = 4096


class NotificationError(Exception):
    """A safe delivery failure that does not disclose the account or token."""


class NotificationDeliveryUnknown(NotificationError):
    """The request might have reached the server; never blindly resend it."""


class WeLinkClient:
    def __init__(self, token, *, transport=None):
        self.token = token
        self.client = httpx.AsyncClient(
            timeout=SEND_TIMEOUT_SECONDS, follow_redirects=False, transport=transport
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def send_msg(self, content, receiver):
        try:
            response = await self.client.post(
                XIAOLUBAN_API_URL,
                json={"content": content, "receiver": receiver, "auth": self.token},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise NotificationError("无法连接小鲁班通知接口，请检查内网连接") from None
        except httpx.HTTPError:
            raise NotificationDeliveryUnknown(
                "通知送达情况未知，请人工确认，避免重复发送"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        def sanitized(value):
            if isinstance(value, dict):
                return {
                    key: "[已隐藏]"
                    if key.lower() in {"auth", "token", "account", "receiver"}
                    else sanitized(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [sanitized(item) for item in value]
            if isinstance(value, str):
                return value.replace(self.token, "[已隐藏]").replace(receiver, "[已隐藏]")
            if str(value) == receiver:
                return "[已隐藏]"
            return value

        text = (
            json.dumps(sanitized(payload), ensure_ascii=False)
            if payload is not None
            else response.text.replace(self.token, "[已隐藏]").replace(receiver, "[已隐藏]")
        )[:MAX_RESPONSE_LENGTH]
        if response.status_code >= 500:
            raise NotificationDeliveryUnknown("通知服务响应异常，送达情况未知，请人工确认")
        if not response.is_success:
            raise NotificationError(f"小鲁班通知接口拒绝请求（HTTP {response.status_code}）")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise NotificationError("小鲁班通知接口返回发送失败：" + text)
        # The supplied API contract treats a successful HTTP response as accepted.
        return text
