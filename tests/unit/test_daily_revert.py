"""Real GitHub request shapes and recovery, using an isolated HTTP transport."""

import httpx
import pytest

from daily_pipeline.pull_requests import located_pull_request
from infrastructure.clients.daily_pipeline_revert import (
    DailyRevertClient,
    RetryableRevertError,
    RevertError,
)

ORIGINAL = {
    "number": 42,
    "title": "broken change",
    "merged_at": "2026-11-17T00:00:00Z",
    "merge_commit_sha": "1" * 40,
    "commits": 1,
    "head": {"ref": "feature"},
    "base": {"ref": "main"},
}
MARKER = "<!-- daily-pipeline-revert:example/source#42 -->"
SIGNED_SHA = "2" * 40
COMMITTER = {"id": 123, "login": "revert-bot", "name": "Revert Bot"}
SIGNED_COMMIT = {
    "commit": {
        "author": {"name": "Revert Bot", "email": "123+revert-bot@users.noreply.github.com"},
        "message": "Revert broken change\n\nSigned-off-by: Revert Bot <123+revert-bot@users.noreply.github.com>",
    }
}
EXISTING = {
    "number": 201,
    "html_url": "https://github.com/example/source/pull/201",
    "state": "open",
    "body": MARKER,
    "merged_at": None,
    "head": {"ref": "revert-42-feature", "sha": SIGNED_SHA},
}


async def client_for(handler):
    client = DailyRevertClient("example/source", "test-only-token")
    await client.client.aclose()
    client.client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    return client


@pytest.mark.parametrize(
    "value",
    [
        {"pr_url": "https://github.com/example/source/pull/42"},
        {"pr_repository": "example/source", "pr_number": 42},
    ],
)
def test_exact_pr_identity_includes_repository(value):
    assert located_pull_request(value) == {
        "pr_repository": "example/source",
        "pr_number": 42,
        "pr_url": "https://github.com/example/source/pull/42",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"pr_number": 42},
        {"pr_url": "https://other.example/source/pull/42"},
        {"pr_url": "https://github.com/example/source/pull/42", "pr_number": 43},
        {"pr_repository": "example/source", "pr_number": True},
    ],
)
def test_ambiguous_or_conflicting_identity_is_rejected(value):
    with pytest.raises(ValueError):
        located_pull_request(value)


@pytest.mark.asyncio
async def test_creates_revert_from_diagnosed_pr_and_reuses_it(monkeypatch):
    import json

    writes = []
    created = []
    signed = []

    def sign(self, original, branch, name, email):
        signed.append((branch, original["merge_commit_sha"], name, email))
        return SIGNED_SHA

    monkeypatch.setattr(DailyRevertClient, "_prepare_signed_branch", sign)

    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.url.path == "/user":
            return httpx.Response(200, json=COMMITTER)
        if request.url.path.endswith(f"/commits/{SIGNED_SHA}"):
            return httpx.Response(200, json=SIGNED_COMMIT)
        assert request.url.path.endswith("/pulls")
        if request.method == "GET":
            assert request.url.params["head"] == "example:revert-42-feature"
            assert request.url.params["base"] == "main"
            return httpx.Response(200, json=created)
        assert signed, "The PR must be created only after the signed branch is ready"
        payload = json.loads(request.content)
        assert payload["head"] == "revert-42-feature" and payload["base"] == "main"
        assert MARKER in payload["body"]
        writes.append(payload)
        created.append(EXISTING)
        return httpx.Response(201, json=EXISTING)

    async with await client_for(server) as client:
        first = await client.create_or_reuse(42, "https://github.com/example/ci/actions/runs/99")
        second = await client.create_or_reuse(42, "https://github.com/example/ci/actions/runs/100")
    assert first["number"] == second["number"] == 201
    assert len(writes) == 1
    assert signed == [
        (
            "revert-42-feature",
            ORIGINAL["merge_commit_sha"],
            "Revert Bot",
            "123+revert-bot@users.noreply.github.com",
        )
    ]


