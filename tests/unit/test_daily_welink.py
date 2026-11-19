"""WeLink payload, privacy and ambiguous-delivery behavior without external requests."""

import json

import httpx
import pytest
from infrastructure.clients.daily_pipeline_welink import (
    NotificationDeliveryUnknown,
    NotificationError,
    WeLinkClient,
)


@pytest.mark.asyncio
async def test_supplied_api_contract_and_secret_redaction():
    def server(request):
        assert str(request.url) == "http://xiaoluban.rnd.huawei.com/"
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {
            "content": "失败用例通知",
            "receiver": "account-test",
            "auth": "token-test",
        }
        return httpx.Response(
            200, json={"success": True, "auth": "token-test", "receiver": "account-test"}
        )

    async with WeLinkClient("token-test", transport=httpx.MockTransport(server)) as client:
        response = await client.send_msg("失败用例通知", "account-test")
    assert "token-test" not in response and "account-test" not in response


@pytest.mark.parametrize("status", [400, 401, 403, 429, 302])
@pytest.mark.asyncio
async def test_rejected_response_does_not_count_as_sent(status):
    async with WeLinkClient(
        "token", transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        with pytest.raises(NotificationError):
            await client.send_msg("message", "account")


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError])
@pytest.mark.asyncio
async def test_uncertain_transport_does_not_allow_blind_resend(error):
    def server(request):
        raise error("sensitive request content omitted", request=request)

    async with WeLinkClient("secret", transport=httpx.MockTransport(server)) as client:
        with pytest.raises(NotificationDeliveryUnknown) as exc:
            await client.send_msg("message", "account")
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_explicit_api_failure_is_not_success():
    async with WeLinkClient(
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": False, "auth": "secret"})
        ),
    ) as client:
        with pytest.raises(NotificationError) as exc:
            await client.send_msg("message", "account")
    assert "secret" not in str(exc.value)


@pytest.mark.asyncio
async def test_json_escaped_credentials_are_redacted_before_auditing():
    payload = json.dumps(
        {"success": True, "auth": "密钥", "receiver": "account", "nested": "密钥"},
        ensure_ascii=True,
    )
    async with WeLinkClient(
        "密钥", transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    ) as client:
        response = await client.send_msg("message", "account")
    assert "密钥" not in response
    assert "account" not in response
