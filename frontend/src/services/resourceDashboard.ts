import api from './api'

export interface KubernetesCluster {
  id: number
  name: string
  description?: string | null
  context?: string | null
  default_label_selector?: string | null
  namespaces: string
  npu_resource_name: string
  enabled: boolean
  display_order: number
  kubeconfig_configured: boolean
  created_by?: number | null
  created_at: string
  updated_at: string
}

export interface KubernetesClusterCreate {
  name: string
  description?: string | null
  kubeconfig: string
  context?: string | null
  default_label_selector?: string | null
  namespaces: string
  npu_resource_name: string
  enabled: boolean
  display_order: number
}

export interface KubernetesClusterUpdate {
  name?: string
  description?: string | null
  kubeconfig?: string | null
  context?: string | null
  default_label_selector?: string | null
  namespaces?: string
  npu_resource_name?: string
  enabled?: boolean
  display_order?: number
}

export interface KubernetesClusterTestResponse {
  success: boolean
  message: string
  node_count: number
  pod_count: number
}

export interface ResourceQuantity {
  cpu_cores: number
  memory_bytes: number
  npu: number
}

export interface ResourceNodeInfo {
  node_name: string
  total: ResourceQuantity
  used: ResourceQuantity
  available: ResourceQuantity
  running_instances: number
  executing_pods_count: number
}

export interface ClusterResourceSummary {
  cluster_id: number
  cluster_name: string
  total: ResourceQuantity
  used: ResourceQuantity
  available: ResourceQuantity
  running_instances: number
  executing_pods_count: number
  pending_pods_count?: number
  pending_npu?: number
  executed_pods_count: number
  node_resources: ResourceNodeInfo[]
  executing_pods: ResourcePodInfo[]
  scope: Record<string, unknown>
  error?: string | null
}

export interface ResourcePodInfo {
  cluster_id: number
  cluster_name: string
  namespace: string
  name: string
  phase?: string | null
  status?: string | null
  node_name?: string | null
  labels: Record<string, string>
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  last_observed_at?: string | null
  duration_seconds?: number | null
  repository?: string | null
  repository_url?: string | null
  workflow_path?: string | null
  workflow_url?: string | null
  pr_number?: number | null
  pr_url?: string | null
  job_workflow_ref?: string | null
  workflow_run_id?: number | null
  run_url?: string | null
  requests: ResourceQuantity
  containers: string[]
}

export interface ResourceDashboardResponse {
  generated_at: string
  overall: ClusterResourceSummary
  clusters: ClusterResourceSummary[]
  executing_pods: ResourcePodInfo[]
  executed_pods: ResourcePodInfo[]
}

export interface ResourceDashboardParams {
  cluster_ids?: number[]
  label_selector?: string
  include_pods?: boolean
}

export interface ResourcePodTimelineResponse {
  generated_at: string
  window_start: string
  pods: ResourcePodInfo[]
}

export interface NodeUtilizationBucket {
  start: string
  end: string
  utilization: number | null
  active_seconds: number
  window_seconds: number
  coverage: number
}

export interface NodeUtilizationHeatmap {
  pool: string | null
  start: string
  end: string
  bucket: string
  overall_utilization: number
  nodes: Array<{
    cluster_id: number
    cluster_name: string
    node_name: string
    buckets: NodeUtilizationBucket[]
  }>
}

const buildParams = (params: ResourceDashboardParams) => {
  const searchParams = new URLSearchParams()
  params.cluster_ids?.forEach(id => searchParams.append('cluster_ids', String(id)))
  if (params.label_selector) {
    searchParams.append('label_selector', params.label_selector)
  }
  if (params.include_pods !== undefined) {
    searchParams.append('include_pods', String(params.include_pods))
  }
  return searchParams
}

export const getEnabledResourceClusters = async () => {
  const response = await api.get<KubernetesCluster[]>('/resource-dashboard/clusters/enabled')
  return response.data
}

export const getClusterSummary = async (clusterId: number) => {
  const response = await api.get<ClusterResourceSummary>(`/resource-dashboard/cluster/${clusterId}/summary`)
  return response.data
}

export const getResourceDashboard = async (params: ResourceDashboardParams = {}) => {
  const response = await api.get<ResourceDashboardResponse>('/resource-dashboard/summary', {
    params: buildParams(params),
  })
  return response.data
}

export const getResourcePodTimeline = async (params: { cluster_ids?: number[]; hours?: number } = {}) => {
  const searchParams = buildParams(params)
  if (params.hours !== undefined) {
    searchParams.set('hours', String(params.hours))
  }
  const response = await api.get<ResourcePodTimelineResponse>('/resource-dashboard/pod-timeline', {
    params: searchParams,
  })
  return response.data
}

export const getNodeUtilizationHeatmap = async (
  pool?: string,
  params?: { start?: string; end?: string; bucket_hours?: number },
) => {
  const response = await api.get<NodeUtilizationHeatmap>('/resource-dashboard/node-utilization-heatmap', {
    params: { ...(pool ? { pool } : {}), ...params },
  })
  return response.data
}

export const listResourceClusters = async () => {
  const response = await api.get<KubernetesCluster[]>('/resource-dashboard/clusters')
  return response.data
}

export const createResourceCluster = async (data: KubernetesClusterCreate) => {
  const response = await api.post<KubernetesCluster>('/resource-dashboard/clusters', data)
  return response.data
}

export const updateResourceCluster = async (id: number, data: KubernetesClusterUpdate) => {
  const response = await api.put<KubernetesCluster>(`/resource-dashboard/clusters/${id}`, data)
  return response.data
}

export const deleteResourceCluster = async (id: number) => {
  const response = await api.delete<{ message: string }>(`/resource-dashboard/clusters/${id}`)
  return response.data
}

export const testResourceCluster = async (id: number) => {
  const response = await api.post<KubernetesClusterTestResponse>(`/resource-dashboard/clusters/${id}/test`)
  return response.data
}
