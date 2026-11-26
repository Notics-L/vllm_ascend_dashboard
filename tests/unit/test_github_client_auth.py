from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_github_client_surfaces_http_401_as_authentication_error(monkeypatch):
    from infrastructure.clients.github_client import GitHubAuthenticationError, GitHubClient
    from infrastructure.core.config import settings

    monkeypatch.setattr(settings, "DEBUG", False)
    client = GitHubClient("test-token")
    request = httpx.Request("GET", "https://api.github.com/user")

    async def unauthorized(*_args, **_kwargs):
        return httpx.Response(401, request=request)

    client.client.request = unauthorized
    try:
        with pytest.raises(GitHubAuthenticationError, match="HTTP 401"):
            await client._request("GET", "/user")
    finally:
        await client.close()
