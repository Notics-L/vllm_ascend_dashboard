import base64
from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from image_build import workflow_sync

TEST_REPOSITORY = "example/builds"
OTHER_REPOSITORY = "example/other-builds"
WORKFLOW = """
on:
  workflow_dispatch:
    inputs:
      vllm_ascend_branch:
        type: choice
        required: true
        default: main
        options: [main, off]
      skip_base:
        type: boolean
        default: false
      count:
        type: number
        default: 0
      source_ref:
        default: ''
"""


def test_parse_inputs_preserves_order_choices_and_explicit_defaults():
    inputs = workflow_sync.parse_dispatch_inputs(WORKFLOW)

    assert [item["name"] for item in inputs] == [
        "vllm_ascend_branch",
        "skip_base",
        "count",
        "source_ref",
    ]
    assert inputs[0] == {
        "name": "vllm_ascend_branch",
        "type": "choice",
        "description": "",
        "required": True,
        "options": ["main", "off"],
        "default": "main",
    }
    assert inputs[1]["default"] is False
    assert type(inputs[2]["default"]) is int and inputs[2]["default"] == 0
    assert inputs[3]["default"] == ""
    assert workflow_sync.parse_dispatch_inputs("on: [push, workflow_dispatch]") == []
    assert workflow_sync.parse_dispatch_inputs("on: push") is None


class DefinitionStore:
    def __init__(self):
        self.repositories = defaultdict(dict)
        self.repository = TEST_REPOSITORY

    @property
    def definitions(self):
        return self.repositories[TEST_REPOSITORY]

    async def scalar(self, _statement):
        return self.repository

    @asynccontextmanager
    async def __call__(self):
        yield self

    @asynccontextmanager
    async def begin(self):
        yield self

    async def execute(self, statement):
        repository = statement.compile().params["repository_1"]
        definitions = self.repositories[repository]
        return SimpleNamespace(all=lambda: [(name, row.is_available) for name, row in definitions.items()])

    async def get(self, _model, key, **_options):
        repository, branch = key
        return self.repositories[repository].get(branch)

    def add(self, row):
        self.repositories[row.repository][row.branch_name] = row


def file_response(source):
    return httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(source.encode()).decode(),
        },
    )


@pytest.mark.asyncio
async def test_sync_discovers_branches_and_only_changes_normalized_inputs(monkeypatch):
    store = DefinitionStore()
    monkeypatch.setattr(workflow_sync, "SessionLocal", store)

    heads = {"main": "main-1", "release": "release-1"}
    contents = {"main-1": WORKFLOW}
    requested = []

    def transport(request):
        if request.url.path.endswith("/branches"):
            return httpx.Response(
                200, json=[{"name": name, "commit": {"sha": sha}} for name, sha in heads.items()]
            )
        ref = request.url.params["ref"]
        requested.append(ref)
        return file_response(contents[ref]) if ref in contents else httpx.Response(404)

    sync = workflow_sync.ImageBuildWorkflowSync()
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(transport)
    ) as client:
        await sync.sync(client, TEST_REPOSITORY)
        assert set(store.definitions) == {"main"}
        main = store.definitions["main"]
        original_inputs = main.inputs_json
        original_hash = main.inputs_hash

        # A job or comment change only affects the workflow file, not its form definition.
        heads["main"] = "main-2"
        contents["main-2"] = WORKFLOW + "\n# Job changed\n"
        await sync.sync(client, TEST_REPOSITORY)
        assert main.inputs_json is original_inputs
        assert main.inputs_hash == original_hash
        assert requested.count("release-1") == 1

        heads["release"] = "release-2"
        contents["release-2"] = "on: workflow_dispatch"
        contents["main-3"] = WORKFLOW.replace("options: [main, off]", "options: [main, release]")
        heads["main"] = "main-3"
        await sync.sync(client, TEST_REPOSITORY)
        assert store.definitions["release"].inputs_json == []
        assert main.inputs_hash != original_hash
        assert main.inputs_json[0]["options"] == ["main", "release"]

        # A confirmed missing file hides its definition. Restoring the file reactivates it.
        heads["release"] = "release-3"
        await sync.sync(client, TEST_REPOSITORY)
        assert not store.definitions["release"].is_available
        heads["release"] = "release-4"
        contents["release-4"] = "on: workflow_dispatch"
        await sync.sync(client, TEST_REPOSITORY)
        assert store.definitions["release"].is_available


