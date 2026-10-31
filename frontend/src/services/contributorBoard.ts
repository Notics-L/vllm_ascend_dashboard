/**
 * Contributor Board API Service
 */
import api from './api'

// ==================== 分类统计 ====================

export interface ClassificationSummary {
  total_commits: number
  type_distribution: Record<string, number>
  type_percentage: Record<string, number>
  chip_specific_count: number
  module_hotspot: Record<string, number>
}

/**
 * 获取提交分类统计摘要
 */
export const getClassificationSummary = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<ClassificationSummary>('/contributor-board/commits/classifications', { params: queryParams })
  return response.data
}

// ==================== 构建优化统计 ====================

export interface BuildOptimizationStats {
  total_evaluations: number
  skipped_builds: number
  reduced_builds: number
  full_builds: number
  avg_saving_percent: number
  by_type: Record<string, number>
}

/**
 * 获取构建优化统计
 */
export const getBuildOptimizationStats = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<BuildOptimizationStats>('/contributor-board/builds/stats', { params: queryParams })
  return response.data
}

// ==================== 构建计划 ====================

export interface BuildPlan {
  id: number | null
  pr_number: number
  commit_sha: string
  commit_type: string
  build_type: string
  chips: string[] | null
  os_list: string[] | null
  cache_strategy: string | null
  estimated_saving_percent: number
  actual_execution: boolean
  reasoning: string | null
  files_changed: string[] | null
  created_at: string | null
}

/**
 * 获取构建计划列表
 */
export const getBuildPlans = async (params?: {
  repo_name?: string | null
  build_type?: string | null
  commit_type?: string | null
  created_after?: string | null
  created_before?: string | null
  limit?: number
  offset?: number
}) => {
  const queryParams: Record<string, unknown> = {
    limit: params?.limit ?? 20,
    offset: params?.offset ?? 0,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.build_type) queryParams.build_type = params.build_type
  if (params?.commit_type) queryParams.commit_type = params.commit_type
  if (params?.created_after) queryParams.created_after = params.created_after
  if (params?.created_before) queryParams.created_before = params.created_before
  const response = await api.get<{ plans: BuildPlan[]; total: number }>('/contributor-board/builds/plans', { params: queryParams })
  return response.data
}

export const getBuildFilterOptions = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<{ commit_types: string[]; build_types: string[] }>('/contributor-board/builds/filter_options', { params: queryParams })
  return response.data
}

/**
 * 评估构建计划
 */
export const evaluateBuild = async (
  prNumber: number,
  commitMessage?: string,
  filesChanged?: string[]
) => {
  const body: Record<string, unknown> = { pr_number: prNumber }
  if (commitMessage) body.commit_message = commitMessage
  if (filesChanged) body.files_changed = filesChanged
  const response = await api.post<BuildPlan>('/contributor-board/builds/evaluate', body)
  return response.data
}

// ==================== 总览统计 ====================

export interface OverviewStats {
  total_contributors: number
  community_contributors: number
  total_commits_classified: number
  classification_distribution: Record<string, number>
  quadrant_distribution: Record<string, number>
  build_optimization_stats: BuildOptimizationStats
  top_contributors: unknown[]
  last_synced_at: string | null
}

/**
 * 获取总览统计
 */
export const getOverviewStats = async () => {
  const response = await api.get<OverviewStats>('/contributor-board/stats/overview')
  return response.data
}

// ==================== 贡献者看板（四象限） ====================

export interface Contributor {
  id: number
  github_id: number
  login: string
  avatar_url: string | null
  email: string | null
  name: string | null
  is_community: boolean
  role_types: string[] | null
  company: string | null
  first_contribution_at: string | null
  last_contribution_at: string | null
}

