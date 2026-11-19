"""Directory writes encrypt accounts; responses only expose a fixed mask."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daily_pipeline.notification_recipients import DailyNotificationRecipientService
from infrastructure.core.security import decrypt_api_key, encrypt_api_key


@pytest.fixture(autouse=True)
def no_sending(monkeypatch):
    monkeypatch.setattr("daily_pipeline.notification_recipients.wake_notifications", AsyncMock())


@pytest.mark.asyncio
async def test_single_create_encrypts_account_and_returns_mask():
    rows = []

    def add(github, name, account):
        row = SimpleNamespace(id=1, github_name=github.lower(), name=name, account=account)
        rows.append(row)
        return row

    repo = SimpleNamespace(add=add, commit=AsyncMock(), db=object())
    result = await DailyNotificationRecipientService(repo).save(
        "GitHubUser", "姓名", "private-account"
    )
    assert decrypt_api_key(rows[0].account) == "private-account"
    assert rows[0].account != "private-account"
    assert result == {"id": 1, "githubName": "githubuser", "name": "姓名"}
    assert "account" not in result


@pytest.mark.asyncio
async def test_edit_keeps_ciphertext_if_account_omitted():
    ciphertext = encrypt_api_key("account")
    row = SimpleNamespace(id=1, github_name="user", name="old", account=ciphertext)
    repo = SimpleNamespace(get=AsyncMock(return_value=row), commit=AsyncMock(), db=object())
    await DailyNotificationRecipientService(repo).save("user", "new", None, 1)
    assert row.name == "new" and row.account == ciphertext


@pytest.mark.asyncio
async def test_github_identity_is_readonly_on_edit():
    row = SimpleNamespace(id=1, github_name="user")
    repo = SimpleNamespace(get=AsyncMock(return_value=row), commit=AsyncMock(), db=object())
    with pytest.raises(ValueError, match="用户名不能修改"):
        await DailyNotificationRecipientService(repo).save("other", "name", "account", 1)
    repo.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_delete_validates_every_row_before_deleting():
    repo = SimpleNamespace(
        get=AsyncMock(side_effect=[SimpleNamespace(id=1), None]),
        delete=AsyncMock(),
        commit=AsyncMock(),
    )
    with pytest.raises(LookupError):
        await DailyNotificationRecipientService(repo).delete_many({1, 2})
    repo.delete.assert_not_awaited()
    repo.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_delete_commits_selected_rows_once():
    first, second = SimpleNamespace(id=1), SimpleNamespace(id=2)
    repo = SimpleNamespace(
        get=AsyncMock(side_effect=[first, second]), delete=AsyncMock(), commit=AsyncMock()
    )
    assert await DailyNotificationRecipientService(repo).delete_many({1, 2}) == {"deleted": 2}
    assert repo.delete.await_count == 2
    repo.commit.assert_awaited_once()
