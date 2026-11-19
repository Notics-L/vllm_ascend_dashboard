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
    "node_id": "PR_original",
    "title": "broken change",
    "merged_at": "2026-11-17T00:00:00Z",
    "head": {"ref": "feature"},
    "base": {"ref": "main"},
}
MARKER = "<!-- daily-pipeline-revert:example/source#42 -->"
EXISTING = {
    "number": 201,
    "html_url": "https://github.com/example/source/pull/201",
    "state": "open",
    "body": MARKER,
    "merged_at": None,
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
async def test_creates_revert_from_diagnosed_pr_and_reuses_it():
    import json

    writes = []
    created = []

    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.method == "GET":
            assert request.url.params["head"] == "example:revert-42-feature"
            assert request.url.params["base"] == "main"
            return httpx.Response(200, json=created)
        payload = json.loads(request.content)
        assert payload["variables"]["input"]["pullRequestId"] == "PR_original"
        assert MARKER in payload["variables"]["input"]["body"]
        assert "mergePullRequest" not in payload["query"]
        writes.append(payload)
        created.append(EXISTING)
        return httpx.Response(
            200,
            json={
                "data": {
                    "revertPullRequest": {
                        "revertPullRequest": {
                            "number": 201,
                            "url": EXISTING["html_url"],
                            "state": "OPEN",
                            "merged": False,
                        }
                    }
                }
            },
        )

    async with await client_for(server) as client:
        first = await client.create_or_reuse(42, "https://github.com/example/ci/actions/runs/99")
        second = await client.create_or_reuse(42, "https://github.com/example/ci/actions/runs/100")
    assert first["number"] == second["number"] == 201
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_lost_create_response_is_recovered_without_second_mutation():
    created = []
    writes = []

    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
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
    "kind,expected",
    [
        ("FORBIDDEN", RevertError),
        ("UNPROCESSABLE", RevertError),
        ("RATE_LIMITED", RetryableRevertError),
    ],
)
@pytest.mark.asyncio
async def test_graphql_error_is_not_recorded_as_success(kind, expected):
    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json=ORIGINAL)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, json={"errors": [{"type": kind, "message": "sensitive response omitted"}]}
        )

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
async def test_not_revertable_error_is_explained_specifically():
    def server(request):
        if request.url.path.endswith("/pulls/42"):
            return httpx.Response(200, json={**ORIGINAL, "changed_files": 1})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, json={"errors": [{"type": "UNPROCESSABLE", "message": "Not revertable"}]}
        )

    async with await client_for(server) as client:
        with pytest.raises(RevertError, match="Not revertable"):
            await client.create_or_reuse(42, "run")