export interface QuadrantPoint {
  login: string
  avatar_url: string | null
  contribution_score: number
  error_score: number
  y_value: number
  y_axis_type: string
  quadrant: 'INCENTIVE' | 'IMPROVE' | 'ENCOURAGE' | 'REPLACE'
  role_label: string
  merged_pr_count: number
  is_community: boolean
  color: string
  company?: string | null
  primary_email?: string | null
  is_bot?: boolean
}

export interface QuadrantData {
  points: QuadrantPoint[]
  quad_x: number
  quad_y: number
  y_axis_type: string
  repo_name: string | null
  period_start: string | null
  period_end: string | null
  total_count: number
  quadrant_counts: Record<string, number>
}

export interface ContributorDetail {
  id: number
  github_id: number
  login: string
  avatar_url: string | null
  email: string | null
  name: string | null
  is_community: boolean
  role_types: string[] | null
  company: string | null
  employee_id: string | null
  first_contribution_at: string | null
  last_contribution_at: string | null
  merged_pr_count: number
  pending_pr_count: number
  code_additions: number
  code_deletions: number
  review_count: number
  approval_count: number
  contribution_score: number
  error_score: number
  quadrant: string | null
  emails?: string[]
  is_bot?: boolean
  ci_failure_count?: number
  first_review_hours_avg?: number | null
  merge_hours_avg?: number | null
  ci_duration_hours_avg?: number | null
}

export interface RankingItem {
  login: string
  avatar_url: string | null
  score: number
  rank: number
  merged_pr_count: number
  pending_pr_count: number
  code_additions: number
  code_deletions: number
  review_count: number
  approval_count: number
  contribution_score: number
  error_score: number
  commit_count: number
  dynamic_quadrant: string | null
  role_label: string | null
  employee_id: string | null
  company?: string | null
  primary_email?: string | null
  is_bot?: boolean
}

export interface ContributorRanking {
  role_type: string
  rankings: RankingItem[]
  total: number
  repo_name: string | null
}

export interface TrendPoint {
  date: string
  merged_pr_count: number
  code_additions: number
  code_deletions: number
  review_count: number
  contribution_score: number
  error_score: number
}

export interface ContributorTrend {
  login: string
  points: TrendPoint[]
  period_start: string | null
  period_end: string | null
}

/**
 * 获取四象限数据
 */
export const getQuadrantData = async (
  params?: { repo_name?: string | null; y_axis_type?: string }
) => {
  const response = await api.get<QuadrantData>('/contributor-board/quadrant', { params })
  return response.data
}

/**
 * 获取贡献者列表
 */
export const getContributors = async (
  params?: {
    role_type?: string
    search?: string
    limit?: number
    offset?: number
  }
) => {
  const response = await api.get<{ contributors: Contributor[]; total: number }>(
    '/contributor-board/contributors', { params }
  )
  return response.data
}

/**
 * 获取贡献者详情
 */
export const getContributorDetail = async (login: string) => {
  const response = await api.get<ContributorDetail>(
    `/contributor-board/contributors/${login}`
  )
  return response.data
}

/**
 * 获取排行榜
 */
export const getRanking = async (
  roleType: string = 'developer',
  limit: number = 20,
  params?: {
    repo_name?: string | null
    sort_by?: string | null
    sort_order?: string
    offset?: number
    quadrant?: string | null
    y_axis_type?: string | null
  }
) => {
  const response = await api.get<ContributorRanking>('/contributor-board/ranking', {
    params: { role_type: roleType, limit, offset: params?.offset ?? 0, ...params },
  })
  return response.data
}

/**
 * 获取贡献者趋势数据
 */
export const getContributorTrend = async (
  login: string,
  params?: { period_start?: string; period_end?: string }
) => {
  const response = await api.get<ContributorTrend>(
    `/contributor-board/contributors/${login}/trend`, { params }
  )
  return response.data
}

