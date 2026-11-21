from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.v1.project_dashboard import get_dashboard_config, update_dashboard_config
from contracts.schemas import ProjectDashboardConfigUpdate
from infrastructure.core.config import settings
from infrastructure.persistence.models import ProjectDashboardConfig, User
from workflow_actions.credentials import (
    CREDENTIAL_CONFIG_KEY,
    clear_github_token,
    decrypt_token,
    encrypt_token,
    get_credential_status,
    get_github_token,
    save_github_token,
)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return FakeResult(self.row)

    def add(self, row):
        self.added.append(row)
        self.row = row

    async def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setattr(
        settings, "WORKFLOW_ACTIONS_ENCRYPTION_KEY", "test-master-key"
    )


def test_encryption_round_trip_and_wrong_key_rejection(monkeypatch):
    ciphertext = encrypt_token("github_pat_secret")

    assert ciphertext != "github_pat_secret"
    assert decrypt_token(ciphertext) == "github_pat_secret"

    monkeypatch.setattr(
        settings, "WORKFLOW_ACTIONS_ENCRYPTION_KEY", "different-master-key"
    )
    with pytest.raises(ValueError, match="解密失败"):
        decrypt_token(ciphertext)


def test_master_key_is_required(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_ACTIONS_ENCRYPTION_KEY", None)
    with pytest.raises(ValueError, match="WORKFLOW_ACTIONS_ENCRYPTION_KEY"):
        encrypt_token("github_pat_secret")


@pytest.mark.asyncio
async def test_saved_token_is_encrypted_and_status_has_no_secret():
    db = FakeDB()
    await save_github_token(db, "github_pat_secret", "admin")

    stored = db.row.config_value["token_encrypted"]
    assert stored != "github_pat_secret"
    assert "github_pat_secret" not in str(db.row.config_value)
    assert await get_github_token(db) == "github_pat_secret"

    status = await get_credential_status(db)
    assert status["configured"] is True
    assert "token_encrypted" not in status
    assert "github_pat_secret" not in str(status)

    await clear_github_token(db, "admin")
    cleared_status = await get_credential_status(db)
    assert cleared_status["configured"] is False
    assert cleared_status["updated_by"] == "admin"
    with pytest.raises(ValueError, match="未配置 Actions 专用"):
        await get_github_token(db)


@pytest.mark.asyncio
async def test_project_dashboard_config_cannot_read_or_overwrite_workflow_token():
    engine = create_engine("sqlite://")
    try:
        ProjectDashboardConfig.__table__.create(engine)
        with Session(engine) as session:
            session.add_all([
                ProjectDashboardConfig(config_key="ordinary", config_value={"value": 1}),
                ProjectDashboardConfig(
                    config_key=CREDENTIAL_CONFIG_KEY,
                    config_value={"token_encrypted": "encrypted-value"},
                ),
            ])
            session.commit()
            db = SimpleNamespace(execute=AsyncMock(side_effect=session.execute))
            admin = User(id=1, username="admin", role="admin")

            result = await get_dashboard_config(db, admin)
            assert [config["config_key"] for config in result["configs"]] == ["ordinary"]

            with pytest.raises(HTTPException) as error:
                await update_dashboard_config(
                    CREDENTIAL_CONFIG_KEY, ProjectDashboardConfigUpdate(config_value={}), db, admin
                )
            assert error.value.status_code == 404
    finally:
        engine.dispose()
