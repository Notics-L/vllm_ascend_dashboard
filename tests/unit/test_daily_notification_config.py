"""Notification credentials never leak through config responses or replace GitHub credentials."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from api.deps import get_current_user
from api.v1.daily_pipeline import router
from daily_pipeline import notification_config as module
from infrastructure.core.security import decrypt_api_key
from infrastructure.db.base import get_db


@pytest.mark.asyncio
async def test_token_is_encrypted_separately_and_never_returned(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(module, "set_setting", store)
    monkeypatch.setattr(module, "wake_notifications", AsyncMock())
    monkeypatch.setattr(module, "has_setting", AsyncMock(return_value=True))
    db = SimpleNamespace(commit=AsyncMock())
    result = await module.save_notification_config(db, "  notification-test-only  ")
    assert result == {"tokenConfigured": True}
    args = store.await_args.args
    assert args[1] == "notification_token"
    assert args[2] != "notification-test-only"
    assert decrypt_api_key(args[2]) == "notification-test-only"
    assert "notification-test-only" not in str(result)
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("token", [None, "", "  "])
@pytest.mark.asyncio
async def test_blank_save_preserves_existing_credential(monkeypatch, token):
    store = AsyncMock()
    monkeypatch.setattr(module, "set_setting", store)
    monkeypatch.setattr(module, "wake_notifications", AsyncMock())
    monkeypatch.setattr(module, "has_setting", AsyncMock(return_value=True))
    db = SimpleNamespace(commit=AsyncMock())
    assert await module.save_notification_config(db, token) == {"tokenConfigured": True}
    store.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_readonly_user_cannot_change_notification_token():
    app = FastAPI()
    app.include_router(router)
    async def user():
        return SimpleNamespace(username="viewer", role="user")
    async def database():
        yield SimpleNamespace()
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = database
    with patch("api.v1.daily_pipeline.save_notification_config", new_callable=AsyncMock) as save:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            response = await client.put("/daily-pipeline/notification-config",json={"token":"test-only"})
    assert response.status_code == 403
    save.assert_not_awaited()