export interface SyncRecord {
  id: number
  repo_name: string | null
  sync_type: string
  status: string
  since_date: string | null
  until_date: string | null
  contributors_synced: number
  commits_classified: number
  total_prs_scanned: number
  build_plans_created: number
  errors: string[]
  duration_seconds: number
  synced_at: string | null
  progress_percent: number
  progress_message: string | null
  trigger_source: string
  started_at: string | null
  completed_at: string | null
  created_at: string
}

/**
 * 触发同步贡献者数据
 */
export const syncContributors = async (
  params?: {
    sinceDate?: string
    untilDate?: string
    fullResync?: boolean
    repoName?: string
  }
) => {
  const body: Record<string, unknown> = {
    since_date: params?.sinceDate,
    until_date: params?.untilDate,
    full_resync: params?.fullResync ?? false,
  }
  if (params?.repoName) body.repo_name = params.repoName
  const response = await api.post('/contributor-board/sync/contributors', body)
  return response.data
}

/**
 * 获取同步任务状态
 */
export const getSyncStatus = async (recordId: number) => {
  const response = await api.get<SyncRecord>(`/contributor-board/sync/status/${recordId}`)
  return response.data
}

/**
 * 取消/终止同步任务
 */
export const cancelSync = async (recordId: number) => {
  const response = await api.post(`/contributor-board/sync/status/${recordId}/cancel`)
  return response.data
}

/**
 * 获取同步历史记录
 */
export const getSyncHistory = async (params?: {
  repo_name?: string | null
  status?: string | null
  limit?: number
  offset?: number
}) => {
  const queryParams: Record<string, unknown> = {
    limit: params?.limit ?? 20,
    offset: params?.offset ?? 0,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.status) queryParams.status = params.status
  const response = await api.get<{ records: SyncRecord[]; total: number }>('/contributor-board/sync/history', { params: queryParams })
  return response.data
}

/**
 * 获取同步统计摘要
 */
export const getSyncSummary = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<SyncSummary>('/contributor-board/sync/summary', { params: queryParams })
  return response.data
}

export interface SyncSummary {
  total_syncs: number
  completed_count: number
  failed_count: number
  running_count: number
  total_contributors: number
  total_commits: number
  total_build_plans: number
  total_prs_scanned: number
  last_sync_at: string | null
  last_sync_status: string | null
}

// ==================== 角色统计 ====================

export interface RoleStats {
  developer_count: number
  reviewer_count: number
  maintainer_count: number
  total_pr: number
  total_code_lines: number
  total_reviews: number
  total_approvals: number
}

export interface ModuleHotspotItem {
  module: string
  count: number
  percentage: number
  contributors: number
}

export interface ModuleHotspotResponse {
  modules: ModuleHotspotItem[]
  total_changes: number
  repo_name: string | null
  period_start: string | null
  period_end: string | null
}

/**
 * 获取角色维度统计
 */
export const getRoleStats = async (params?: { repo_name?: string | null }) => {
  const response = await api.get<RoleStats>('/contributor-board/stats/roles', { params })
  return response.data
}

/**
 * 获取模块热点统计
 */
export const getModuleHotspot = async (params?: { repo_name?: string | null; period_start?: string | null; period_end?: string | null }) => {
  const response = await api.get<ModuleHotspotResponse>('/contributor-board/stats/module_hotspot', { params })
  return response.data
}

/**
 * 获取可选仓库列表
 */
export const getReposList = async () => {
  const response = await api.get<{ repos: string[] }>('/contributor-board/repos/list')
  return response.data
}

/**
 * 获取指定角色的排行榜
 */
export const getRoleRanking = async (
  roleType: string = 'developer',
  limit: number = 10
) => {
  const response = await api.get<ContributorRanking>(
    `/contributor-board/ranking/roles/${roleType}`,
    { params: { limit } }
  )
  return response.data
}

// ==================== 策略引擎 ====================

export interface PolicyItem {
  action: string
  severity: 'info' | 'warning' | 'critical'
  execution: 'auto' | 'suggestion' | 'alert'
  description: string
  reason: string
  impact_level: string
}

