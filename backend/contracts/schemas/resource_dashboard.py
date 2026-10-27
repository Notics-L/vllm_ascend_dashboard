from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_RESOURCE_DASHBOARD_NAMESPACE = "vllm-project"


def normalize_namespaces_value(value: str | None) -> str:
    namespaces = []
    seen = set()
    for item in (value or "").split(","):
        namespace = item.strip()
        if namespace and namespace not in seen:
            namespaces.append(namespace)
            seen.add(namespace)
    return ",".join(namespaces) if namespaces else DEFAULT_RESOURCE_DASHBOARD_NAMESPACE


class KubernetesClusterBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    context: str | None = Field(None, max_length=200)
    default_label_selector: str | None = Field(None, max_length=500)
    namespaces: str = Field("vllm-project", min_length=1, max_length=500, description="逗号分隔的命名空间列表")
    npu_resource_name: str = Field("huawei.com/Ascend910", min_length=1, max_length=200)
    enabled: bool = True
    display_order: int = 0

    @field_validator("default_label_selector", "context")
    @classmethod
    def normalize_empty_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("namespaces", mode="before")
    @classmethod
    def normalize_namespaces(cls, value: str) -> str:
        return normalize_namespaces_value(value)


class KubernetesClusterCreate(KubernetesClusterBase):
    kubeconfig: str = Field(..., min_length=1)


class KubernetesClusterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    kubeconfig: str | None = None
    context: str | None = Field(None, max_length=200)
    default_label_selector: str | None = Field(None, max_length=500)
    namespaces: str | None = Field(None, min_length=1, max_length=500)
    npu_resource_name: str | None = Field(None, min_length=1, max_length=200)
    enabled: bool | None = None
    display_order: int | None = None

    @field_validator("kubeconfig", "default_label_selector", "context")
    @classmethod
    def normalize_empty_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("namespaces", mode="before")
    @classmethod
    def normalize_namespaces(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_namespaces_value(value)


class KubernetesClusterResponse(KubernetesClusterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kubeconfig_configured: bool = True
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


class KubernetesClusterTestResponse(BaseModel):
    success: bool
    message: str
    node_count: int = 0
    pod_count: int = 0


class ResourceQuantity(BaseModel):
    cpu_cores: float = 0
    memory_bytes: float = 0
    npu: float = 0


class ResourceNodeInfo(BaseModel):
    node_name: str
    total: ResourceQuantity
    used: ResourceQuantity
    available: ResourceQuantity
    running_instances: int = 0
    executing_pods_count: int = 0


class ResourcePodInfo(BaseModel):
    cluster_id: int
    cluster_name: str
    namespace: str
    name: str
    phase: str | None = None
    # The scheduler's latest PodScheduled condition is retained in resource
    # snapshots so a long Pending wait can be explained after collection.
    scheduling_reason: str | None = None
    scheduling_message: str | None = None
    node_selector: dict[str, str] = Field(default_factory=dict)
    node_name: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Timestamp of the latest resource collection that observed this Pod.
    # It lets the timeline distinguish a currently Pending Pod from an older
    # Pending observation after the Pod has disappeared from Kubernetes.
    last_observed_at: datetime | None = None
    duration_seconds: int | None = None
    repository: str | None = None
    repository_url: str | None = None
    workflow_path: str | None = None
    workflow_url: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    job_workflow_ref: str | None = None
    workflow_run_id: int | None = None
    run_url: str | None = None
    requests: ResourceQuantity
    containers: list[str] = Field(default_factory=list)


class ClusterResourceSummary(BaseModel):
    cluster_id: int
    cluster_name: str
    total: ResourceQuantity
    used: ResourceQuantity
    available: ResourceQuantity
    running_instances: int = 0
    executing_pods_count: int = 0
    pending_pods_count: int = 0
    pending_npu: float = 0
    executed_pods_count: int = 0
    node_resources: list[ResourceNodeInfo] = Field(default_factory=list)
    executing_pods: list[ResourcePodInfo] = Field(default_factory=list)
    scope: dict[str, Any]
    error: str | None = None


class ResourceDashboardResponse(BaseModel):
    overall: ClusterResourceSummary
    clusters: list[ClusterResourceSummary]
    executing_pods: list[ResourcePodInfo]
    executed_pods: list[ResourcePodInfo]


class ResourcePodTimelineResponse(BaseModel):
    """Pod lifecycles observed in a bounded resource-metrics window."""

    generated_at: datetime
    window_start: datetime
    pods: list[ResourcePodInfo] = Field(default_factory=list)


class PodSchedulingDiagnosticsResponse(BaseModel):
    """Read-only live scheduling view; kubeconfig and tokens never leave the server."""

    cluster_id: int
    cluster_name: str
    namespace: str
    name: str
    phase: str | None = None
    node_name: str | None = None
    scheduler_name: str | None = None
    priority_class_name: str | None = None
    resource_requests: ResourceQuantity
    node_selector: dict[str, str] = Field(default_factory=dict)
    required_node_affinity: dict[str, Any] | None = None
    tolerations: list[dict[str, Any]] = Field(default_factory=list)
    pod_scheduled_condition: dict[str, Any] | None = None
