import api from './api'

// ============ Types ============

export interface Workflow {
  id: number
  repo_owner: string
  repo_name: string
  workflow_id: number
  workflow_name: string
  workflow_path: string
  workflow_type: string
  enabled: boolean
  last_sync_at: string | null
  created_at: string | null
}

export interface BuildRun {
  id: number
  workflow_id: number
  github_run_id: number
  status: string
  event: string
  branch: string
  commit_sha: string
  created_at: string
  started_at: string
  completed_at: string
  wait_duration: number
  exec_duration: number
  wall_duration: number
  num_jobs: number
  conclusion_summary: Record<string, number>
}

export interface BuildGroup {
  id: number
  run_id: number
  group_name: string
  target: string
  os_name: string
  phase: string
  status: string
  total_duration: number
  runner_name: string
  architecture: string
  sub_job_name: string
  steps: StepData[]
}

export interface StepData {
  name: string
  number: number
  status: string
  duration: number
}

export interface OverviewStats {
  total: number
  successful: number
  failed: number
  cancelled: number
  success_rate: number
  avg_exec: number
  min_exec: number
  max_exec: number
  cv_exec: number
  avg_wait: number
  max_wait: number
  avg_wall: number
  abnormal_count: number
  long_wait_count: number
}

export interface OverviewData {
  stats: OverviewStats
  timeline: TimelinePoint[]
  phase_data: PhaseData[]
  target_data: TargetData[]
  arch_data: ArchData[]
  step_data: StepSummary[]
  run_breakdown: RunBreakdown[]
}

export interface TimelinePoint {
  date: string
  created: string
  run_id: number
  status: string
  wait: number
  exec: number
  wall: number
  jobs: number
}

export interface PhaseData {
  name: string
  count: number
  avg: number
  total: number
  max: number
}

export interface TargetData {
  name: string
  count: number
  avg: number
  min: number
  max: number
  total: number
}

export interface ArchData {
  name: string
  count: number
  avg: number
  total: number
}

export interface StepSummary {
  name: string
  count: number
  avg: number
  total: number
  max: number
}

export interface RunBreakdown {
  run_id: number
  date: string
  status: string
  wait: number
  exec: number
  wall: number
  jobs: number
}

export interface ConfigCheckResult {
  github_configured: boolean
  github_owner: string
  github_repo: string
  llm_configured: boolean
  llm_providers: LLMProvider[]
}

export interface LLMProvider {
  id: number
  provider: string
  display_name: string
  is_active: boolean
  default_model: string
}

export interface ThirdPartyComponent {
  component_name: string
  component_category: string
  versions: { version: string; usage_count: number }[]
  latest_version: string
}

export interface ThirdPartyBaseline {
  id: number
  component_name: string
  component_category: string
  approved_versions: string[]
  min_version: string
  max_version: string
  notes: string
  is_active: boolean
  updated_at: string | null
}

export interface ComplianceResult {
  run_id: number
  compliant: CompliantItem[]
  violations: ViolationItem[]
  compliance_rate: number
}

export interface CompliantItem {
  component: string
  version: string
  status: string
}

export interface ViolationItem {
  component: string
  version: string
  expected: string[] | null
  min_version: string
  max_version: string
}

export interface AIEvaluation {
  id: number
  workflow_id: number
  evaluation_type: string
  period_start: string | null
  period_end: string | null
  run_count: number
  trust_score: number
  trust_level: string
  production_ready: boolean
  llm_model: string
  executive_summary: string
  findings: AIFinding[]
  recommendations: AIRecommendation[]
  risk_factors: AIRiskFactor[]
  data_quality_issues: string[]
  generated_by: string
  generated_at: string
}

export interface AIEvaluationListItem {
  id: number
  workflow_id: number
  evaluation_type: string
  period_start: string | null
  period_end: string | null
  run_count: number
  trust_score: number
  trust_level: string
  production_ready: boolean
  llm_model: string
  generated_by: string
  generated_at: string
}

export interface AIFinding {
  id: string
  title: string
  description: string
  severity: 'high' | 'medium' | 'low'
}

export interface AIRecommendation {
  title: string
  description: string
  priority: string
}

export interface AIRiskFactor {
  name: string
  description: string
  impact: string
}