export interface PolicyResult {
  login: string
  quadrant: string
  contribution_score: number
  error_score: number
  policies: PolicyItem[]
  has_critical: boolean
  has_warning: boolean
  evaluated_at: string | null
  auto_count: number
  suggestion_count: number
  alert_count: number
}

export const getContributorPolicy = async (login: string) => {
  const response = await api.get<PolicyResult>(
    `/contributor-board/contributors/${login}/policy`
  )
  return response.data
}

// ==================== 扩展趋势 ====================

export interface CommitTrendPoint {
  date: string
  merged_pr_count: number
  code_additions: number
  code_deletions: number
}

export interface ReviewTrendPoint {
  date: string
  review_count: number
  approval_count: number
}

export interface ScoreTrendPoint {
  date: string
  contribution_score: number
  error_score: number
}

export interface ExtendedTrend {
  login: string
  commit_trend: CommitTrendPoint[]
  review_trend: ReviewTrendPoint[]
  score_trend: ScoreTrendPoint[]
}

export const getExtendedTrend = async (
  login: string,
  params?: { period_start?: string; period_end?: string }
) => {
  const response = await api.get<ExtendedTrend>(
    `/contributor-board/contributors/${login}/trend/extended`, { params }
  )
  return response.data
}

// ==================== 权重维度配置 ====================

type WeightKey = keyof ScoringWeightsConfig

export interface WeightDimensionConfig {
  key: WeightKey
  label: string
  min: number
  max: number
  step: number
}

export const WEIGHT_DIMENSIONS: WeightDimensionConfig[] = [
  { key: 'merged_pr_weight', label: 'PR 合并', min: 0, max: 30, step: 1 },
  { key: 'code_line_weight', label: '代码行', min: 0, max: 5, step: 0.1 },
  { key: 'review_weight', label: 'Review', min: 0, max: 15, step: 0.5 },
  { key: 'approval_weight', label: 'Approval', min: 0, max: 15, step: 0.5 },
  { key: 'core_module_weight', label: '核心模块', min: 0, max: 20, step: 1 },
  { key: 'files_changed_weight', label: '变更文件', min: 0, max: 5, step: 0.1 },
  { key: 'bugfix_weight', label: 'Bug 修复', min: 0, max: 5, step: 0.1 },
  { key: 'feature_weight', label: '新功能', min: 0, max: 5, step: 0.1 },
  { key: 'active_duration_weight', label: '活跃时长', min: 0, max: 1, step: 0.01 },
]

export const WEIGHT_LABELS: Record<WeightKey, string> = Object.fromEntries(
  WEIGHT_DIMENSIONS.map(d => [d.key, d.label])
) as Record<WeightKey, string>

// ==================== 方法标签和颜色 ====================

export const METHOD_CONFIG: Record<string, { label: string; color: string; emoji: string }> = {
  auto: { label: '自动算权', color: 'blue', emoji: '📊' },
  ai: { label: 'AI 评权', color: 'purple', emoji: '🤖' },
  manual: { label: '手动设置', color: 'default', emoji: '✏️' },
}

// ==================== 设置 ====================

export interface QuaddrantYAxisConfig {
  y_axis_type: string
}

/** 归一化权重：将原始倍数量纲值归一化为总和为 1 的比例 */
export function normalizeWeights(weights: Record<string, number>): Record<string, number> {
  const entries = Object.entries(weights)
  const total = entries.reduce((sum, [, v]) => sum + v, 0)
  if (total <= 0) {
    const n = entries.length
    return Object.fromEntries(entries.map(([k]) => [k, Number((1 / n).toFixed(4))]))
  }
  const result: Record<string, number> = {}
  for (const [k, v] of entries) {
    result[k] = Number((v / total).toFixed(4))
  }
  return result
}

export interface ScoringWeightsConfig {
  merged_pr_weight: number
  code_line_weight: number
  review_weight: number
  approval_weight: number
  core_module_weight: number
  files_changed_weight: number
  bugfix_weight: number
  feature_weight: number
  active_duration_weight: number
}

