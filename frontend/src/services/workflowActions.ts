import axios from 'axios'
import api from './api'

export const formatWorkflowActionDetail = (value: unknown): string => {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (item && typeof item === 'object') {
          const entry = item as { loc?: unknown[]; msg?: string }
          const location = Array.isArray(entry.loc)
            ? `${entry.loc.join('.')}: `
            : ''
          return `${location}${entry.msg || JSON.stringify(item)}`
        }
        return String(item)
      })
      .join('\n')
  }
  if (value && typeof value === 'object') return JSON.stringify(value)
  return value == null ? '' : String(value)
}

export const getWorkflowActionError = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as
      | { msg?: unknown; detail?: unknown }
      | undefined
    return (
      formatWorkflowActionDetail(data?.msg) ||
      formatWorkflowActionDetail(data?.detail) ||
      error.message
    )
  }
  return error instanceof Error ? error.message : String(error)
}

export type WorkflowAction =
  | 'dispatch'
  | 'rerun'
  | 'rerun-failed'
  | 'cancel'
  | 'force-cancel'

export interface WorkflowActionRequest {
  action: WorkflowAction
  workflow_path: string
  ref?: string
  inputs_yaml?: string
  run_id?: number
}

export interface WorkflowActionResult {
  success: boolean
  status_code: number | null
  output: string
  run_url: string | null
}

export interface WorkflowActionLog {
  id: number
  username: string
  command_text: string
  created_at: string
}

export interface WorkflowActionLogPage {
  total: number
  page: number
  page_size: number
  items: WorkflowActionLog[]
}

export interface WorkflowActionPermission {
  username: string
  workflow_paths: string[]
  schedule_permission: boolean
  granted_by: string
  created_at: string
}

export const getMyPermissions = async (): Promise<string[]> => {
  const response = await api.get<{ items: string[] }>(
    '/workflow-actions/permissions',
  )
  return response.data.items
}

export const executeWorkflowAction = async (
  data: WorkflowActionRequest,
): Promise<WorkflowActionResult> => {
  const response = await api.post<WorkflowActionResult>(
    '/workflow-actions/execute',
    data,
  )
  return response.data
}

export const getWorkflowActionLogs = async (
  page: number,
): Promise<WorkflowActionLogPage> => {
  const response = await api.get<WorkflowActionLogPage>(
    '/workflow-actions/logs',
    {
      params: { page, page_size: 20 },
    },
  )
  return response.data
}

export interface WorkflowActionCredentialStatus {
  configured: boolean
  updated_at: string | null
  updated_by: string | null
}

export const getActionsCredentialStatus =
  async (): Promise<WorkflowActionCredentialStatus> => {
    const response = await api.get<WorkflowActionCredentialStatus>(
      '/workflow-actions/admin/credential',
    )
    return response.data
  }

export const saveActionsCredential = async (
  token: string,
): Promise<WorkflowActionCredentialStatus> => {
  const response = await api.put<WorkflowActionCredentialStatus>(
    '/workflow-actions/admin/credential',
    { token },
  )
  return response.data
}

export const clearActionsCredential =
  async (): Promise<WorkflowActionCredentialStatus> => {
    const response = await api.delete<WorkflowActionCredentialStatus>(
      '/workflow-actions/admin/credential',
    )
    return response.data
  }

export const testActionsCredential =
  async (): Promise<WorkflowActionResult> => {
    const response = await api.post<WorkflowActionResult>(
      '/workflow-actions/admin/credential/test',
    )
    return response.data
  }

export const getAdminPermissions = async (): Promise<
  WorkflowActionPermission[]
> => {
  const response = await api.get<{ items: WorkflowActionPermission[] }>(
    '/workflow-actions/admin/permissions',
  )
  return response.data.items
}

export interface WorkflowActionPermissionBatchResult {
  user_count: number
  created_count: number
  skipped_count: number
  schedule_created_count: number
  schedule_skipped_count: number
  message: string
}

export const createAdminPermission = async (data: {
  usernames: string[]
  workflow_paths: string[]
  schedule_permission: boolean
}): Promise<WorkflowActionPermissionBatchResult> => {
  const response = await api.post<WorkflowActionPermissionBatchResult>(
    '/workflow-actions/admin/permissions',
    data,
  )
  return response.data
}

export const updateUserPermissions = async (
  username: string,
  workflowPaths: string[],
  schedulePermission: boolean,
): Promise<void> => {
  await api.put(
    `/workflow-actions/admin/permissions/users/${encodeURIComponent(username)}`,
    {
      workflow_paths: workflowPaths,
      schedule_permission: schedulePermission,
    },
  )
}

export interface WorkflowSchedule {
  id: number
  name: string
  workflow_path: string
  ref: string
  inputs_yaml: string
  cron_expression: string
  cron_preview_expression: string | null
  timezone: string
  enabled: boolean
  setter_username: string
  created_at: string
  updated_at: string
  last_run_at: string | null
  last_output: string | null
  last_submission_status: 'submitted' | 'failed' | 'unknown' | null
  next_run_at: string | null
}

export interface WorkflowSchedulePayload {
  name: string
  workflow_path: string
  ref: string
  inputs_yaml: string
  cron_expression: string
  enabled: boolean
}

export const getSchedules = async (): Promise<WorkflowSchedule[]> => {
  const response = await api.get<{ items: WorkflowSchedule[] }>(
    '/workflow-actions/schedules',
  )
  return response.data.items
}

export const getCronPreview = async (expression: string): Promise<string> => {
  const response = await api.get<{ expression: string }>(
    '/workflow-actions/schedules/cron-preview',
    { params: { expression } },
  )
  return response.data.expression
}

export const createSchedule = async (
  data: WorkflowSchedulePayload,
): Promise<WorkflowSchedule> => {
  const response = await api.post<WorkflowSchedule>(
    '/workflow-actions/schedules',
    data,
  )
  return response.data
}

export const updateSchedule = async (
  id: number,
  data: WorkflowSchedulePayload,
): Promise<WorkflowSchedule> => {
  const response = await api.put<WorkflowSchedule>(
    `/workflow-actions/schedules/${id}`,
    data,
  )
  return response.data
}

export const setScheduleEnabled = async (
  id: number,
  enabled: boolean,
): Promise<void> => {
  await api.patch(`/workflow-actions/schedules/${id}/enabled`, { enabled })
}

export const triggerSchedule = async (
  id: number,
): Promise<WorkflowActionResult> => {
  const response = await api.post<WorkflowActionResult>(
    `/workflow-actions/schedules/${id}/trigger`,
  )
  return response.data
}

export const getMySchedulePermission = async (): Promise<boolean> => {
  const response = await api.get<{ allowed: boolean }>(
    '/workflow-actions/schedule-permission',
  )
  return response.data.allowed
}
