from types import SimpleNamespace

from cryptography.fernet import InvalidToken
import pytest

import resource_dashboard.service as resource_dashboard_service
from resource_dashboard.service import ResourceDashboardService


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