export interface RoleThresholdsConfig {
  star_threshold: number
  active_threshold: number
  max_error_score: number
}

export interface ContributorBoardSettings {
  quadrant_config: QuaddrantYAxisConfig
  scoring_weights: ScoringWeightsConfig
  role_thresholds: RoleThresholdsConfig
}

export const getContributorBoardSettings = async () => {
  const response = await api.get<ContributorBoardSettings>('/contributor-board/settings')
  return response.data
}

export const updateQuadrantConfig = async (y_axis_type: string) => {
  const response = await api.put('/contributor-board/settings/quadrant', { y_axis_type })
  return response.data
}

export const updateScoringWeights = async (weights: ScoringWeightsConfig) => {
  const response = await api.put('/contributor-board/settings/scoring_weights', weights)
  return response.data
}

export const updateRoleThresholds = async (thresholds: RoleThresholdsConfig) => {
  const response = await api.put('/contributor-board/settings/role_thresholds', thresholds)
  return response.data
}

/**
 * 触发重算所有贡献者分数
 */
export const recalculateScores = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.post('/contributor-board/settings/recalculate_scores', null, { params: queryParams })
  return response.data
}

export interface ContributorSyncScheduleConfig {
  full_cron: string
  incremental_interval_minutes: number
  enabled: boolean
}

export const getSyncSchedule = async () => {
  const response = await api.get<ContributorSyncScheduleConfig>('/contributor-board/settings/sync-schedule')
  return response.data
}

export const updateSyncSchedule = async (config: Partial<ContributorSyncScheduleConfig>) => {
    const response = await api.put<ContributorSyncScheduleConfig>('/contributor-board/settings/sync-schedule', config)
  return response.data
}

// ==================== 贡献者详情页增强 ====================

// --- 热力图 ---

export interface HeatmapDataPoint {
  date: string
  commit_count: number
  weekDay: number
}

export interface ContributorHeatmap {
  login: string
  heatmap_data: HeatmapDataPoint[]
}

export const getContributorHeatmap = async (
  login: string,
  params?: { repo_name?: string | null; years?: number }
) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.years) queryParams.years = params.years
  const response = await api.get<ContributorHeatmap>(
    `/contributor-board/contributors/${login}/heatmap`, { params: queryParams }
  )
  return response.data
}

// --- 模块+类型分布 ---

export interface ModuleDistributionItem {
  module: string
  commit_count: number
  code_additions: number
  code_deletions: number
}

export interface TypeDistributionItem {
  commit_type: string
  count: number
}

export interface ContributorDistribution {
  login: string
  modules: ModuleDistributionItem[]
  type_distribution: TypeDistributionItem[]
}

export const getContributorDistribution = async (
  login: string,
  params?: { repo_name?: string | null }
) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<ContributorDistribution>(
    `/contributor-board/contributors/${login}/distribution`, { params: queryParams }
  )
  return response.data
}

// --- 提交时间线 ---

export interface TimelineCommitItem {
  commit_hash: string
  message: string
  classification: string
  module: string
  committed_at: string | null
  files_changed: number
  code_additions: number
  code_deletions: number
}

export interface ContributorTimeline {
  login: string
  commits: TimelineCommitItem[]
}

export const getContributorTimeline = async (
  login: string,
  params?: { limit?: number }
) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.limit) queryParams.limit = params.limit
  const response = await api.get<ContributorTimeline>(
    `/contributor-board/contributors/${login}/timeline`, { params: queryParams }
  )
  return response.data
}

// ==================== 评分权重版本管理 ====================