@pytest.mark.asyncio
async def test_sync_keeps_definitions_when_github_reads_fail(monkeypatch):
    store = DefinitionStore()
    monkeypatch.setattr(workflow_sync, "SessionLocal", store)
    heads = {"main": "main-1", "removed": "removed-1"}
    error = None
    partial_listing = False

    def transport(request):
        if request.url.path.endswith("/branches"):
            if partial_listing:
                if request.url.params["page"] == "2":
                    return httpx.Response(503)
                return httpx.Response(
                    200,
                    json=[{"name": "main", "commit": {"sha": heads["main"]}}],
                    headers={"Link": '<https://api.github.com/test?page=2>; rel="next"'},
                )
            return httpx.Response(
                200, json=[{"name": name, "commit": {"sha": sha}} for name, sha in heads.items()]
            )
        if error:
            return httpx.Response(error)
        return file_response(WORKFLOW)

    sync = workflow_sync.ImageBuildWorkflowSync()
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(transport)
    ) as client:
        await sync.sync(client, TEST_REPOSITORY)
        original_hash = store.definitions["main"].inputs_hash
        partial_listing = True
        with pytest.raises(httpx.HTTPStatusError):
            await sync.sync(client, TEST_REPOSITORY)
        assert store.definitions["removed"].is_available

        partial_listing = False
        error = 429
        await sync.sync(client, TEST_REPOSITORY)
        assert store.definitions["main"].is_available
        assert store.definitions["main"].inputs_hash == original_hash
        assert store.definitions["removed"].is_available

        del heads["removed"]
        error = None
        await sync.sync(client, TEST_REPOSITORY)
        assert not store.definitions["removed"].is_available


@pytest.mark.asyncio
async def test_sync_uses_current_repository_environments_for_every_branch(monkeypatch):
    store = DefinitionStore()
    monkeypatch.setattr(workflow_sync, "SessionLocal", store)
    environments = ["staging", "production"]
    environment_pages = []
    environment_error = False
    other_workflow = "on: workflow_dispatch"
    environment_workflow = WORKFLOW + """
      target:
        type: environment
        default: staging
"""

    def transport(request):
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json=[
                {"name": branch, "commit": {"sha": branch}}
                for branch in ("main", "release", "other")
            ])
        if request.url.path.endswith("/environments"):
            page = int(request.url.params["page"])
            environment_pages.append(page)
            if environment_error:
                return httpx.Response(403)
            headers = {}
            if page == 1 and len(environments) > 1:
                headers = {"Link": '<https://api.github.com/test?page=2>; rel="next"'}
            return httpx.Response(
                200,
                json={"environments": [{"name": name} for name in environments[page - 1:page]]},
                headers=headers,
            )
        return file_response(
            other_workflow if request.url.params["ref"] == "other" else environment_workflow
        )

    sync = workflow_sync.ImageBuildWorkflowSync()
    async with httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(transport)
    ) as client:
        result = await sync.sync(client, TEST_REPOSITORY)
        assert result.changed == 3 and result.failed == 0
        assert environment_pages == [1, 2]
        for branch in ("main", "release"):
            field = store.definitions[branch].inputs_json[-1]
            assert field["type"] == "environment"
            assert field["options"] == ["production", "staging"]
            assert field["default"] == "staging"

        environments[:] = ["qa"]
        result = await sync.sync(client, TEST_REPOSITORY)
        assert result.changed == 2 and result.failed == 0
        assert environment_pages == [1, 2, 1]
        assert store.definitions["main"].inputs_json[-1]["options"] == ["qa"]

        environment_error = True
        other_workflow = WORKFLOW
        result = await sync.sync(client, TEST_REPOSITORY)
        assert result.changed == 1 and result.failed == 2
        assert environment_pages == [1, 2, 1, 1]
        assert store.definitions["main"].inputs_json[-1]["options"] == ["qa"]
        assert store.definitions["release"].inputs_json[-1]["options"] == ["qa"]
        assert store.definitions["other"].inputs_json[0]["name"] == "vllm_ascend_branch"


@pytest.mark.asyncio
async def test_sync_reads_configured_repository_on_every_run(monkeypatch):
    store = DefinitionStore()
    monkeypatch.setattr(workflow_sync, "SessionLocal", store)
    monkeypatch.setattr(workflow_sync, "get_github_token", AsyncMock(return_value="token"))

    @asynccontextmanager
    async def lock():
        yield

    monkeypatch.setattr(workflow_sync, "_workflow_sync_lock", lock)
    requests = []

    def transport(request):
        assert request.url.path.startswith(f"/repos/{store.repository}/")
        requests.append(request.url.path)
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json=[
                {"name": "main", "commit": {"sha": "shared-main"}},
                {"name": "release", "commit": {"sha": "shared-release"}},
            ])
        if request.url.params["ref"] == "shared-release" and store.repository == TEST_REPOSITORY:
            return httpx.Response(404)
        source = WORKFLOW if store.repository == TEST_REPOSITORY else "on: workflow_dispatch"
        return file_response(source)

    def github_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url=kwargs["base_url"])

    monkeypatch.setattr(workflow_sync, "GitHubAsyncClient", github_client)
    sync = workflow_sync.ImageBuildWorkflowSync()

    assert (await sync.run()).changed == 1
    store.repository = OTHER_REPOSITORY
    assert (await sync.run()).changed == 2

    assert len(requests) == 6
    assert set(store.definitions) == {"main"}
    assert store.definitions["main"].inputs_json
    assert store.repositories[OTHER_REPOSITORY]["main"].inputs_json == []
    assert store.repositories[OTHER_REPOSITORY]["release"].is_available
