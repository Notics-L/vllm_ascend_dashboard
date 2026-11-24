import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from api.deps import get_current_user
from api.v1.github_operations import router
from fastapi import FastAPI
from infrastructure.clients import github_http
from infrastructure.core.github_audit import github_actor, github_audit_writer, github_feature
from infrastructure.db import base
from infrastructure.persistence.models import GitHubOperation, User
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from workflow_actions import service as workflow_actions

from database.migrations import github_operations as migration
from tests.mysql_test_db import create_test_engine


@pytest.mark.asyncio
async def test_signed_redirect_and_secrets_stay_out_of_audit(monkeypatch):
    records = []
    monkeypatch.setattr(github_http, "record_github_operation", lambda **record: records.append(record))
    token = "legacy_test_token"
    path = "/repos/example/dashboard/actions/jobs/42/logs"

    def send(request):
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={
                "Location": "https://artifacts.example/download?signature=secret",
                "X-GitHub-Request-Id": "A11B:0123:FE",
            })
        raise httpx.ConnectTimeout("download with secret URL failed", request=request)

    async with github_http.GitHubAsyncClient(
        audit_source="github_client", credential_source="global", transport=httpx.MockTransport(send),
        headers={"Authorization": f"Bearer {token}"}, follow_redirects=True,
    ) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(f"https://api.github.com{path}/{token}?access_token=secret")
        await client.get(f"https://api.github.com{path}", headers={"Authorization": ""}, follow_redirects=False)

    assert len(records) == 1
    assert records[0]["endpoint"] == f"{path}/[redacted]"
    assert records[0]["repository"] == "example/dashboard"
    assert records[0]["github_request_id"] == "A11B:0123:FE"
    assert records[0]["credential_source"] == "global"
    assert records[0]["result"] == "redirect"
    assert token not in repr(records)
    assert "secret" not in repr(records)


@pytest.mark.asyncio
async def test_operation_history_persists_and_filters_authenticated_calls(monkeypatch):
    engine = create_test_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "SessionLocal", sessions)
    monkeypatch.setattr(migration, "engine", engine)
    monkeypatch.setenv("ENVIRONMENT", "test")
    repository = f"example/audit-{uuid4().hex}"
    since = datetime.now(UTC) - timedelta(minutes=1)
    role = "admin"

    async def current_user():
        return User(username="tester", role=role)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = current_user

    def send(request):
        if request.url.path.endswith("/timeout"):
            raise httpx.ReadTimeout("sensitive network error text", request=request)
        return httpx.Response(201 if request.method == "POST" else 403)

    def push():
        with github_http.audit_github_git(
            "daily_revert", repository, "test-only-token", "push", credential_source="daily",
        ):
            pass

    monkeypatch.setattr(
        workflow_actions, "GitHubAsyncClient",
        lambda **kwargs: github_http.GitHubAsyncClient(transport=httpx.MockTransport(send), **kwargs),
    )
    try:
        await migration.run()
        await migration.run()
        actor = github_actor.set("tester")
        try:
            async with github_audit_writer("api"), github_http.GitHubAsyncClient(
                audit_source="daily_config", credential_source="daily", base_url="https://api.github.com",
                headers={"Authorization": "Bearer test-only-token"}, transport=httpx.MockTransport(send),
            ) as client:
                path = f"/repos/{repository}/issues"
                await client.post(path, json={"body": "private issue text"})
                await client.get(path)
                with pytest.raises(httpx.ReadTimeout):
                    await client.get(f"{path}/timeout")
                await asyncio.to_thread(push)
                await workflow_actions._github_request(
                    "test-only-actions-token", "POST", f"/repos/{repository}/actions/runs/42/rerun",
                )
                feature = github_feature.set("ci")
                try:
                    async with github_http.GitHubAsyncClient(
                        audit_source="github_client", credential_source="global", base_url="https://api.github.com",
                        headers={"Authorization": "Bearer test-only-global-token"},
                        transport=httpx.MockTransport(send),
                    ) as global_client:
                        await global_client.get(f"/repos/{repository}")
                finally:
                    github_feature.reset(feature)
        finally:
            github_actor.reset(actor)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = "/api/v1/github-operations"
            params = {"repository": repository, "start": since.isoformat(), "credential_source": "daily"}
            response = await client.get(url, params={**params, "page_size": 1, "page": 2})
            assert response.status_code == 200
            assert response.json()["total"] == 4
            assert response.json()["items"][0]["error_type"] == "timeout"

            response = await client.get(url, params={**params, "operation_type": "write", "result": "success"})
            assert response.json()["total"] == 2
            pushed, item = response.json()["items"]
            assert (pushed["source"], pushed["method"], pushed["http_status"]) == ("daily_revert", "PUSH", None)
            assert pushed["actor"] == "tester"
            assert item["http_status"] == 201
            assert item["actor"] == "tester"
            assert item["process"] == "api"
            assert item["source"] == "daily_config"
            assert "private issue text" not in response.text

            response = await client.get(url, params={**params, "result": "failed"})
            failed = response.json()["items"]
            assert [(row["http_status"], row["error_type"]) for row in failed] == [(None, "timeout"), (403, None)]
            assert "sensitive network error text" not in response.text
            assert (await client.get(url, params={**params, "end": since.isoformat()})).status_code == 422

            for source, credential_source, method in (
                ("workflow_actions", "actions", "POST"), ("ci", "global", "GET"),
            ):
                filtered = {**params, "credential_source": credential_source, "source": source, "actor": "tester"}
                response = await client.get(url, params=filtered)
                assert response.json()["total"] == 1
                operation = response.json()["items"][0]
                assert operation["actor"] == "tester"
                assert (operation["source"], operation["credential_source"], operation["method"]) == (
                    source, credential_source, method,
                )
                assert (await client.get(url, params={**filtered, "actor": "someone-else"})).json()["total"] == 0

            role = "user"
            assert (await client.get(url, params=params)).status_code == 403
    finally:
        try:
            async with sessions.begin() as db:
                await db.execute(delete(GitHubOperation.__table__).where(
                    GitHubOperation.__table__.c.repository == repository
                ))
        finally:
            await engine.dispose()
