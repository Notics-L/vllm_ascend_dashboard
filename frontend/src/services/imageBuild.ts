import api from './api'

export interface ImageBuildWorkflowInput {
  name: string
  type: string
  description: string
  required: boolean
  options: string[]
  default?: string | number | boolean | null
}

export interface ImageBuildWorkflowDefinition {
  branch_name: string
  workflow_url: string
  inputs: ImageBuildWorkflowInput[]
  inputs_hash: string
  update_at: string
}

export async function getImageBuildWorkflows(signal?: AbortSignal) {
  const response = await api.get<ImageBuildWorkflowDefinition[]>('/image-build/workflows', { signal })
  return response.data
}

interface ImageBuildWorkflowSyncResult {
  branches: number
  changed: number
  failed: number
}

export async function syncImageBuildWorkflows() {
  const response = await api.post<ImageBuildWorkflowSyncResult>('/image-build/workflows/sync', undefined, {
    timeout: 360_000,
  })
  return response.data
}

export type ImageBuildSourceRefMode = 'branch' | 'recommended' | 'manual'

export interface ImageBuildTriggerTaskValues {
  workflow_branch: string
  cron_expression: string
  source_ref_mode: ImageBuildSourceRefMode
  inputs: Record<string, ImageBuildWorkflowInput['default']>
}

export interface ImageBuildTriggerTask extends ImageBuildTriggerTaskValues {
  id: number
  update_at: string
}

const triggerTasksPath = '/image-build/trigger-tasks'

export async function getImageBuildTriggerTasks(signal?: AbortSignal) {
  const response = await api.get<ImageBuildTriggerTask[]>(triggerTasksPath, { signal })
  return response.data
}

export async function saveImageBuildTriggerTask(id: number | undefined, values: ImageBuildTriggerTaskValues) {
  const response = id === undefined
    ? await api.post<ImageBuildTriggerTask>(triggerTasksPath, values)
    : await api.put<ImageBuildTriggerTask>(`${triggerTasksPath}/${id}`, values)
  return response.data
}

export async function deleteImageBuildTriggerTasks(ids: number[]) {
  const response = await api.delete<{ deleted: number }>(triggerTasksPath, { data: { ids } })
  return response.data
}


export interface ImageBuildTriggerRecord {
  id: number
  task_id: number
  workflow_branch: string
  username: string
  status: 'submitting' | 'submitted' | 'failed' | 'unknown'
  message: string
  run_id: number | null
  run_url: string | null
  triggered_at: string
}

export async function triggerImageBuildTask(id: number) {
  const response = await api.post<ImageBuildTriggerRecord>(`${triggerTasksPath}/${id}/trigger`)
  return response.data
}

export interface ImageBuildTriggerRecordDetail extends ImageBuildTriggerRecord {
  dispatch_snapshot: {
    repository: string
    workflow_path: string
    source_ref_mode: ImageBuildSourceRefMode | null
    source_recommendation: { trigger_history_id: number; pr_number: number | null; problem_pr_number: number | null } | null
    inputs: Record<string, ImageBuildWorkflowInput['default']>
  } | null
  github_status_code: number | null
}

const triggerRecordsPath = '/image-build/trigger-records'

export async function getImageBuildTriggerRecords(signal?: AbortSignal) {
  const response = await api.get<ImageBuildTriggerRecord[]>(triggerRecordsPath, { signal })
  return response.data
}

export async function getImageBuildTriggerRecord(id: number, signal?: AbortSignal) {
  const response = await api.get<ImageBuildTriggerRecordDetail>(`${triggerRecordsPath}/${id}`, { signal })
  return response.data
}

export interface ImageBuildStageResult {
  succeeded: number
  total: number
  result: string
  jobUrl: string | null
}

export interface ImageBuildMatrixRow {
  key: string
  chip: string
  os: string
  base: ImageBuildStageResult
  nightly: ImageBuildStageResult
  startedAt: string | null
  endedAt: string | null
}

export interface ImageBuildPublishedImage {
  chip: string
  os: string
  layer: 'Base' | 'Nightly'
  repositories: string[]
  tag: string
  digest: string
  commits: { arm64: string | null; amd64: string | null }
}

export interface ImageBuildPublication {
  sourceBranch: string
  reportUrl: string
  images: ImageBuildPublishedImage[]
}

export interface ImageBuildCollectionRun {
  runId: number
  workflow: string
  workflowBranch: string
  workflowSha: string | null
  actor: string
  result: string
  startedAt: string | null
  endedAt: string | null
  githubUrl: string
  runAttempt: number | null
  canRerun: boolean
  collecting: boolean
  collectionError: string | null
  chips: string[]
  publicationStatus: 'pending' | 'published' | 'skipped' | 'unavailable'
}

export interface ImageBuildCollectionDetail extends ImageBuildCollectionRun {
  matrix: ImageBuildMatrixRow[]
  publication: ImageBuildPublication | null
}

export const imageBuildCollectionQueryKey = ['image-build', 'collection'] as const
const collectionRunsPath = '/image-build/collection/runs'

export async function getImageBuildCollectionRuns(page: number, pageSize: number, signal?: AbortSignal) {
  const response = await api.get<{ items: ImageBuildCollectionRun[]; total: number }>(collectionRunsPath, {
    params: { page, page_size: pageSize }, signal,
  })
  return response.data
}

export async function getImageBuildCollectionRun(runId: number, signal?: AbortSignal) {
  const response = await api.get<ImageBuildCollectionDetail>(`${collectionRunsPath}/${runId}`, { signal })
  return response.data
}

export async function refreshImageBuildCollectionRun(runId: number) {
  const response = await api.post<ImageBuildCollectionDetail>(`${collectionRunsPath}/${runId}/refresh`)
  return response.data
}

export async function rerunFailedImageBuildJobs(runId: number) {
  const response = await api.post<{ status: 'submitted' | 'unknown' }>(`${collectionRunsPath}/${runId}/rerun-failed`)
  return response.data
}
