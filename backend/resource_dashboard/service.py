import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from contracts.schemas import (
    ClusterResourceSummary,
    ResourceDashboardResponse,
    ResourceNodeInfo,
    ResourcePodInfo,
    ResourceQuantity,
)
from infrastructure.clients.kubernetes_client import (
    KubernetesClientFactory,
    list_ephemeral_runners,
    list_nodes,
    list_pods,
)
from infrastructure.persistence.models import KubernetesClusterConfig

ACTIVE_PHASES = {"Running", "Unknown"}
PENDING_PHASES = {"Pending"}
EXECUTED_PHASES = {"Succeeded", "Failed"}
RESOURCE_DASHBOARD_NAMESPACE = "vllm-project"
_JOB_WORKFLOW_REF_PATTERN = re.compile(
    r"^(?:https://github\.com/)?(?P<repository>[^/\s]+/[^/\s]+)/"
    r"\.github/workflows/(?P<workflow_path>[^@\s]+)@(?P<ref>.+)$"
)
from kubernetes_asyncio.client import CoreV1Api


def parse_job_workflow_ref(job_workflow_ref: str | None) -> dict[str, str | int] | None:
    """Extract GitHub repository, workflow, and optional PR links from ARC metadata."""

    value = job_workflow_ref.strip() if isinstance(job_workflow_ref, str) else ""
    match = _JOB_WORKFLOW_REF_PATTERN.fullmatch(value)
    if not match:
        return None

    repository = match.group("repository")
    workflow_path = match.group("workflow_path")
    repository_url = f"https://github.com/{repository}"
    info: dict[str, str | int] = {
        "repository": repository,
        "repository_url": repository_url,
        "workflow_path": workflow_path,
        "workflow_url": f"{repository_url}/actions/workflows/{quote(workflow_path, safe='/')}",
    }
    pr_match = re.search(r"(?:^|/)refs/pull/(\d+)/", match.group("ref"))
    if pr_match:
        pr_number = int(pr_match.group(1))
        info["pr_number"] = pr_number
        info["pr_url"] = f"{repository_url}/pull/{pr_number}"
    return info


