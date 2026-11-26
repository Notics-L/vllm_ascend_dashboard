import api from './api'

export interface StatusSummary {
  total: number
  passed: number
  failed: number
  skipped: number
  unknown: number
}

export interface CategoryCount {
  category: string
  count: number
}

export interface CiRun {
  id: number | null
  run_number: number | null
  event: string | null
  status: string | null
  conclusion: string | null
  branch: string | null
  created_at: string | null
  updated_at: string | null
  html_url: string | null
}

export interface FailureReason {
  reason: string
  count: number
}

export interface UpstreamTestsStat {
  available: boolean
  repo?: string
  ref?: string | null
  count?: number | null
  truncated?: boolean
  rule?: string
  reason?: string | null
}

export interface CiSummary {
  available: boolean
  reason?: string | null
  workflow_id?: number
  workflow_name?: string
  workflow_path?: string
  workflow_url?: string
  repo_url?: string
  week_start?: string
  stats_scope?: string
  stats_start?: string
  total?: number
  scheduled_total?: number
  pr_total?: number
  succeeded?: number
  failed?: number
  cancelled?: number
  fallback_used?: boolean
  latest?: CiRun | null
  runs?: CiRun[]
  recent_scheduled?: CiRun[]
}

export interface UpstreamOverview {
  available: boolean
  has_snapshot?: boolean
  last_error?: string | null
  message?: string
  snapshot_id?: number
  fetched_at?: string | null
  stale?: boolean
  source_repo?: string
  // 部分数据源抓取失败时的告警，页面据此提示"数据不完整"
  warnings?: string[]
  weekly?: {
    file: string | null
    summary: StatusSummary
    categories: CategoryCount[]
    failure_reasons: FailureReason[]
    // 本周失败用例关联的开启中 issue 数（按 issue 去重）
    issue_count?: number
  }
  version?: {
    name: string | null
    file: string | null
    previous_name?: string | null
    summary: StatusSummary
    failure_reasons: FailureReason[]
  }
  issues?: {
    summary: Record<string, number>
    added_count?: number | null
  }
  upstream_tests?: UpstreamTestsStat
  ci?: CiSummary
}

export interface CaseRow {
  id: number
  test_path: string
  status: string
  snippet: string | null
  analysis: string | null
  category: string | null
  case_version: string | null
  removed: string | null
}

export interface IssueRow {
  id: number
  issue_number: number | null
  issue_url: string | null
  issue_title: string | null
  test_path: string | null
  impact_level: string | null
  impact: string | null
  notes: string | null
}

export interface Paged<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface CaseQuery {
  scope?: 'weekly' | 'version'
  status?: string
  category?: string
  keyword?: string
  page?: number
  page_size?: number
}

export interface IssueQuery {
  state?: 'open' | 'resolved' | 'other'
  keyword?: string
  page?: number
  page_size?: number
}

export async function getUpstreamOverview(): Promise<UpstreamOverview> {
  const { data } = await api.get<UpstreamOverview>('/upstream-test/overview')
  return data
}

export async function listUpstreamCases(params: CaseQuery): Promise<Paged<CaseRow>> {
  const { data } = await api.get<Paged<CaseRow>>('/upstream-test/cases', { params })
  return data
}

export async function listUpstreamIssues(params: IssueQuery): Promise<Paged<IssueRow>> {
  const { data } = await api.get<Paged<IssueRow>>('/upstream-test/issues', { params })
  return data
}

export async function syncUpstreamTest(): Promise<Record<string, unknown>> {
  const { data } = await api.post<Record<string, unknown>>('/upstream-test/sync')
  return data
}

export interface VersionDetail {
  cases: Paged<CaseRow>
  issues: IssueRow[]
  version: {
    name: string | null
    previous_name?: string | null
    summary: StatusSummary
    failure_reasons: FailureReason[]
  } | null
}

export interface CiDetail {
  cases: Paged<CaseRow>
  issues: IssueRow[]
  ci: CiSummary
  weekly?: {
    file: string | null
    summary: StatusSummary
    failure_reasons: FailureReason[]
  }
}

export async function getVersionDetail(params: {
  status?: string
  keyword?: string
  page?: number
  page_size?: number
}): Promise<VersionDetail> {
  const { data } = await api.get<VersionDetail>('/upstream-test/version-detail', { params })
  return data
}

export async function getCiDetail(params: {
  status?: string
  keyword?: string
  page?: number
  page_size?: number
}): Promise<CiDetail> {
  const { data } = await api.get<CiDetail>('/upstream-test/ci-detail', { params })
  return data
}
