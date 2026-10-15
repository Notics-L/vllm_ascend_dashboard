import api from './api'

export type OccupancyView = 'pool' | 'project'

export interface OccupancyDimension {
  name: string
  label: string
  capacity: number | null
  configured: boolean
}

export interface OccupancyTrendPoint {
  timestamp: string
  occupied_cards: number
}

export interface OccupancyTrendResponse {
  dimension: OccupancyView
  names: string[]
  start: string
  end: string
  sample_interval_seconds: number
  metrics: { peak: number; average: number; latest: number; capacity: number | null }
  series: OccupancyTrendPoint[]
  snapshot_at: string
  data_status: 'ready'
}

export interface OccupancyTask {
  env_id: string
  organization: string | null
  repository: string | null
  project: string
  pool: string
  workflow: string
  job_id: string | null
  job_name: string
  workflow_run_id: number | null
  workflow_name: string | null
  workflow_file: string | null
  run_display_title: string | null
  run_number: number | null
  run_display_name: string
  workflow_ref: string | null
  pr_number: number | null
  event: string | null
  trigger_scope: string | null
  run_url: string | null
  github_url: string | null
  workflow_config_match: boolean
  cards: number
  node_ip: string | null
  npu_list: string | null
  status: string
  created_at: string
  effective_end: string
}

export interface OccupancyRun {
  key: string
  workflow_run_id: number | null
  workflow_name: string | null
  workflow_file: string | null
  run_display_title: string | null
  run_number: number | null
  run_display_name: string
  workflow_ref: string | null
  pr_number: number | null
  event: string | null
  trigger_scope: string | null
  run_url: string | null
  github_url: string | null
  workflow_config_match: boolean
  cards: number
  task_count: number
  created_at: string
  effective_end: string
  tasks: OccupancyTask[]
}

export interface OccupancyDetailGroup {
  name: string
  occupied_cards: number
  task_count: number
  runs: OccupancyRun[]
}

export interface OccupancyDetailsResponse {
  timestamp: string
  dimension: OccupancyView
  names: string[]
  group_by: OccupancyView
  groups: OccupancyDetailGroup[]
  snapshot_at: string
}

export interface OccupancyAnalysisGroup {
  name: string
  peak_cards: number
  average_cards: number
  task_count: number
  runs: OccupancyRun[]
}

export interface OccupancyAnalysisResponse {
  dimension: OccupancyView
  names: string[]
  start: string
  end: string
  sample_interval_seconds: number
  groups: OccupancyAnalysisGroup[]
  snapshot_at: string
}

const namesParam = (names: string[]) => names.length ? names.join(',') : undefined

export async function getOccupancyOptions(dimension: OccupancyView) {
  const response = await api.get<{ dimension: OccupancyView; options: OccupancyDimension[] }>('/npu-occupancy/options', { params: { dimension } })
  return response.data
}

export async function getOccupancyTrend(params: { dimension: OccupancyView; names: string[]; start: string; end: string }) {
  const response = await api.get<OccupancyTrendResponse>('/npu-occupancy/trend', { params: { ...params, names: namesParam(params.names) } })
  return response.data
}

export async function refreshOccupancySnapshot(params: { start: string; end: string }) {
  const response = await api.post('/npu-occupancy/refresh', undefined, { params })
  return response.data
}

export async function getOccupancyDetails(params: { dimension: OccupancyView; group_by: OccupancyView; names: string[]; timestamp: string; start: string; end: string }) {
  const response = await api.get<OccupancyDetailsResponse>('/npu-occupancy/details', { params: { ...params, names: namesParam(params.names) } })
  return response.data
}

export async function getOccupancyAnalysis(params: { dimension: OccupancyView; names: string[]; start: string; end: string }) {
  const response = await api.get<OccupancyAnalysisResponse>('/npu-occupancy/analysis', { params: { ...params, names: namesParam(params.names) } })
  return response.data
}
