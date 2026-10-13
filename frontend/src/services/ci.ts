import api from './api'
import { longTimeoutApiClient } from './api'

// ============ Types ============

export interface CIResult {
  id: number
  workflow_name: string
  run_id: number
  run_number?: number
  job_name: string | null
  status: string
  conclusion: string | null
  event?: string | null
  branch?: string | null
  head_sha?: string | null
  hardware: string | null
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  created_at: string
  github_html_url?: string
}

export interface CIJob {
  id: number
  job_id: number
  run_id: number
  workflow_name: string
  job_name: string
  status: string
  conclusion: string | null
  hardware: string | null
  runner_name: string | null
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  runner_labels?: string[]
  steps_summary?: StepSummary[]
  steps_data?: StepSummary[]
  vllm_ascend_commit?: string | null
  vllm_ascend_commit_date?: string | null
  vllm_ascend_commit_message?: string | null
  version_evidence_status?: string | null
  created_at: string
  github_job_url?: string
}

export interface StepSummary {
  name: string
  status: string
  conclusion: string | null
  number: number
}

export interface ComparisonField {
  field: string
  start: unknown
  end: unknown
  changed: boolean | null
  availability?: 'available' | 'unknown'
  source: string
}

export interface FailureStageEvidence {
  last_successful_step: string | null
  first_failed_step: string | null
  direct_error: string | null
  source: string
}

export interface JobFailureSummary {
  summary: string | null
  failed_step: string | null
  source: 'github_log' | 'steps' | 'success'
  reason?: string | null
}

export interface JobLogRootCause {
  job_id: number
  run_id: number
  status: 'analyzing' | 'completed' | 'failed'
  summary: string | null
  log_excerpt: string | null
  llm_provider: string | null
  llm_model: string | null
  generation_time_seconds: number | null
  error_message: string | null
  created_at: string | null
  updated_at: string | null
}

export interface CIJobComparison {
  start: { started_at: string | null; conclusion: string | null; duration_seconds: number | null; vllm_ascend_commit?: string | null; vllm_ascend_commit_date?: string | null; vllm_ascend_commit_message?: string | null; version_evidence_status?: string | null }
  end: { started_at: string | null; conclusion: string | null; duration_seconds: number | null; vllm_ascend_commit?: string | null; vllm_ascend_commit_date?: string | null; vllm_ascend_commit_message?: string | null; version_evidence_status?: string | null }
  pr_changes: {
    source: string
    precision: 'candidate_only' | 'commit_range' | 'job_file_intersection'
    compare_url: string | null
    error?: string | null
    items: Array<{
      number: number | null
      sha?: string
      title: string
      author: string | null
      url: string | null
      commit_url?: string | null
      matched_files?: string[]
      merged_at?: string | null
      net_status?: 'effective' | 'fully_reverted' | 'revert_commit'
    }>
    effective_prs?: CIJobComparison['pr_changes']['items']
    reverted_prs?: CIJobComparison['pr_changes']['items']
    revert_prs?: CIJobComparison['pr_changes']['items']
    revert_commits?: Array<{ sha: string; target_sha: string | null; revert_pr?: number | null; reverted_pr: number; title: string; url?: string; original_in_window?: boolean; revert_in_window?: boolean; cancellation_status?: 'cancelled' | 'not_cancelled' }>
    partial?: boolean
  }
  version_differences: ComparisonField[]
  failure_stage_difference: { start: FailureStageEvidence; end: FailureStageEvidence }
  test_difference: { status: 'unknown' | 'available'; summary: string; source: string }
  configuration_differences: ComparisonField[]
  warnings: string[]
}

export interface CIJobPerformanceMetric {
  job_id: number
  run_id: number
  started_at: string | null
  conclusion: string | null
  github_job_url: string
  case_name: string
  metric_type: 'ott' | 'accuracy'
  value: number | null
  baseline: number | null
  threshold_ratio?: number | null
  threshold_tolerance?: number | null
  reason?: string | null
  unit: string
  source_step: 'run_pytest' | 'stream_logs'
  source_excerpt: string
}

