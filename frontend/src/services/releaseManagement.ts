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
  max_wall: number
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
  date_full: string
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
  baseline_status?: 'compliant' | 'violation' | 'no_baseline'
}

export interface ThirdPartyBaseline {
  id: number
  component_name: string
  component_category: string
  approved_versions: string[]
  min_version: string | null
  max_version: string | null
  notes: string | null
  is_active: boolean
  updated_at: string | null
}

export interface ThirdPartyVersionHistory {
  version: string
  run_id: number
  branch: string | null
  used_at: string
}

export interface ComplianceCheckResult {
  run_id: number
  compliant: Array<{ component: string; version: string; status: string }>
  violations: Array<{ component: string; version: string; expected: string[] | null; min_version: string | null; max_version: string | null }>
  compliance_rate: number
}

export interface ThirdPartyRunItem {
  id: number
  workflow_id: number
  workflow_name: string
  github_run_id: number
  status: string
  branch: string | null
  commit_sha: string
  created_at: string | null
  component_count: number
}

export interface ThirdPartySnapshotComponent {
  component_name: string
  component_version: string
  component_category: string | null
  source_line: string | null
  repository_url: string | null
  baseline_status: 'compliant' | 'violation' | 'no_baseline'
  approved_versions: string[]
}

export interface ThirdPartySnapshot {
  run_id: number
  github_run_id: number
  workflow_id: number
  status: string
  branch: string | null
  commit_sha: string | null
  created_at: string | null
  components: ThirdPartySnapshotComponent[]
  components_by_category: Record<string, ThirdPartySnapshotComponent[]>
  category_order: string[]
  category_labels: Record<string, string>
  total_components: number
}

export interface ThirdPartyDiffItem {
  component_name: string
  previous_version: string | null
  current_version: string | null
  change_type: 'unchanged' | 'added' | 'removed' | 'changed'
}

export interface ThirdPartyRunDiff {
  current_run: {
    run_id: number
    github_run_id: number
    branch: string | null
    commit_sha: string | null
    created_at: string | null
  }
  previous_run: {
    run_id: number
    github_run_id: number
    branch: string | null
    commit_sha: string | null
    created_at: string | null
  } | null
  components: ThirdPartySnapshotComponent[]
  diffs: ThirdPartyDiffItem[]
  changed_count: number
  unchanged_count: number
}

export interface AIEvaluationSummary {
  id: number
  workflow_id: number
  evaluation_type: string
  period_start: string | null
  period_end: string | null
  run_count: number
  trust_score: number | null
  trust_level: string | null
  production_ready: boolean
  llm_model: string | null
  generated_by: string | null
  generated_at: string | null
}

export interface AIEvaluation extends AIEvaluationSummary {
  executive_summary: string | null
  findings: AIFinding[]
  recommendations: AIRecommendation[]
  risk_factors: AIRiskFactor[]
  data_quality_issues: string[]
  report_content: string | null
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
  workflow_id: number
  workflow_name: string
  task_type: string
  trigger_source: string
  status: string
  progress: number
  progress_message: string | null
  total_items: number
  processed_items: number
  collected_items: number
  days_back: number
  max_runs: number
  duration_seconds: number
  error_message: string | null
  errors_json: any
  started_at: string | null
  completed_at: string | null
  created_at: string | null
}

export interface SyncSummary {
  total_syncs: number
  completed_count: number
  failed_count: number
  running_count: number
  total_collected: number
  last_sync_at: string | null
  last_sync_status: string | null
}