export interface SyncTask {
  id: number
  task_type: string
  status: string
  progress: number
  total_items: number
  processed_items: number
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface DurationTrendPoint {
  period: string
  count: number
  success_count: number
  success_rate: number
  avg_duration: number
  min_duration: number
  max_duration: number
}

// ============ API Calls ============

export const getConfigCheck = async () => {
  const response = await api.get<ConfigCheckResult>('/release-management/config-check')
  return response.data
}

export const listWorkflows = async (enabledOnly = true) => {
  const response = await api.get<Workflow[]>('/release-management/workflows', {
    params: { enabled_only: enabledOnly },
  })
  return response.data
}

export const discoverWorkflows = async (owner: string, repo: string) => {
  const response = await api.post('/release-management/workflows/discover', null, {
    params: { owner, repo },
  })
  return response.data
}

export const toggleWorkflow = async (workflowId: number, enabled: boolean) => {
  const response = await api.put('/release-management/workflows/${workflowId}/toggle', null, {
    params: { enabled },
  })
  return response.data
}

export const triggerSync = async (workflowId: number, daysBack = 30, maxRuns = 100) => {
  const response = await api.post('/release-management/sync', null, {
    params: { workflow_id: workflowId, days_back: daysBack, max_runs: maxRuns },
  })
  return response.data
}

export const getSyncStatus = async (workflowId: number) => {
  const response = await api.get<SyncTask[]>('/release-management/sync/status', {
    params: { workflow_id: workflowId },
  })
  return response.data
}

export const getOverview = async (workflowIds: number[] | null, daysBack = 30) => {
  const response = await api.get<OverviewData>('/release-management/overview', {
    params: {
      workflow_ids: workflowIds ? workflowIds.join(',') : undefined,
      days_back: daysBack,
    },
  })
  return response.data
}

export const getRuns = async (
  workflowIds: number[] | null,
  status: string | null = null,
  branch: string | null = null,
  page = 1,
  pageSize = 50,
) => {
  const params: Record<string, unknown> = { page, page_size: pageSize }
  if (workflowIds) params.workflow_ids = workflowIds.join(',')
  if (status) params.status = status
  if (branch) params.branch = branch
  const response = await api.get('/release-management/runs', { params })
  return response.data
}

export const getRunDetail = async (runId: number) => {
  const response = await api.get<{ run: BuildRun; groups: BuildGroup[] }>(
    `/release-management/runs/${runId}`,
  )
  return response.data
}

export const getDurationTrend = async (
  workflowIds: number[] | null,
  daysBack = 30,
  aggregate: 'day' | 'week' = 'day',
) => {
  const params: Record<string, unknown> = { days_back: daysBack, aggregate }
  if (workflowIds) params.workflow_ids = workflowIds.join(',')
  const response = await api.get<DurationTrendPoint[]>('/release-management/trends/duration', {
    params,
  })
  return response.data
}

export const getThirdPartyOverview = async (workflowIds: number[] | null) => {
  const params: Record<string, unknown> = {}
  if (workflowIds) params.workflow_ids = workflowIds.join(',')
  const response = await api.get<ThirdPartyComponent[]>('/release-management/third-party', {
    params,
  })
  return response.data
}

export const getVersionHistory = async (componentName: string, limit = 50) => {
  const response = await api.get('/release-management/third-party/version-history', {
    params: { component_name: componentName, limit },
  })
  return response.data
}

export const getBaselines = async () => {
  const response = await api.get<ThirdPartyBaseline[]>('/release-management/third-party/baselines')
  return response.data
}

export const createOrUpdateBaseline = async (data: Partial<ThirdPartyBaseline> & { component_name: string }) => {
  const response = await api.post('/release-management/third-party/baselines', data)
  return response.data
}

export const deleteBaseline = async (baselineId: number) => {
  const response = await api.delete(`/release-management/third-party/baselines/${baselineId}`)
  return response.data
}

export const getComplianceCheck = async (runId: number) => {
  const response = await api.get<ComplianceResult>('/release-management/third-party/compliance', {
    params: { run_id: runId },
  })
  return response.data
}

export const triggerEvaluation = async (
  workflowId: number,
  evaluationType = 'trustworthiness',
  periodStart: string | null = null,
  periodEnd: string | null = null,
  modelName: string | null = null,
) => {
  const response = await api.post('/release-management/evaluate', null, {
    params: {
      workflow_id: workflowId,
      evaluation_type: evaluationType,
      period_start: periodStart,
      period_end: periodEnd,
      model_name: modelName,
    },
  })
  return response.data
}

export const listEvaluations = async (
  workflowId: number | null = null,
  page = 1,
  pageSize = 20,
) => {
  const params: Record<string, unknown> = { page, page_size: pageSize }
  if (workflowId) params.workflow_id = workflowId
  const response = await api.get('/release-management/evaluations', { params })
  return response.data
}

export const getLatestEvaluation = async (workflowId: number | null = null) => {
  const params: Record<string, unknown> = {}
  if (workflowId) params.workflow_id = workflowId
  const response = await api.get<AIEvaluation>('/release-management/evaluations/latest', {
    params,
  })
  return response.data
}

export const getEvaluationDetail = async (evaluationId: number) => {
  const response = await api.get<AIEvaluation>(
    `/release-management/evaluations/${evaluationId}`,
  )
  return response.data
}

export const exportExcel = async (workflowIds: number[] | null, daysBack = 30) => {
  const params: Record<string, unknown> = { days_back: daysBack }
  if (workflowIds) params.workflow_ids = workflowIds.join(',')
  const response = await api.post('/release-management/export/excel', null, {
    params,
    responseType: 'blob',
  })
  return response.data
}

// ============ Utility ============

export function fmtDuration(sec: number): string {
  if (!sec || sec < 0) return '0s'
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  if (m < 60) return s > 0 ? `${m}m${s}s` : `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h${m % 60}m`
}

export function getStatusColor(status: string): string {
  const s = status.toLowerCase()
  if (s === 'success') return '#52c41a'
  if (s === 'failure') return '#ff4d4f'
  if (s === 'cancelled') return '#faad14'
  if (s === 'skipped') return '#d9d9d9'
  return '#1890ff'
}

export function getTrustLevelColor(level: string): string {
  const l = level.toLowerCase()
  if (l === 'high') return '#52c41a'
  if (l === 'medium') return '#faad14'
  return '#ff4d4f'
}
