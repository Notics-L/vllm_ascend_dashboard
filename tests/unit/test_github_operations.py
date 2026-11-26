import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from database.migrations import github_operations as migration
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.mysql_test_db import create_test_engine

from api.deps import get_current_user
from api.v1.github_operations import router
from infrastructure.clients import daily_pipeline_collection, github_http
from infrastructure.core.config import settings
from infrastructure.core.github_audit import (
    GitHubAuditUnavailable,
    github_actor,
    github_audit_writer,
    github_feature,
)
from infrastructure.core.github_credentials import GitHubCredential, daily_github_credential
from infrastructure.core.security import encrypt_api_key
from infrastructure.db import base
from infrastructure.persistence.models import GitHubOperation, User
from workflow_actions import service as workflow_actions


@pytest.mark.asyncio
async def test_signed_redirect_and_secrets_stay_out_of_audit(github_audit_records):
    records = github_audit_records
    token = "legacy_test_token"
    path = "/repos/example/dashboard/actions/jobs/42/logs"

    def send(request):
        if request.url.host == "api.github.com":
            assert request.headers["Authorization"] == f"Bearer {token}"
            return httpx.Response(302, headers={
                "Location": "https://artifacts.example/download?signature=secret",
                "X-GitHub-Request-Id": "A11B:0123:FE",
            })
        assert "Authorization" not in request.headers
        if request.url.host == "artifacts.example":
            raise httpx.ConnectTimeout("download with secret URL failed", request=request)
        return httpx.Response(200)

    async with github_http.GitHubAsyncClient(
        audit_source="github_client", credential=GitHubCredential("global", token),
        transport=httpx.MockTransport(send), follow_redirects=True,
    ) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(f"https://api.github.com{path}/{token}?access_token=secret")
        await client.get("https://download.example/signed", headers={"Authorization": f"Bearer {token}"})

    assert len(records) == 1
    assert records[0]["endpoint"] == f"{path}/[redacted]"
    assert records[0]["repository"] == "example/dashboard"
    assert records[0]["github_request_id"] == "A11B:0123:FE"
    assert records[0]["credential_source"] == "global"
    assert records[0]["result"] == "redirect"
    assert token not in repr(records)
    assert "secret" not in repr(records)


@pytest.mark.asyncio
async def test_daily_collection_audits_its_dedicated_configured_token(monkeypatch, github_audit_records):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "global-test-token")
    credential = daily_github_credential({"github_token": encrypt_api_key("daily-test-token")})
    records = github_audit_records

    def send(request):
        assert request.headers["Authorization"] == "Bearer daily-test-token"
        return httpx.Response(200, json={"id": 42})

    monkeypatch.setattr(
        daily_pipeline_collection,
        "GitHubAsyncClient",
        lambda **kwargs: github_http.GitHubAsyncClient(transport=httpx.MockTransport(send), **kwargs),
    )
    async with daily_pipeline_collection.DailyCollectionClient("example/dashboard", credential) as client:
        assert await client.run(42) == {"id": 42}

    assert len(records) == 1
    assert records[0]["source"] == "daily_collection"
    assert records[0]["credential_source"] == "daily"
    assert records[0]["result"] == "success"


@pytest.mark.asyncio
async def test_refuses_authenticated_requests_without_audit(monkeypatch):
    credential = GitHubCredential("daily", "test-only-token")

    def send(*args, **kwargs):
        pytest.fail("GitHub request reached the network without an audit writer")

    async with github_http.GitHubAsyncClient(
        audit_source="daily_config", credential=credential, transport=httpx.MockTransport(send),
    ) as client:
        with pytest.raises(GitHubAuditUnavailable, match="audit writer is unavailable"):
            await client.get("/user")

    monkeypatch.setattr(github_http.subprocess, "run", send)
    with pytest.raises(GitHubAuditUnavailable, match="audit writer is unavailable"):
        github_http.run_github_git(
            credential, "example/dashboard", "fetch", "https://github.com/example/dashboard.git",
            audit_source="daily_revert", cwd=".", name="Example", email="example@example.com",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["insert", "commit"])
async def test_audit_database_failure_blocks_http_and_git_before_network(monkeypatch, stage):
    credential = GitHubCredential("global", "test-only-token")

    @asynccontextmanager
    async def broken_session():
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(lastrowid=1)), commit=AsyncMock())
        getattr(db, "execute" if stage == "insert" else "commit").side_effect = OSError("database unavailable")
        yield db

    def send(*args, **kwargs):
        pytest.fail("A GitHub operation started before committing its audit row")

    monkeypatch.setattr(base, "SessionLocal", broken_session)
    monkeypatch.setattr(github_http.subprocess, "run", send)
    async with github_audit_writer("test"), github_http.GitHubAsyncClient(
        audit_source="test", credential=credential, transport=httpx.MockTransport(send),
    ) as client:
        with pytest.raises(GitHubAuditUnavailable, match="could not be committed"):
            await client.get("/user")
        with pytest.raises(GitHubAuditUnavailable, match="could not be committed"):
            await asyncio.to_thread(
                github_http.run_github_git, credential, "example/dashboard", "fetch",
                "https://github.com/example/dashboard.git",
                audit_source="test", cwd=".", name="Example", email="example@example.com",
            )