export interface CIJobPerformanceTrends {
  items: CIJobPerformanceMetric[]
  unavailable: Array<{ job_id: number; reason: string }>
}

export interface CIStats {
  total_runs: number
  passed_runs: number
  failed_runs: number
  other_runs: number
  success_rate: number
  avg_duration_seconds: number | null
  last_7_days: {
    runs: number
    success_rate: number
    avg_duration_seconds: number | null
  } | null
}

export interface CITrend {
  date: string
  total_runs: number
  success_runs: number
  success_rate: number
  avg_duration_seconds: number | null
  max_duration_seconds: number | null
}

export interface CISyncResponse {
  success: boolean
  message: string
  collected_count?: number
}

export interface SyncStatus {
  scheduler_running: boolean
  jobs: Array<{
    id: string
    name: string
    next_run_time: string | null
  }>
  error?: string
}

export interface SyncProgress {
  status: string
  task_id?: number | null
  checkpoint?: Record<string, unknown> | null
  progress_percentage: number
  total_workflows: number
  completed_workflows: number
  current_workflow: string | null
  total_collected: number
  workflow_details: Record<string, {
    collected: number
    status: string
    updated_at: string
  }>
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  elapsed_seconds: number | null
}

// ============ API Functions ============

/**
 * 获取 workflow 列表
 */
export const getWorkflows = async (): Promise<string[]> => {
  const response = await api.get<string[]>('/ci/workflows')
  return response.data
}

/**
 * 获取 CI 运行列表
 */
export const getRuns = async (params?: {
  workflow_name?: string
  status?: string
  hardware?: string
  start_time?: string
  end_time?: string
  limit?: number
}): Promise<CIResult[]> => {
  const response = await api.get<CIResult[]>('/ci/runs', { params })
  return response.data
}

/**
 * 获取 CI 统计数据
 */
export const getStats = async (params?: {
  workflow_name?: string
  hardware?: string
  start_time?: string
  end_time?: string
}): Promise<CIStats> => {
  const response = await api.get<CIStats>('/ci/stats', { params })
  return response.data
}

/**
 * 获取 CI 趋势数据
 */
export const getTrends = async (params: {
  days?: number
  workflow_name?: string
  hardware?: string
}): Promise<CITrend[]> => {
  const response = await api.get<CITrend[]>('/ci/trends', { params })
  return response.data
}

/**
 * 获取指定 workflow run 的所有 jobs
 */
export const getJobsByRun = async (runId: number): Promise<CIJob[]> => {
  const response = await api.get<CIJob[]>(`/ci/runs/${runId}/jobs`)
  return response.data
}

export const getJobs = async (params?: {
  days?: number
  workflow_name?: string
  search?: string
  status?: string
  conclusion?: string
  hardware?: string
  limit?: number
}): Promise<CIJob[]> => {
  const response = await api.get<CIJob[]>('/ci/jobs', { params })
  return response.data
}

export const getJobComparison = async (startJobId: number, endJobId: number): Promise<CIJobComparison> => {
  const response = await api.get<CIJobComparison>('/ci/job-comparison', {
    params: { start_job_id: startJobId, end_job_id: endJobId },
  })
  return response.data
}

export const getJobPerformanceTrends = async (jobIds: number[]): Promise<CIJobPerformanceTrends> => {
  const response = await api.get<CIJobPerformanceTrends>('/ci/job-performance-trends', {
    params: { job_ids: jobIds },
    paramsSerializer: { indexes: null },
  })
  return response.data
}

export const refreshRunVersionSnapshot = async (runId: number) => {
  const response = await api.post(`/ci/runs/${runId}/version-snapshot/refresh`)
  return response.data
}

export const getJobFailureSummary = async (jobId: number): Promise<JobFailureSummary> => {
  const response = await api.get<JobFailureSummary>(`/ci/jobs/${jobId}/failure-summary`)
  return response.data
}

export const getJobLogRootCause = async (jobId: number): Promise<JobLogRootCause | null> => {
  const response = await api.get<JobLogRootCause | null>(`/ci/jobs/${jobId}/log-root-cause`)
  return response.data
}

