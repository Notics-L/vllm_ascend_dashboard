from types import SimpleNamespace

import pytest
from cryptography.fernet import InvalidToken

import resource_dashboard.service as resource_dashboard_service
from resource_dashboard.metrics_query import PersistedResourceMetricsService
from resource_dashboard.service import ResourceDashboardService, parse_job_workflow_ref


def test_job_workflow_ref_uses_its_own_repository_for_all_github_links() -> None:
    assert parse_job_workflow_ref(
        "acme/ml-ci/.github/workflows/nightly-a2.yml@refs/pull/42/merge"
    ) == {
        "repository": "acme/ml-ci",
        "repository_url": "https://github.com/acme/ml-ci",
        "workflow_path": "nightly-a2.yml",
        "workflow_url": "https://github.com/acme/ml-ci/actions/workflows/nightly-a2.yml",
        "pr_number": 42,
        "pr_url": "https://github.com/acme/ml-ci/pull/42",
    }


def test_job_workflow_ref_keeps_non_pr_workflows_linkable() -> None:
    assert parse_job_workflow_ref(
        "acme/ml-ci/.github/workflows/nightly.yml@refs/heads/main"
    ) == {
        "repository": "acme/ml-ci",
        "repository_url": "https://github.com/acme/ml-ci",
        "workflow_path": "nightly.yml",
        "workflow_url": "https://github.com/acme/ml-ci/actions/workflows/nightly.yml",
    }


def test_workload_pod_uses_its_own_workflow_annotation_before_runner_name() -> None:
    service = ResourceDashboardService()
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="buildkite-workload-1",
            namespace="vllm-project",
            labels={},
            annotations={
                "actions.github.com/job-workflow-ref": (
                    "acme/workload/.github/workflows/nightly.yml@refs/pull/7/merge"
                ),
            },
        ),
    )

    assert service._pod_workflow_info(pod, {}) == {
        "repository": "acme/workload",
        "repository_url": "https://github.com/acme/workload",
        "workflow_path": "nightly.yml",
        "workflow_url": "https://github.com/acme/workload/actions/workflows/nightly.yml",
        "pr_number": 7,
        "pr_url": "https://github.com/acme/workload/pull/7",
        "job_workflow_ref": "acme/workload/.github/workflows/nightly.yml@refs/pull/7/merge",
    }


def test_persisted_resource_metrics_restore_github_links() -> None:
    row = SimpleNamespace(
        cluster_id=1,
        cluster_name="A2",
        top_pods_json=[{
            "name": "runner-1",
            "namespace": "vllm-project",
            "npu": 8,
            "repository": "acme/ml-ci",
            "repository_url": "https://github.com/acme/ml-ci",
            "workflow_path": "nightly.yml",
            "workflow_url": "https://github.com/acme/ml-ci/actions/workflows/nightly.yml",
            "pr_number": 42,
            "pr_url": "https://github.com/acme/ml-ci/pull/42",
            "job_workflow_ref": "acme/ml-ci/.github/workflows/nightly.yml@refs/pull/42/merge",
        }],
    )

    pod = PersistedResourceMetricsService._pods(row, include_pods=True)[0]

    assert pod.repository == "acme/ml-ci"
    assert pod.workflow_url == "https://github.com/acme/ml-ci/actions/workflows/nightly.yml"
    assert pod.pr_url == "https://github.com/acme/ml-ci/pull/42"


def test_pod_history_metadata_requires_one_exact_namespace_and_pod_name_match() -> None:
    payload = {
        "name": "workload-0",
        "source": "github-action",
        "groups": {"vllm-project": {"device_count": 1}},
        "extend_env_comments": {
            "workflow_ref": "acme/ml-ci/.github/workflows/nightly.yml@refs/pull/42/merge",
            "workflow_run_id": "123",
        },
    }
    result = PersistedResourceMetricsService._history_metadata_by_pod_key([payload])

    assert result[("vllm-project", "workload-0")]["repository"] == "acme/ml-ci"
    assert result[("vllm-project", "workload-0")]["run_url"] == "https://github.com/acme/ml-ci/actions/runs/123"
    assert PersistedResourceMetricsService._history_metadata_by_pod_key([payload, payload]) == {}


@pytest.mark.asyncio
async def test_pending_pods_are_separated_from_active_npu_usage(monkeypatch) -> None:
    service = ResourceDashboardService()
    resource_name = "huawei.com/Ascend910"
    node = SimpleNamespace(
        metadata=SimpleNamespace(name="node-1"),
        status=SimpleNamespace(allocatable={"cpu": "8", "memory": "16Gi", resource_name: "16"}),
    )
    def pod(name: str, phase: str) -> SimpleNamespace:
        return SimpleNamespace(
            metadata=SimpleNamespace(name=name, namespace="vllm-project", labels={}, creation_timestamp=None),
            status=SimpleNamespace(phase=phase, start_time=None, container_statuses=[]),
            spec=SimpleNamespace(
                node_name="node-1",
                containers=[SimpleNamespace(name="job", resources=SimpleNamespace(requests={resource_name: "8"}))],
                init_containers=[],
                overhead={},
            ),
        )

    class FakeClient:
        async def close(self) -> None:
            return None

    async def list_test_nodes(_client):
        return [node]

    async def list_test_pods(_client, _namespaces, _selector):
        return [pod("running", "Running"), pod("pending", "Pending")]

    async def list_test_runners(_client, _namespace):
        return []

    async def create_client(_cluster):
        return FakeClient()

    monkeypatch.setattr(service.client_factory, "create_api_client", create_client)
    monkeypatch.setattr(resource_dashboard_service, "list_nodes", list_test_nodes)
    monkeypatch.setattr(resource_dashboard_service, "list_pods", list_test_pods)
    monkeypatch.setattr(resource_dashboard_service, "list_ephemeral_runners", list_test_runners)
    cluster = SimpleNamespace(
        id=1, name="A3", namespaces="vllm-project", default_label_selector=None, npu_resource_name=resource_name,
    )

    summary, _, _ = await service.build_cluster_summary(cluster, None, False)

    assert summary.used.npu == 8
    assert summary.available.npu == 8
    assert summary.executing_pods_count == 1
    assert summary.pending_pods_count == 1
    assert summary.pending_npu == 8


@pytest.mark.asyncio
async def test_empty_exception_message_is_not_persisted_as_success() -> None:
    service = ResourceDashboardService()

    async def raise_invalid_token(*_args, **_kwargs):
        raise InvalidToken

    service.build_cluster_summary = raise_invalid_token  # type: ignore[method-assign]
    cluster = SimpleNamespace(
        id=1,
        name="dev-a2",
        namespaces="vllm-project",
        default_label_selector=None,
    )

    summary, _, _ = await service._safe_build_cluster_summary(cluster, None, False)

    assert summary.error == "InvalidToken"
