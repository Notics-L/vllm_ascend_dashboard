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
export const getClassificationSummary = async () => {
  const response = await api.get<ClassificationSummary>('/contributor-board/commits/classifications')
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
export const getBuildOptimizationStats = async () => {
  const response = await api.get<BuildOptimizationStats>('/contributor-board/builds/stats')
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
  created_at: string | null
}

/**
 * 获取构建计划列表
 */
export const getBuildPlans = async (params?: {
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
  if (params?.build_type) queryParams.build_type = params.build_type
  if (params?.commit_type) queryParams.commit_type = params.commit_type
  if (params?.created_after) queryParams.created_after = params.created_after
  if (params?.created_before) queryParams.created_before = params.created_before
  const response = await api.get<{ plans: BuildPlan[]; total: number }>('/contributor-board/builds/plans', { params: queryParams })
  return response.data
}

export const getBuildFilterOptions = async () => {
  const response = await api.get<{ commit_types: string[]; build_types: string[] }>('/contributor-board/builds/filter_options')
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

/**
 * 触发同步贡献者数据
 */
export const syncContributors = async (
  sinceDate?: string,
  fullResync: boolean = false
) => {
  const response = await api.post('/contributor-board/sync/contributors', {
    since_date: sinceDate,
    full_resync: fullResync,
  })
  return response.data
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
  description: string
  reason: string
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

// ==================== 设置 ====================

export interface QuaddrantYAxisConfig {
  y_axis_type: string
}

export interface ScoringWeightsConfig {
  merged_pr_weight: number
  code_line_weight: number
  review_weight: number
  approval_weight: number
  core_module_weight: number
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
