import api from './api'

export interface MonitorTarget {
  id: number
  release_workflow_id: number
  repo_owner: string
  repo_name: string
  workflow_name: string
  workflow_path: string
  workflow_type: string
  enabled: boolean
  schedule_cron: string | null
  timezone: string
  grace_minutes: number
  lookback_days: number
  notify_emails: string[]
  notify_on_failure: boolean
  notify_on_missing: boolean
  notify_on_stuck: boolean
  stuck_threshold_minutes: number
  cooldown_minutes: number
  last_checked_at: string | null
  last_check_success_at: string | null
  last_check_error: string | null
  open_alert_count: number
  latest_run: {
    github_run_id: number
    status: string | null
    event: string | null
    branch: string | null
    created_at: string | null
    wall_duration: number | null
    url: string | null
  } | null
}

export interface MonitorCandidate {
  id: number
  repo_owner: string
  repo_name: string
  workflow_id: number
  workflow_name: string
  workflow_path: string
  workflow_type: string
  enabled: boolean
  already_monitored: boolean
}

export interface MonitorAlert {
  id: number
  target_id: number
  release_workflow_id: number
  repo_owner: string
  repo_name: string
  workflow_name: string
  github_run_id: number | null
  alert_type: 'failure' | 'missing' | 'stuck'
  severity: string
  status: 'open' | 'acknowledged' | 'resolved'
  title: string
  message: string | null
  run_url: string | null
  branch: string | null
  event: string | null
  first_detected_at: string | null
  last_detected_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  notified_at: string | null
  last_notified_at: string | null
  notification_success: boolean | null
  notification_error: string | null
  details: Record<string, unknown> | null
}

export interface AlertFailedStep {
  name: string
  number: number | null
  status: string
  duration: number
}

export interface AlertFailedJob {
  name: string
  target: string | null
  os: string | null
  phase: string | null
  architecture: string | null
  status: string | null
  url: string | null
  failed_steps: AlertFailedStep[]
}

export interface AlertDetail extends MonitorAlert {
  job_summary: Record<string, number>
  failed_jobs: AlertFailedJob[]
  run: {
    github_run_id: number
    status: string | null
    event: string | null
    branch: string | null
    commit_sha: string | null
    created_at: string | null
    completed_at: string | null
    wall_duration: number | null
    num_jobs: number
    url: string | null
  } | null
}

export interface MonitorOverview {
  days_back: number
  targets_total: number
  targets_enabled: number
  runs_total: number
  failed_runs: number
  alerts_total: number
  open_alerts: number
  unresolved_alerts: number
  by_type: Record<string, number>
  by_status: Record<string, number>
  monitor_health: {
    last_checked_at: string | null
    last_success_at: string | null
    targets_with_error: number
  }
  target_health: MonitorTarget[]
  recent_alerts: MonitorAlert[]
}

export interface TargetPayload {
  release_workflow_id: number
  enabled?: boolean
  schedule_cron?: string | null
  timezone?: string
  grace_minutes?: number
  lookback_days?: number
  notify_emails?: string[]
  notify_on_failure?: boolean
  notify_on_missing?: boolean
  notify_on_stuck?: boolean
  stuck_threshold_minutes?: number
  cooldown_minutes?: number
}

export interface TargetRun {
  id: number
  github_run_id: number
  status: string | null
  event: string | null
  branch: string | null
  commit_sha: string | null
  created_at: string | null
  completed_at: string | null
  wall_duration: number | null
  num_jobs: number
  url: string | null
}

export interface GithubStatus {
  ok: boolean
  mode: 'authenticated' | 'anonymous' | 'none' | 'token_invalid'
  remaining?: number | null
  message: string
}

export const getGithubStatus = async (): Promise<GithubStatus> => {
  const { data } = await api.get<GithubStatus>('/image-build-monitor/github-status')
  return data
}

export const getOverview = async (daysBack = 7): Promise<MonitorOverview> => {
  const { data } = await api.get<MonitorOverview>('/image-build-monitor/overview', {
    params: { days_back: daysBack },
  })
  return data
}

export const listTargets = async (): Promise<MonitorTarget[]> => {
  const { data } = await api.get<{ items: MonitorTarget[] }>('/image-build-monitor/targets')
  return data.items
}

export const listCandidates = async (): Promise<MonitorCandidate[]> => {
  const { data } = await api.get<{ items: MonitorCandidate[] }>('/image-build-monitor/candidates')
  return data.items
}

export const createTarget = async (payload: TargetPayload): Promise<{ id: number }> => {
  const { data } = await api.post('/image-build-monitor/targets', payload)
  return data
}

export const updateTarget = async (id: number, payload: TargetPayload): Promise<void> => {
  await api.put(`/image-build-monitor/targets/${id}`, payload)
}

export const deleteTarget = async (id: number): Promise<void> => {
  await api.delete(`/image-build-monitor/targets/${id}`)
}

export const getTargetRuns = async (id: number, limit = 20): Promise<TargetRun[]> => {
  const { data } = await api.get<{ items: TargetRun[] }>(`/image-build-monitor/targets/${id}/runs`, {
    params: { limit },
  })
  return data.items
}

export const listAlerts = async (params?: {
  status?: string
  alert_type?: string
  release_workflow_id?: number
  page?: number
  page_size?: number
}): Promise<{ total: number; page: number; page_size: number; items: MonitorAlert[] }> => {
  const { data } = await api.get('/image-build-monitor/alerts', { params })
  return data
}

export const getAlertDetail = async (id: number): Promise<AlertDetail> => {
  const { data } = await api.get<AlertDetail>(`/image-build-monitor/alerts/${id}/detail`)
  return data
}

export const ackAlert = async (id: number): Promise<void> => {
  await api.post(`/image-build-monitor/alerts/${id}/ack`)
}

export const resolveAlert = async (id: number): Promise<void> => {
  await api.post(`/image-build-monitor/alerts/${id}/resolve`)
}

export const renotifyAlert = async (id: number): Promise<void> => {
  await api.post(`/image-build-monitor/alerts/${id}/renotify`)
}

export const runCheck = async (): Promise<{
  created: number
  notified: number
  resolved: number
  targets: number
  synced_runs?: number
  errors?: string[]
}> => {
  const { data } = await api.post('/image-build-monitor/check')
  return data
}

export const sendTestEmail = async (payload: {
  recipients: string[]
  target_id?: number
}): Promise<{
  success: boolean
  recipients: string[]
  error: string | null
  subject: string
}> => {
  const { data } = await api.post('/image-build-monitor/test-email', payload)
  return data
}