export const createJobLogRootCause = async (jobId: number, regenerate = false): Promise<JobLogRootCause> => {
  const response = await longTimeoutApiClient.post<JobLogRootCause>(
    `/ci/jobs/${jobId}/log-root-cause`,
    undefined,
    { params: { regenerate } },
  )
  return response.data
}

/**
 * 获取 job 详情
 */
export const getJobDetail = async (jobId: number): Promise<CIJob> => {
  const response = await api.get<CIJob>(`/ci/jobs/${jobId}`)
  return response.data
}

/**
 * 手动触发数据同步
 */
export const triggerSync = async (): Promise<CISyncResponse> => {
  const response = await api.post<CISyncResponse>('/ci/sync')
  return response.data
}

/**
 * 获取同步任务状态
 */
export const getSyncStatus = async (): Promise<SyncStatus> => {
  const response = await api.get<SyncStatus>('/ci/sync/status')
  return response.data
}

/**
 * 获取同步进度详情
 */
export const getSyncProgress = async (): Promise<SyncProgress> => {
  const response = await api.get<SyncProgress>('/ci/sync/progress')
  return response.data
}

// ============ Daily Failure Tracking ============

export interface DailyFailureJob {
  id: number
  job_id: number
  run_id: number
  workflow_name: string
  job_name: string
  conclusion: string | null
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  hardware: string | null
  owner: string | null
  owner_email: string | null
  display_name: string | null
  test_model: string | null
  model_fo: string | null
  deployment_type: string | null
  processing_time: string | null
  closure_time: string | null
  processing_status: string
  problem_category: string | null
  related_pr: string | null
  notes: string | null
  updated_by: string | null
  status_updated_at: string | null
  github_job_url: string | null
}

export interface DailyFailureStats {
  date: string
  total_failed_jobs: number
  cancelled: number
  unprocessed: number
  processing: number
  fixed: number
  closed: number
}

export interface DailyFailureListResponse {
  date: string
  stats: DailyFailureStats
  jobs: DailyFailureJob[]
}

export interface DailyFailureUpdateRequest {
  processing_status: string
  owner?: string | null
  problem_category?: string | null
  related_pr?: string | null
  notes?: string | null
  processing_time?: string | null
  closure_time?: string | null
}

export type DailyFailureBatchUpdateRequest = Partial<DailyFailureUpdateRequest>

export interface DailyFailureQueryParams {
  start_date?: string
  end_date?: string
  workflow_name?: string
  processing_status?: string
  notes_search?: string
}

/**
 * 获取每日失败 Job 列表（按天分组）
 * 不传日期范围则返回全部数据
 */
export const getDailyFailures = async (
  params?: DailyFailureQueryParams
): Promise<DailyFailureListResponse[]> => {
  const response = await api.get<DailyFailureListResponse[]>('/ci/daily-failures', { params })
  return response.data
}

/**
 * 按当前筛选条件导出全部每日失败追踪记录。
 */
export const exportDailyFailures = async (
  params?: DailyFailureQueryParams
): Promise<Blob> => {
  const response = await api.get<Blob>('/ci/daily-failures/export', {
    params,
    responseType: 'blob',
  })
  return response.data
}

/**
 * 更新失败 Job 的责任人、处理状态和处理信息
 */
export const updateFailureStatus = async (
  jobDbId: number,
  data: DailyFailureUpdateRequest
): Promise<DailyFailureJob> => {
  const response = await api.put<DailyFailureJob>(`/ci/daily-failures/${jobDbId}/status`, data)
  return response.data
}

export const batchUpdateFailureStatus = async (
  ids: number[],
  data: DailyFailureBatchUpdateRequest
): Promise<{ message: string; count: number }> => {
  const response = await api.put<{ message: string; count: number }>(
    `/ci/daily-failures/batch-status?${ids.map(id => `ids=${id}`).join('&')}`,
    data
  )
  return response.data
}

export interface BatchAnalyzeDailyFailuresResponse {
  success: boolean
  selected: number
  queued: number
  analyzing: number
  completed: number
  skipped: number
  errors: string[]
}

