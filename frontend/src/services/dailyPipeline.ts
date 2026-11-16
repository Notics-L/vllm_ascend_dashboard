import api from './api'
import type { DailyOverviewData, DailyCase, DailyCaseUpdate, DailyCollectionRun, DailyCollectionDetail, DailyFailedCase, DailyFailedCaseDetail, DailyIssueDetail, DailyIssueItem, DailyPipelineSchedule, DailyTriggerHistoryDetail, DailyTriggerHistoryPage, DailySelectionResult, DailyTriggerConfig, DailyTriggerConfigInput, NightlyCase } from '../types/dailyPipeline'

export type { DailyCase, DailyCaseInput, DailyCaseUpdate, DailySelectionResult, NightlyCase } from '../types/dailyPipeline'


export const getDailyCases = async (): Promise<DailyCase[]> => {
  const response = await api.get<DailyCase[]>('/daily-pipeline/cases')
  return response.data
}


export const getNightlyCases = async (): Promise<NightlyCase[]> => {
  const response = await api.get<NightlyCase[]>('/daily-pipeline/nightly-cases')
  return response.data
}

export const addDailyCasesFromNightly = async (nightlyCaseIds: string[]): Promise<DailyCase[]> => {
  const response = await api.post<DailyCase[]>('/daily-pipeline/cases/from-nightly', { nightlyCaseIds })
  return response.data
}

export const updateDailyCase = async ({ id, values }: { id: string; values: Partial<DailyCaseUpdate> }): Promise<DailyCase> => {
  const response = await api.patch<DailyCase>(`/daily-pipeline/cases/${encodeURIComponent(id)}`, values)
  return response.data
}

export const deleteDailyCases = async (ids: string[]): Promise<{ deleted: number }> => {
  const response = await api.delete<{ deleted: number }>('/daily-pipeline/cases', { data: { ids } })
  return response.data
}

export const saveDailyCaseSelection = async (nightlyCaseIds: string[]): Promise<DailySelectionResult> => {
  const response = await api.put<DailySelectionResult>('/daily-pipeline/cases/selection', { nightlyCaseIds })
  return response.data
}

export const updateDailyCaseStatuses = async ({ ids, status }: { ids: string[]; status: DailyCase['status'] }): Promise<{ updated: number }> => {
  const response = await api.patch<{ updated: number }>('/daily-pipeline/cases/status', { ids, status })
  return response.data
}

export const getDailySchedule = async (): Promise<DailyPipelineSchedule> => {
  const response = await api.get<DailyPipelineSchedule>('/daily-pipeline/schedule')
  return response.data
}

export const saveDailySchedule = async (cron: string): Promise<DailyPipelineSchedule> => {
  const response = await api.put<DailyPipelineSchedule>('/daily-pipeline/schedule', { cron })
  return response.data
}

export const getDailyTriggerConfig = async (): Promise<DailyTriggerConfig> => {
  const response = await api.get<DailyTriggerConfig>('/daily-pipeline/trigger-config')
  return response.data
}

export const saveDailyTriggerConfig = async (values: DailyTriggerConfigInput): Promise<DailyTriggerConfig> => {
  const response = await api.put<DailyTriggerConfig>('/daily-pipeline/trigger-config', values)
  return response.data
}

export const testDailyTriggerConnection = async (githubToken?: string): Promise<{ detail: string }> => {
  const response = await api.post<{ detail: string }>('/daily-pipeline/trigger-config/test-connection',
    githubToken ? { githubToken } : {})
  return response.data
}

export const previewDailySchedule = async (cron: string, signal?: AbortSignal): Promise<DailyPipelineSchedule> => {
  const response = await api.post<DailyPipelineSchedule>('/daily-pipeline/schedule/preview', { cron }, { signal })
  return response.data
}

export const getDailyTriggerHistory = async (page: number, pageSize: number): Promise<DailyTriggerHistoryPage> => {
  const response = await api.get<DailyTriggerHistoryPage>('/daily-pipeline/trigger-history', { params: { page, page_size: pageSize } })
  return response.data
}

export const getDailyTriggerHistoryDetail = async (id: string): Promise<DailyTriggerHistoryDetail> => {
  const response = await api.get<DailyTriggerHistoryDetail>(`/daily-pipeline/trigger-history/${encodeURIComponent(id)}`)
  return response.data
}

export const deleteDailyTriggerHistory = async (ids: string[]): Promise<{ deleted: number }> => {
  const response = await api.delete<{ deleted: number }>('/daily-pipeline/trigger-history', { data: { ids } })
  return response.data
}

export const getDailyCollectionRuns = async (): Promise<DailyCollectionRun[]> => {
  return (await api.get('/daily-pipeline/collection/runs')).data
}

export const getDailyCollectionDetail = async (id: string): Promise<DailyCollectionDetail> => {
  return (await api.get(`/daily-pipeline/collection/runs/${encodeURIComponent(id)}`)).data
}

export const recollectDailyRun = async (id: string): Promise<{ status: '等待采集' }> => {
  return (await api.post(`/daily-pipeline/collection/runs/${encodeURIComponent(id)}/recollect`)).data
}

export const deleteDailyCollectionRuns = async (runIds: number[]): Promise<{ deleted: number }> => {
  return (await api.delete<{ deleted: number }>('/daily-pipeline/collection/runs', { data: { runIds } })).data
}

export const getDailyFailedCases = async (): Promise<DailyFailedCase[]> => {
  return (await api.get<DailyFailedCase[]>('/daily-pipeline/failed-cases')).data
}

export const getDailyIssueDetail = async (number: number): Promise<DailyIssueDetail> => {
  return (await api.get<DailyIssueDetail>(`/daily-pipeline/issues/${number}`)).data
}

export const getDailyIssues = async (): Promise<DailyIssueItem[]> => {
  return (await api.get<DailyIssueItem[]>('/daily-pipeline/issues')).data
}

export const getDailyFailedCase = async (id: string): Promise<DailyFailedCaseDetail> => {
  return (await api.get<DailyFailedCaseDetail>(`/daily-pipeline/failed-cases/${encodeURIComponent(id)}`)).data
}

export const closeDailyIssues = async (numbers: number[]): Promise<{ closed: number }> => {
  return (await api.post<{ closed: number }>('/daily-pipeline/issues/close', { numbers })).data
}

export const deleteDailyIssues = async (numbers: number[]): Promise<{ deleted: number }> => {
  return (await api.delete<{ deleted: number }>('/daily-pipeline/issues', { data: { numbers } })).data
}

export const getDailyOverview = async (days: 7 | 14): Promise<DailyOverviewData> => {
  return (await api.get<DailyOverviewData>('/daily-pipeline/overview', { params: { days } })).data
}