@pytest.mark.asyncio
async def test_daily_scheduler_installs_audit_before_restoring_background_jobs(monkeypatch):
    from daily_pipeline import scheduler as daily_scheduler
    from infrastructure.core.github_audit import require_github_audit

    async def verify_audit(*args):
        require_github_audit()

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(daily_scheduler, "reload_daily_schedules", AsyncMock(side_effect=verify_audit))
    monkeypatch.setattr(daily_scheduler.DailyRunCollector, "restore", AsyncMock(side_effect=verify_audit))
    monkeypatch.setattr(daily_scheduler.DailyProcessRunner, "restore", AsyncMock(side_effect=verify_audit))

    app = daily_scheduler.create_control_app()
    async with app.router.lifespan_context(app):
        require_github_audit()

    with pytest.raises(RuntimeError, match="audit writer is unavailable"):
        require_github_audit()


def test_public_git_does_not_inherit_github_credentials(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-token")
    monkeypatch.setenv("GH_TOKEN", "test-only-token")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "Authorization: Basic inherited-test-token")
    env = github_http.github_git_environment()

    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env
    assert env["GIT_CONFIG_VALUE_0"] == env["GIT_CONFIG_VALUE_1"] == ""


def test_audited_http_client_requires_tls_certificate_verification():
    with pytest.raises(ValueError, match="TLS certificate verification"):
        github_http.GitHubAsyncClient(
            audit_source="test", credential=GitHubCredential("global", "test-token"), verify=False,
        )


@pytest.mark.asyncio
async def test_audited_http_client_removes_token_from_returned_request(github_audit_records):
    credential = GitHubCredential("global", "test-only-token")

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-only-token"
        return httpx.Response(200)

    async with github_http.GitHubAsyncClient(
        audit_source="test", credential=credential, transport=httpx.MockTransport(respond),
    ) as client:
        response = await client.get("/user")

    assert "Authorization" not in response.request.headers


@pytest.mark.asyncio
async def test_operation_history_persists_and_filters_authenticated_calls(monkeypatch):
    engine = create_test_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "SessionLocal", sessions)
    monkeypatch.setattr(migration, "engine", engine)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    repository = f"example/audit-{uuid4().hex}"
    since = datetime.now(UTC) - timedelta(minutes=1)
    role = "admin"

    async def current_user():
        return User(username="tester", role=role)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = current_user

    async def assert_committed_attempt(method, endpoint):
        async with sessions() as db:
            attempt_state = await db.scalar(select(GitHubOperation.result).where(
                GitHubOperation.repository == repository,
                GitHubOperation.method == method,
                GitHubOperation.endpoint == endpoint,
                GitHubOperation.result == "pending",
            ))
        assert attempt_state == "pending"

    async def send(request):
        await assert_committed_attempt(request.method, request.url.path)
        if request.url.path.endswith("/timeout"):
            raise httpx.ReadTimeout("sensitive network error text", request=request)
        return httpx.Response(201 if request.method == "POST" else 403)

    loop = asyncio.get_running_loop()

    def git_run(args, *, env, **kwargs):
        asyncio.run_coroutine_threadsafe(assert_committed_attempt("PUSH", f"/{repository}.git"), loop).result(5)
        assert args[-2] == f"https://github.com/{repository}.git"
        assert env["GIT_CONFIG_VALUE_1"].startswith("Authorization: Basic ")
        assert all("test-only-token" not in arg for arg in args)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(
        workflow_actions, "GitHubAsyncClient",
        lambda **kwargs: github_http.GitHubAsyncClient(transport=httpx.MockTransport(send), **kwargs),
    )
    monkeypatch.setattr(github_http.subprocess, "run", git_run)
    try:
        await migration.run()
        await migration.run()
        actor = github_actor.set("tester")
        try:
            async with github_audit_writer("api"), github_http.GitHubAsyncClient(
                audit_source="daily_config", credential=GitHubCredential("daily", "test-only-token"),
                transport=httpx.MockTransport(send),
            ) as client:
                path = f"/repos/{repository}/issues"
                await client.post(path, json={"body": "private issue text"})
                await client.get(path)
                with pytest.raises(httpx.ReadTimeout):
                    await client.get(f"{path}/timeout")
                await asyncio.to_thread(
                    github_http.run_github_git, GitHubCredential("daily", "test-only-token"),
                    repository, "push", f"https://github.com/{repository}.git", "main",
                    audit_source="daily_revert", cwd=".", name="Example", email="example@example.com",
                )
                await workflow_actions._github_request(
                    GitHubCredential("actions", "test-only-actions-token"), "POST",
                    f"/repos/{repository}/actions/runs/42/rerun",
                )
                feature = github_feature.set("ci")
                try:
                    async with github_http.GitHubAsyncClient(
                        audit_source="github_client", credential=GitHubCredential("global", "test-only-global-token"),
                        transport=httpx.MockTransport(send),
                    ) as global_client:
                        await global_client.get(f"/repos/{repository}")
                finally:
                    github_feature.reset(feature)

                class FinalUpdateFails(AsyncSession):
                    async def execute(self, statement, *args, **kwargs):
                        if statement.is_update:
                            raise OSError("database unavailable after GitHub responded")
                        return await super().execute(statement, *args, **kwargs)

                with monkeypatch.context() as outage:
                    outage.setattr(base, "SessionLocal", async_sessionmaker(engine, class_=FinalUpdateFails))
                    async with github_http.GitHubAsyncClient(
                        audit_source="upstream_test", credential=GitHubCredential("global", "test-only-token"),
                        transport=httpx.MockTransport(send),
                    ) as global_client:
                        assert (await global_client.get(f"/repos/{repository}/pending")).status_code == 403
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

            pending = await client.get(url, params={**params, "credential_source": "global", "result": "pending"})
            assert pending.status_code == 200
            assert pending.json()["total"] == 1
            assert pending.json()["items"][0]["source"] == "upstream_test"

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