export interface ScoringVersion {
  id: number
  version: string
  repo_name: string | null
  weights: Record<string, number>
  method: 'manual' | 'auto' | 'ai'
  status: 'draft' | 'active' | 'archived'
  data_summary: Record<string, any> | null
  ai_prompt: string | null
  ai_response: string | null
  ai_model: string | null
  confidence_score: number | null
  description: string | null
  created_at: string
  created_by: string | null
  activated_at: string | null
  activated_by: string | null
}

export interface VersionCompareResult {
  version_a: ScoringVersion
  version_b: ScoringVersion
  weight_diff: Record<string, { from: number; to: number; change: number; change_pct: number }>
  score_impact: { quadrant_changes?: Array<{ login: string; from: string; to: string }> } | null
}

export interface ScoreSnapshot {
  id: number
  version_id: number
  repo_name: string | null
  snapshot_date: string
  period_start: string | null
  period_end: string | null
  contributor_count: number
  statistics: { score_mean?: number; quadrant_distribution?: Record<string, number> } | null
  scores: Array<{ login: string; score: number; error_score: number; quadrant: string }>
  created_at: string
  created_by: string | null
  description: string | null
}

export interface ScoreUpdateAudit {
  id: number
  action: string
  version_id: number | null
  snapshot_id: number | null
  repo_name: string | null
  details: Record<string, any> | null
  summary: string | null
  weight_diff: Record<string, any> | null
  max_weight_shift: number | null
  created_at: string
  created_by: string | null
}

export const getActiveScoringVersion = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<ScoringVersion | null>('/contributor-board/settings/scoring/versions/active', { params: queryParams })
  return response.data
}

export const listScoringVersions = async (params?: {
  repo_name?: string | null
  status?: string | null
  skip?: number
  limit?: number
}) => {
  const queryParams: Record<string, unknown> = {
    skip: params?.skip ?? 0,
    limit: params?.limit ?? 50,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.status) queryParams.status = params.status
  const response = await api.get<{ versions: ScoringVersion[]; total: number }>('/contributor-board/settings/scoring/versions', { params: queryParams })
  return response.data
}

export const getScoringVersion = async (versionId: number) => {
  const response = await api.get<ScoringVersion | null>(`/contributor-board/settings/scoring/versions/${versionId}`)
  return response.data
}

export const createScoringVersion = async (body: {
  method: string
  weights: Record<string, number>
  data_summary?: Record<string, any> | null
  confidence_score?: number | null
  ai_prompt?: string | null
  ai_response?: string | null
  ai_model?: string | null
  description?: string | null
  repo_name?: string | null
  apply_immediately?: boolean
}) => {
  const response = await api.post<ScoringVersion>('/contributor-board/settings/scoring/versions', body)
  return response.data
}

export const applyScoringVersion = async (versionId: number) => {
  const response = await api.post<ScoringVersion>(`/contributor-board/settings/scoring/versions/${versionId}/apply`)
  return response.data
}

export const rollbackScoringVersion = async (versionId: number) => {
  const response = await api.post<ScoringVersion>(`/contributor-board/settings/scoring/versions/${versionId}/rollback`)
  return response.data
}

export const compareScoringVersions = async (versionIdA: number, versionIdB: number) => {
  const response = await api.get<VersionCompareResult>(
    '/contributor-board/settings/scoring/versions/compare',
    { params: { version_id_a: versionIdA, version_id_b: versionIdB } }
  )
  return response.data
}

export const calculateAutoWeights = async (params?: { repo_name?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.post<AutoWeightsResult>('/contributor-board/settings/scoring/auto-evaluate', null, { params: queryParams })
  return response.data
}

export const aiAnalyzeWeights = async (params?: { repo_name?: string | null; model?: string | null }) => {
  const queryParams: Record<string, unknown> = {}
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.model) queryParams.model = params.model
  const response = await api.post<AutoWeightsResult>('/contributor-board/settings/scoring/ai-evaluate', null, { params: queryParams })
  return response.data
}



export const exportPdfByVersion = async (versionId: number) => {
  const response = await api.get('/contributor-board/settings/scoring/snapshots/export/pdf', {
    params: { version_id: versionId },
    responseType: 'blob',
  })
  return response.data
}