class ResourceDashboardService:
    def __init__(self) -> None:
        self.client_factory = KubernetesClientFactory()

    async def pod_scheduling_diagnostics(
        self, cluster: KubernetesClusterConfig, *, namespace: str, name: str
    ) -> dict[str, Any]:
        """Read one live Pod with the server-held encrypted kubeconfig."""
        api_client = await self.client_factory.create_api_client(cluster)
        try:
            pod = await CoreV1Api(api_client).read_namespaced_pod(
                name=name, namespace=namespace, _request_timeout=30,
            )
        finally:
            await api_client.close()

        spec = pod.spec
        status = pod.status
        scheduled = next(
            (condition for condition in (getattr(status, "conditions", None) or [])
             if getattr(condition, "type", None) == "PodScheduled"),
            None,
        )
        affinity = getattr(getattr(spec, "affinity", None), "node_affinity", None)
        required_affinity = getattr(affinity, "required_during_scheduling_ignored_during_execution", None)
        return {
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "namespace": namespace,
            "name": name,
            "phase": getattr(status, "phase", None),
            "node_name": getattr(spec, "node_name", None),
            "scheduler_name": getattr(spec, "scheduler_name", None),
            "priority_class_name": getattr(spec, "priority_class_name", None),
            "resource_requests": self._pod_requests(pod, cluster.npu_resource_name),
            "node_selector": dict(getattr(spec, "node_selector", None) or {}),
            "required_node_affinity": required_affinity.to_dict() if required_affinity else None,
            "tolerations": [toleration.to_dict() for toleration in (getattr(spec, "tolerations", None) or [])],
            "pod_scheduled_condition": {
                "status": getattr(scheduled, "status", None),
                "reason": getattr(scheduled, "reason", None),
                "message": getattr(scheduled, "message", None),
                "last_transition_time": getattr(scheduled, "last_transition_time", None),
            } if scheduled else None,
        }

    async def build_dashboard(
        self,
        clusters: list[KubernetesClusterConfig],
        label_selector: str | None = None,
        include_pods: bool = True,
    ) -> ResourceDashboardResponse:
        cluster_tasks = [
            self._safe_build_cluster_summary(cluster, label_selector, include_pods)
            for cluster in clusters
        ]
        results = await asyncio.gather(*cluster_tasks)

        summaries = [result[0] for result in results]
        executing_pods = [pod for result in results for pod in result[1]]
        executed_pods = [pod for result in results for pod in result[2]]

        overall = self._build_overall_summary(summaries)
        return ResourceDashboardResponse(
            generated_at=datetime.now(UTC),
            overall=overall,
            clusters=summaries,
            executing_pods=executing_pods,
            executed_pods=executed_pods,
        )

    async def _safe_build_cluster_summary(
        self,
        cluster: KubernetesClusterConfig,
        label_selector: str | None,
        include_pods: bool,
    ) -> tuple[ClusterResourceSummary, list[ResourcePodInfo], list[ResourcePodInfo]]:
        namespaces = self._cluster_namespaces(cluster)
        try:
            return await asyncio.wait_for(
                self.build_cluster_summary(cluster, label_selector, include_pods),
                timeout=45,
            )
        except Exception as exc:
            # Some infrastructure exceptions (notably Fernet InvalidToken)
            # stringify to an empty value.  Keep a non-empty failure marker so
            # callers never persist a failed collection as a valid zero-card
            # snapshot.
            error_message = str(exc) or type(exc).__name__
            summary = ClusterResourceSummary(
                cluster_id=cluster.id,
                cluster_name=cluster.name,
                total=ResourceQuantity(),
                used=ResourceQuantity(),
                available=ResourceQuantity(),
                scope={
                    "namespaces": namespaces,
                    "label_selector": label_selector or cluster.default_label_selector,
                },
                error=error_message,
            )
            return summary, [], []

    def _cluster_namespaces(self, cluster: KubernetesClusterConfig) -> list[str]:
        raw_namespaces = getattr(cluster, "namespaces", None) or RESOURCE_DASHBOARD_NAMESPACE
        namespaces = []
        seen = set()
        for item in raw_namespaces.split(","):
            namespace = item.strip()
            if namespace and namespace not in seen:
                namespaces.append(namespace)
                seen.add(namespace)
        return namespaces or [RESOURCE_DASHBOARD_NAMESPACE]

    async def build_cluster_summary(
        self,
        cluster: KubernetesClusterConfig,
        label_selector: str | None,
        include_pods: bool,
    ) -> tuple[ClusterResourceSummary, list[ResourcePodInfo], list[ResourcePodInfo]]:
        resolved_label_selector = label_selector if label_selector is not None else cluster.default_label_selector
        namespaces = self._cluster_namespaces(cluster)

        api_client = await self.client_factory.create_api_client(cluster)
        try:
            nodes = await list_nodes(api_client)
            pods = await list_pods(api_client, namespaces, resolved_label_selector)
            runner_workflow_info = await self._runner_workflow_info(api_client, namespaces)
        finally:
            await api_client.close()

        total = self._sum_node_allocatable(nodes, cluster.npu_resource_name)
        node_resources = self._build_node_resources(nodes, cluster.npu_resource_name)
        used = ResourceQuantity()
        running_instances = 0
        pending_pods = []
        executing_infos: list[ResourcePodInfo] = []
        executed_infos: list[ResourcePodInfo] = []

        visible_pods = []
        for pod in pods:
            phase = pod.status.phase if pod.status else None
            pod_requests = self._pod_requests(pod, cluster.npu_resource_name)
            if pod_requests.npu <= 0:
                continue
            visible_pods.append(pod)
            node_resource = node_resources.get(getattr(pod.spec, "node_name", None))
            if phase == "Running":
                running_instances += 1
                if node_resource:
                    node_resource.running_instances += 1
            if phase in ACTIVE_PHASES:
                used = self._add_quantity(used, pod_requests)
                if node_resource:
                    node_resource.used = self._add_quantity(node_resource.used, pod_requests)
                    node_resource.executing_pods_count += 1
                if include_pods:
                    executing_infos.append(self._pod_info(cluster, pod, pod_requests, runner_workflow_info))
            elif phase in PENDING_PHASES:
                pending_pods.append(pod)
                if include_pods:
                    executing_infos.append(self._pod_info(cluster, pod, pod_requests, runner_workflow_info))
            elif phase in EXECUTED_PHASES and include_pods:
                executed_infos.append(self._pod_info(cluster, pod, pod_requests, runner_workflow_info))

        summary = ClusterResourceSummary(
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            total=total,
            used=used,
            available=ResourceQuantity(
                cpu_cores=max(total.cpu_cores - used.cpu_cores, 0),
                memory_bytes=max(total.memory_bytes - used.memory_bytes, 0),
                npu=max(total.npu - used.npu, 0),
            ),
            running_instances=running_instances,
            executing_pods_count=len([pod for pod in visible_pods if pod.status and pod.status.phase in ACTIVE_PHASES]),
            pending_pods_count=len(pending_pods),
            pending_npu=sum(self._pod_requests(pod, cluster.npu_resource_name).npu for pod in pending_pods),
            executed_pods_count=len([pod for pod in visible_pods if pod.status and pod.status.phase in EXECUTED_PHASES]),
            node_resources=self._finalize_node_resources(node_resources),
            executing_pods=executing_infos,
            scope={
                "namespaces": namespaces,
                "label_selector": resolved_label_selector,
            },
        )
        return summary, executing_infos, executed_infos

    async def test_cluster(self, cluster: KubernetesClusterConfig) -> tuple[int, int]:
        namespaces = self._cluster_namespaces(cluster)
        api_client = await self.client_factory.create_api_client(cluster)
        try:
            nodes = await list_nodes(api_client)
            pods = await list_pods(api_client, namespaces, cluster.default_label_selector)
            return len(nodes), len(pods)
        finally:
            await api_client.close()

    def _build_overall_summary(self, summaries: list[ClusterResourceSummary]) -> ClusterResourceSummary:
        total = ResourceQuantity()
        used = ResourceQuantity()
        available = ResourceQuantity()
        running_instances = 0
        executing_count = 0
        pending_count = 0
        pending_npu = 0.0
        executed_count = 0

        for summary in summaries:
            if summary.error:
                continue
            total = self._add_quantity(total, summary.total)
            used = self._add_quantity(used, summary.used)
            available = self._add_quantity(available, summary.available)
            running_instances += summary.running_instances
            executing_count += summary.executing_pods_count
            pending_count += summary.pending_pods_count
            pending_npu += summary.pending_npu
            executed_count += summary.executed_pods_count

        return ClusterResourceSummary(
            cluster_id=0,
            cluster_name="总资源",
            total=total,
            used=used,
            available=available,
            running_instances=running_instances,
            executing_pods_count=executing_count,
            pending_pods_count=pending_count,
            pending_npu=pending_npu,
            executed_pods_count=executed_count,
            scope={"all_clusters": True},
        )

    def _sum_node_allocatable(self, nodes: list[Any], npu_resource_name: str) -> ResourceQuantity:
        total = ResourceQuantity()
        for node in nodes:
            allocatable = node.status.allocatable or {}
            total.cpu_cores += parse_cpu(allocatable.get("cpu"))
            total.memory_bytes += parse_memory(allocatable.get("memory"))
            total.npu += parse_number(allocatable.get(npu_resource_name))
        return total

    def _build_node_resources(self, nodes: list[Any], npu_resource_name: str) -> dict[str, ResourceNodeInfo]:
        node_resources = {}
        for node in nodes:
            node_name = node.metadata.name
            allocatable = node.status.allocatable or {}
            total = ResourceQuantity(
                cpu_cores=parse_cpu(allocatable.get("cpu")),
                memory_bytes=parse_memory(allocatable.get("memory")),
                npu=parse_number(allocatable.get(npu_resource_name)),
            )
            node_resources[node_name] = ResourceNodeInfo(
                node_name=node_name,
                total=total,
                used=ResourceQuantity(),
                available=total,
            )
        return node_resources

    def _finalize_node_resources(self, node_resources: dict[str, ResourceNodeInfo]) -> list[ResourceNodeInfo]:
        for node_resource in node_resources.values():
            node_resource.available = ResourceQuantity(
                cpu_cores=max(node_resource.total.cpu_cores - node_resource.used.cpu_cores, 0),
                memory_bytes=max(node_resource.total.memory_bytes - node_resource.used.memory_bytes, 0),
                npu=max(node_resource.total.npu - node_resource.used.npu, 0),
            )
        return sorted(node_resources.values(), key=lambda node_resource: node_resource.node_name)

    async def _runner_workflow_info(self, api_client: Any, namespaces: list[str]) -> dict[str, dict[str, Any]]:
        workflow_info = {}
        for namespace in namespaces:
            try:
                runners = await list_ephemeral_runners(api_client, namespace)
            except Exception:
                continue

            for runner in runners:
                metadata = runner.get("metadata", {})
                name = metadata.get("name")
                runner_namespace = metadata.get("namespace") or namespace
                job_workflow_ref = runner.get("status", {}).get("jobWorkflowRef")
                if not name or not job_workflow_ref:
                    continue
                info = parse_job_workflow_ref(job_workflow_ref)
                if info is None:
                    continue
                info["job_workflow_ref"] = job_workflow_ref
                workflow_info[name] = info
                workflow_info[f"{runner_namespace}/{name}"] = info
        return workflow_info

    def _pod_requests(self, pod: Any, npu_resource_name: str) -> ResourceQuantity:
        regular = ResourceQuantity()
        init_max = ResourceQuantity()

        for container in pod.spec.containers or []:
            requests = container.resources.requests or {}
            regular.cpu_cores += parse_cpu(requests.get("cpu"))
            regular.memory_bytes += parse_memory(requests.get("memory"))
            regular.npu += parse_number(requests.get(npu_resource_name))

        for container in pod.spec.init_containers or []:
            requests = container.resources.requests or {}
            init_max.cpu_cores = max(init_max.cpu_cores, parse_cpu(requests.get("cpu")))
            init_max.memory_bytes = max(init_max.memory_bytes, parse_memory(requests.get("memory")))
            init_max.npu = max(init_max.npu, parse_number(requests.get(npu_resource_name)))

        effective = ResourceQuantity(
            cpu_cores=max(regular.cpu_cores, init_max.cpu_cores),
            memory_bytes=max(regular.memory_bytes, init_max.memory_bytes),
            npu=max(regular.npu, init_max.npu),
        )

        overhead = pod.spec.overhead or {}
        effective.cpu_cores += parse_cpu(overhead.get("cpu"))
        effective.memory_bytes += parse_memory(overhead.get("memory"))
        effective.npu += parse_number(overhead.get(npu_resource_name))
        return effective

    def _pod_workflow_info(
        self,
        pod: Any,
        runner_workflow_info: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Resolve GitHub metadata directly from a workload Pod when present.

        ARC runner Pods can be matched by name, but workload Pods created by
        a CI step usually have a different name.  Their workflow reference is
        commonly propagated as a label or annotation, so prefer that exact
        source before falling back to the runner-name convention.
        """
        metadata = pod.metadata
        labels = metadata.labels or {}
        annotations = getattr(metadata, "annotations", None) or {}
        reference_keys = (
            "jobWorkflowRef", "job_workflow_ref", "job-workflow-ref",
            "actions.github.com/jobWorkflowRef",
            "actions.github.com/job-workflow-ref",
        )
        for source in (annotations, labels):
            for key in reference_keys:
                info = parse_job_workflow_ref(source.get(key))
                if info is not None:
                    info["job_workflow_ref"] = source[key]
                    return info

        pod_namespace = metadata.namespace
        pod_name = metadata.name
        namespaced_pod_name = f"{pod_namespace}/{pod_name}"
        runner_names = [namespaced_pod_name, pod_name]
        for key in (
            "actions.github.com/runner-name", "runner-name",
            "actions.github.com/ephemeral-runner",
        ):
            runner_name = labels.get(key)
            if runner_name:
                runner_names.extend((f"{pod_namespace}/{runner_name}", runner_name))
        if pod_name.endswith("-workflow"):
            runner_name = pod_name[: -len("-workflow")]
            runner_names.extend((f"{pod_namespace}/{runner_name}", runner_name))
        for runner_name in runner_names:
            if runner_name in runner_workflow_info:
                return runner_workflow_info[runner_name]
        return None

    def _pod_info(
        self,
        cluster: KubernetesClusterConfig,
        pod: Any,
        requests: ResourceQuantity,
        runner_workflow_info: dict[str, dict[str, Any]],
    ) -> ResourcePodInfo:
        status = pod.status
        started_at = getattr(status, "start_time", None) if status else None
        finished_at = self._finished_at(status)
        created_at = pod.metadata.creation_timestamp if pod.metadata else None
        duration_seconds = None
        if started_at:
            end_time = finished_at or datetime.now(UTC)
            duration_seconds = int((end_time - started_at).total_seconds())

        workflow_info = self._pod_workflow_info(pod, runner_workflow_info) if pod.metadata else None
        scheduling_reason, scheduling_message = self._scheduling_condition(status)

        return ResourcePodInfo(
            cluster_id=cluster.id,
            cluster_name=cluster.name,
            namespace=pod.metadata.namespace,
            name=pod.metadata.name,
            phase=status.phase if status else None,
            scheduling_reason=scheduling_reason,
            scheduling_message=scheduling_message,
            node_selector=dict(getattr(pod.spec, "node_selector", None) or {}),
            status=self._container_status_summary(status),
            node_name=pod.spec.node_name,
            labels=pod.metadata.labels or {},
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            repository=workflow_info.get("repository") if workflow_info else None,
            repository_url=workflow_info.get("repository_url") if workflow_info else None,
            workflow_path=workflow_info.get("workflow_path") if workflow_info else None,
            workflow_url=workflow_info.get("workflow_url") if workflow_info else None,
            pr_number=workflow_info.get("pr_number") if workflow_info else None,
            pr_url=workflow_info.get("pr_url") if workflow_info else None,
            job_workflow_ref=workflow_info.get("job_workflow_ref") if workflow_info else None,
            requests=requests,
            containers=[container.name for container in pod.spec.containers or []],
        )

    @staticmethod
    def _scheduling_condition(status: Any) -> tuple[str | None, str | None]:
        """Extract the Kubernetes scheduler explanation for a Pending Pod."""
        for condition in getattr(status, "conditions", None) or []:
            if getattr(condition, "type", None) == "PodScheduled" and getattr(condition, "status", None) != "True":
                return getattr(condition, "reason", None), getattr(condition, "message", None)
        return None, None

    def _finished_at(self, status: Any) -> datetime | None:
        if not status or not status.container_statuses:
            return None
        finished_times = []
        for container_status in status.container_statuses:
            terminated = getattr(container_status.state, "terminated", None)
            if terminated and terminated.finished_at:
                finished_times.append(terminated.finished_at)
        return max(finished_times) if finished_times else None

    def _container_status_summary(self, status: Any) -> str | None:
        if not status or not status.container_statuses:
            return None
        waiting_reasons = []
        for container_status in status.container_statuses:
            waiting = getattr(container_status.state, "waiting", None)
            if waiting and waiting.reason:
                waiting_reasons.append(waiting.reason)
        return ", ".join(waiting_reasons) if waiting_reasons else None

    def _add_quantity(self, left: ResourceQuantity, right: ResourceQuantity) -> ResourceQuantity:
        return ResourceQuantity(
            cpu_cores=left.cpu_cores + right.cpu_cores,
            memory_bytes=left.memory_bytes + right.memory_bytes,
            npu=left.npu + right.npu,
        )


def parse_cpu(value: str | int | float | None) -> float:
    if value is None:
        return 0
    text = str(value)
    if text.endswith("m"):
        return float(Decimal(text[:-1]) / Decimal(1000))
    return float(Decimal(text))


def parse_memory(value: str | int | float | None) -> int:
    if value is None:
        return 0
    text = str(value)
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
    }
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return int(Decimal(text[: -len(suffix)]) * multiplier)
    return int(Decimal(text))


def parse_number(value: str | int | float | None) -> float:
    if value is None:
        return 0
    return float(Decimal(str(value)))