export const batchAnalyzeDailyFailures = async (
  ids: number[]
): Promise<BatchAnalyzeDailyFailuresResponse> => {
  const response = await api.post<BatchAnalyzeDailyFailuresResponse>(
    `/ci/daily-failures/batch-analyze?${ids.map(id => `ids=${id}`).join('&')}`
  )
  return response.data
}

// ============ Nightly Test Case Management ============

export interface NightlyTestCase {
  id: number
  report_date: string | null
  source_branch: string
  workflow_name: string
  job_name: string
  display_name: string | null
  test_model: string | null
  model_fo: string | null
  owner: string | null
  deployment_type: string | null
  notes: string | null
  enabled: boolean
  created_at: string
  updated_at: string | null
}

export interface NightlyTestCaseCreate {
  report_date?: string | null
  source_branch?: string | null
  workflow_name: string
  job_name: string
  display_name?: string | null
  test_model?: string | null
  model_fo?: string | null
  owner?: string | null
  deployment_type?: string | null
  notes?: string | null
  enabled?: boolean
}

export interface NightlyTestCaseUpdate {
  report_date?: string | null
  source_branch?: string | null
  workflow_name?: string | null
  job_name?: string | null
  display_name?: string | null
  test_model?: string | null
  model_fo?: string | null
  owner?: string | null
  deployment_type?: string | null
  notes?: string | null
  enabled?: boolean | null
}

export const getNightlyTestCases = async (params?: {
  report_date?: string
  start_date?: string
  end_date?: string
  source_branch?: string
  workflow_name?: string
  enabled?: boolean
}): Promise<NightlyTestCase[]> => {
  const response = await api.get<NightlyTestCase[]>('/ci/nightly-test-cases', { params })
  return response.data
}

export const exportNightlyTestCases = async (params?: {
  report_date?: string
  start_date?: string
  end_date?: string
  source_branch?: string
  workflow_name?: string
  enabled?: boolean
}): Promise<Blob> => {
  const response = await api.get<Blob>('/ci/nightly-test-cases/export', {
    params,
    responseType: 'blob',
  })
  return response.data
}

export const createNightlyTestCase = async (data: NightlyTestCaseCreate): Promise<NightlyTestCase> => {
  const response = await api.post<NightlyTestCase>('/ci/nightly-test-cases', data)
  return response.data
}

export const updateNightlyTestCase = async (id: number, data: NightlyTestCaseUpdate): Promise<NightlyTestCase> => {
  const response = await api.put<NightlyTestCase>(`/ci/nightly-test-cases/${id}`, data)
  return response.data
}

export const deleteNightlyTestCase = async (id: number): Promise<void> => {
  await api.delete(`/ci/nightly-test-cases/${id}`)
}

// ============ Nightly Gantt ============

export interface NightlyGanttItem {
  phase: string
  name: string
  raw_name: string
  created_bj: string
  start_bj: string
  end_bj: string
  created_ms: number
  start_ms: number
  end_ms: number
  queued_ms: number
  duration: string
  duration_seconds: number
  status: 'ok' | 'err' | 'cancelled'
  conclusion: string | null
  job_id: number | null
  job_url: string | null
}

export interface NightlyGanttKpi {
  total: number
  ok: number
  err: number
  cancelled: number
  ok_rate: number
  err_rate: number
  cancel_rate: number
  span_ms: number
  phase_counts: Record<string, number>
}

export interface NightlyGanttRunMeta {
  status: string | null
  conclusion: string | null
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  html_url: string | null
}

export interface NightlyGanttResponse {
  run_number: number
  run_id: number
  hardware: string
  workflow_file: string
  workflow_display: string
  run_meta: NightlyGanttRunMeta
  kpi: NightlyGanttKpi
  rows: NightlyGanttItem[]
  phases: Record<string, NightlyGanttItem[]>
}

export const getNightlyGantt = async (
  runNumber: number,
  hardware: string = 'a3',
): Promise<NightlyGanttResponse> => {
  const response = await api.get<NightlyGanttResponse>(
    `/ci/nightly-gantt/${runNumber}`,
    { params: { hardware } },
  )
  return response.data
}