export interface SyncTasksResult {
  tasks: SyncTask[]
  total: number
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

export interface WorkflowComparison {
  workflow_id: number
  workflow_name: string
  stats: OverviewStats
}

export interface ComparisonResult {
  comparisons: WorkflowComparison[]
  days_back: number
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

export const triggerSync = async (workflowId: number, daysBack = 30, maxRuns = 100,
  syncMode = 'incremental', sinceDate?: string, untilDate?: string) => {
  const params: Record<string, unknown> = { workflow_id: workflowId, days_back: daysBack, max_runs: maxRuns, sync_mode: syncMode }
  if (sinceDate) params.since_date = sinceDate
  if (untilDate) params.until_date = untilDate
  const response = await api.post('/release-management/sync', null, { params })
  return response.data
}

export const getSyncTask = async (taskId: number) => {
  const response = await api.get<SyncTask>(`/release-management/sync/task-by-id/${taskId}`)
  return response.data
}

export const getSyncTasks = async (workflowId: number | null = null, status: string | null = null, limit = 20, offset = 0) => {
  const params: Record<string, unknown> = { limit, offset }
  if (workflowId) params.workflow_id = workflowId
  if (status) params.status = status
  const response = await api.get<SyncTasksResult>('/release-management/sync/tasks', { params })
  return response.data
}

export const getSyncSummary = async () => {
  const response = await api.get<SyncSummary>('/release-management/sync/summary')
  return response.data
}

export const retrySync = async (taskId: number) => {
  const response = await api.post(`/release-management/sync/retry/${taskId}`)
  return response.data
}

export const cancelSync = async (taskId: number) => {
  const response = await api.post(`/release-management/sync/cancel/${taskId}`)
  return response.data
}

export const archiveSyncTask = async (taskId: number) => {
  const response = await api.get(`/release-management/sync/archive/${taskId}`)
  return response.data
}

export const exportSyncExcel = async (taskId: number): Promise<Blob> => {
  const response = await api.get(`/release-management/sync/excel/${taskId}`, {
    responseType: 'blob' as const,
  })
  return response.data as Blob
}

export const exportSyncPdf = async (taskId: number): Promise<Blob> => {
  const response = await api.post(`/release-management/sync/pdf/${taskId}`, undefined, {
    responseType: 'blob' as const,
  })
  return response.data as Blob
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
  const response = await api.get<ThirdPartyVersionHistory[]>('/release-management/third-party/version-history', {
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
  const response = await api.get<ComplianceCheckResult>('/release-management/third-party/compliance', {
    params: { run_id: runId },
  })
  return response.data
}

export const getThirdPartyRuns = async (workflowId?: number, limit = 50) => {
  const params: Record<string, unknown> = { limit }
  if (workflowId) params.workflow_id = workflowId
  const response = await api.get<ThirdPartyRunItem[]>('/release-management/third-party/runs', { params })
  return response.data
}

export const getThirdPartyRunSnapshot = async (runId: number) => {
  const response = await api.get<ThirdPartySnapshot>(`/release-management/third-party/run-snapshot/${runId}`)
  return response.data
}

export const getThirdPartyRunDiff = async (runId: number) => {
  const response = await api.get<ThirdPartyRunDiff>(`/release-management/third-party/run-diff/${runId}`)
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
  const response = await api.get<{ evaluations: AIEvaluationSummary[]; total: number; page: number; page_size: number }>(
    '/release-management/evaluations',
    { params },
  )
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

export const exportPdf = async (workflowIds: number[] | null, daysBack = 30) => {
  const params: Record<string, unknown> = { days_back: daysBack }
  if (workflowIds) params.workflow_ids = workflowIds.join(',')
  const response = await api.post('/release-management/export/pdf', null, {
    params,
    responseType: 'blob',
  })
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

export function trustScoreColor(score: number | null): string {
  if (score === null) return '#8c8c8c'
  if (score >= 80) return '#52c41a'
  if (score >= 60) return '#faad14'
  return '#ff4d4f'
}

export const getComparison = async (workflowIds: number[], daysBack = 30) => {
  const response = await api.get<ComparisonResult>('/release-management/comparison', {
    params: { workflow_ids: workflowIds.join(','), days_back: daysBack },
  })
  return response.data
}

export function trustLevelLabel(level: string | null): string {
  switch (level) {
    case 'high': return '高'
    case 'medium': return '中'
    case 'low': return '低'
    default: return level || 'N/A'
  }
}