export interface AutoWeightsResult {
  weights: Record<string, number>
  confidence: number
  data_summary: Record<string, any>
  version_id?: number | null
}

export const listScoreSnapshots = async (params?: {
  repo_name?: string | null
  skip?: number
  limit?: number
}) => {
  const queryParams: Record<string, unknown> = {
    skip: params?.skip ?? 0,
    limit: params?.limit ?? 50,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<{ snapshots: ScoreSnapshot[]; total: number }>('/contributor-board/settings/scoring/snapshots', { params: queryParams })
  return response.data
}

export const getScoreSnapshot = async (snapshotId: number) => {
  const response = await api.get<ScoreSnapshot | null>(`/contributor-board/settings/scoring/snapshots/${snapshotId}`)
  return response.data
}

export const generateScoreSnapshot = async (versionId: number) => {
  const response = await api.post<ScoreSnapshot | null>(
    '/contributor-board/settings/scoring/snapshots/generate',
    null,
    { params: { version_id: versionId } }
  )
  return response.data
}

export const listScoreAuditLogs = async (params?: {
  repo_name?: string | null
  skip?: number
  limit?: number
}) => {
  const queryParams: Record<string, unknown> = {
    skip: params?.skip ?? 0,
    limit: params?.limit ?? 100,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  const response = await api.get<{ audits: ScoreUpdateAudit[]; total: number }>('/contributor-board/settings/scoring/audit-log', { params: queryParams })
  return response.data
}

// ==================== AI 综合分析报表 ====================

export interface AIReportGenerateRequest {
  report_type: 'quarterly' | 'monthly' | 'custom' | 'adhoc'
  period_start?: string | null
  period_end?: string | null
  repo_name?: string | null
  model?: string | null
  include_individual_evaluations?: boolean
}

export interface AIReportListItem {
  id: number
  report_name: string
  report_type: string | null
  period_start: string | null
  period_end: string | null
  generated_at: string
  generated_by: string | null
  generation_source: string | null
  weight_version_id: number | null
  llm_model: string | null
  executive_summary: string | null
  confidence_overall: number | null
  repo_name: string | null
  created_at: string
}

export interface AIReportListResponse {
  reports: AIReportListItem[]
  total: number
}

export interface AIReportDetail {
  id: number
  report_name: string
  report_type: string | null
  period_start: string | null
  period_end: string | null
  generated_at: string
  generated_by: string | null
  generation_source: string | null
  weight_version_id: number | null
  llm_model: string | null
  executive_summary: string | null
  quadrant_analysis?: Record<string, any> | null
  individual_evaluations?: Array<Record<string, any>> | null
  recommendations?: Array<Record<string, any>> | null
  confidence_overall?: number | null
  report_content?: string | null
  report_file_url?: string | null
  schedule_cron?: string | null
  enabled: boolean
  repo_name: string | null
  created_at: string
  updated_at?: string | null
}

export const generateAIReport = async (request: AIReportGenerateRequest) => {
  const response = await api.post<AIReportListItem>('/contributor-board/settings/reports/ai/generate', request)
  return response.data
}

export const listAIReports = async (params?: {
  repo_name?: string | null
  report_type?: string | null
  skip?: number
  limit?: number
}) => {
  const queryParams: Record<string, unknown> = {
    skip: params?.skip ?? 0,
    limit: params?.limit ?? 50,
  }
  if (params?.repo_name) queryParams.repo_name = params.repo_name
  if (params?.report_type) queryParams.report_type = params.report_type
  const response = await api.get<AIReportListResponse>('/contributor-board/settings/reports/ai', { params: queryParams })
  return response.data
}

export const getAIReportDetail = async (reportId: number) => {
  const response = await api.get<AIReportDetail>(`/contributor-board/settings/reports/ai/${reportId}`)
  return response.data
}