@pytest.mark.asyncio
async def test_lost_create_response_is_recovered_without_second_post(monkeypatch):
    created = []
    writes = []
    monkeypatch.setattr(DailyRevertClient, "_prepare_signed_branch", lambda *args: SIGNED_SHA)

    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.url.path == "/user":
            return httpx.Response(200, json=COMMITTER)
        if request.url.path.endswith(f"/commits/{SIGNED_SHA}"):
            return httpx.Response(200, json=SIGNED_COMMIT)
        if request.method == "GET":
            return httpx.Response(200, json=created)
        writes.append(request)
        created.append(EXISTING)
        raise httpx.ReadTimeout("lost response", request=request)

    async with await client_for(server) as client:
        with pytest.raises(RetryableRevertError):
            await client.create_or_reuse(42, "run")
        recovered = await client.create_or_reuse(42, "run")
    assert recovered["number"] == 201 and len(writes) == 1


@pytest.mark.asyncio
async def test_unmerged_pr_is_not_reverted():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={**ORIGINAL, "merged_at": None})

    async with await client_for(server) as client:
        with pytest.raises(RevertError, match="尚未合入"):
            await client.create_or_reuse(42, "run")
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, RevertError),
        (422, RevertError),
        (429, RetryableRevertError),
    ],
)
@pytest.mark.asyncio
async def test_github_create_error_is_not_recorded_as_success(status, expected, monkeypatch):
    monkeypatch.setattr(DailyRevertClient, "_prepare_signed_branch", lambda *args: SIGNED_SHA)

    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.url.path == "/user":
            return httpx.Response(200, json=COMMITTER)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(status, json={"message": "sensitive response omitted"})

    async with await client_for(server) as client:
        with pytest.raises(expected) as error:
            await client.create_or_reuse(42, "run")
    assert "sensitive" not in str(error.value)


@pytest.mark.asyncio
async def test_closed_unmerged_revert_requires_human_handling():
    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        return httpx.Response(200, json=[{**EXISTING, "state": "closed"}])

    async with await client_for(server) as client:
        with pytest.raises(RevertError, match="已关闭且未合入"):
            await client.create_or_reuse(42, "run")


@pytest.mark.parametrize("value", [None, [], {"pr_url": 42}])
def test_malformed_callback_pr_data_is_a_validation_error(value):
    with pytest.raises(ValueError):
        located_pull_request(value)


def test_legacy_number_uses_global_repository():
    result = located_pull_request({"pr_number": 390}, "example/source")
    assert result["pr_url"] == "https://github.com/example/source/pull/390"


def test_explicit_pr_cannot_override_configured_repository():
    with pytest.raises(ValueError, match="全局仓库配置不一致"):
        located_pull_request({"pr_url": "https://github.com/other/repo/pull/390"}, "example/source")


@pytest.mark.asyncio
async def test_empty_trigger_vehicle_is_rejected_before_any_write():
    calls = []

    def server(request):
        calls.append(request)
        return httpx.Response(200, json={**ORIGINAL, "changed_files": 0})

    async with await client_for(server) as client:
        with pytest.raises(RevertError, match="没有文件改动（空提交）"):
            await client.create_or_reuse(42, "run")
    assert len(calls) == 1
    assert calls[0].method == "GET"


@pytest.mark.asyncio
async def test_failed_signoff_leaves_no_pull_request(monkeypatch):
    def sign(*args):
        raise RevertError("回退提交缺少有效 sign-off")

    monkeypatch.setattr(DailyRevertClient, "_prepare_signed_branch", sign)

    def server(request):
        assert request.method == "GET", "Signing failed, so the API must not create a PR"
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.url.path == "/user":
            return httpx.Response(200, json=COMMITTER)
        return httpx.Response(200, json=[])

    async with await client_for(server) as client:
        with pytest.raises(RevertError, match="缺少有效 sign-off"):
            await client.create_or_reuse(42, "run")
