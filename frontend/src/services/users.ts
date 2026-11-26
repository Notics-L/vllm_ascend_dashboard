import api from './api'

// ============ Types ============

export interface User {
  id: number
  username: string
  email: string | null
  role: string  // 'user' | 'admin' | 'super_admin'
  is_active: boolean
  created_at: string
}

export interface UserCreate {
  username: string
  email?: string
  password: string
  role?: string  // 'user' | 'admin' | 'super_admin'
}

export interface UserUpdate {
  username?: string
  email?: string
  role?: string
  is_active?: boolean
}

// ============ API Functions ============

/**
 * 获取用户列表（仅超级管理员）
 */
export const getUsers = async (): Promise<User[]> => {
  const response = await api.get<User[]>('/users')
  return response.data
}

/**
 * 获取用户详情
 */
export const getUser = async (userId: number): Promise<User> => {
  const response = await api.get<User>(`/users/${userId}`)
  return response.data
}

/**
 * 创建用户（仅超级管理员）
 */
export const createUser = async (data: UserCreate): Promise<User> => {
  const response = await api.post<User>('/users', data)
  return response.data
}

/**
 * 更新用户信息
 */
export const updateUser = async (userId: number, data: UserUpdate): Promise<User> => {
  const response = await api.put<User>(`/users/${userId}`, data)
  return response.data
}

/**
 * 删除用户（仅超级管理员）
 */
export const deleteUser = async (userId: number): Promise<{ message: string }> => {
  const response = await api.delete<{ message: string }>(`/users/${userId}`)
  return response.data
}

/**
 * 重置用户密码（管理员权限）
 */
export const resetUserPassword = async (userId: number, newPassword: string): Promise<{ message: string }> => {
  const response = await api.put<{ message: string }>(`/users/${userId}/password`, {
    new_password: newPassword,
  })
  return response.data
}

// ============ Import Types ============

export interface UserImportParseResult {
  columns: string[]
  rows: Record<string, string>[]
  total: number
  sheet_name: string
}

export interface UserImportRow {
  row_number: number
  username_raw: string
  email_raw: string
  password?: string
  role?: string
}

export interface UserImportRequest {
  rows: UserImportRow[]
  default_password: string
  default_role: string
  username_rules: {
    lowercase: boolean
    remove_spaces: boolean
    remove_special: boolean
  }
}

export interface UserImportPreviewRow {
  row_number: number
  source_username: string
  source_email: string
  username: string
  email: string
  password: string
  role: string
  status: 'ok' | 'renamed' | 'invalid' | 'update'
  renamed: boolean
  messages: string[]
}

export interface UserImportPreviewResult {
  rows: UserImportPreviewRow[]
  total: number
  valid_count: number
  renamed_count: number
  invalid_count: number
  updated_count: number
}

export interface UserImportExecuteResult {
  created: number
  updated: number
  skipped: number
  failed: number
  errors: string[]
}

/**
 * 解析 Excel 文件
 */
export const parseUsersImportFile = async (file: File): Promise<UserImportParseResult> => {
  const formData = new FormData()
  formData.append('file', file)
  const response = await api.post<UserImportParseResult>('/users/import/parse', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return response.data
}

/**
 * 预览导入结果
 */
export const previewUsersImport = async (data: UserImportRequest): Promise<UserImportPreviewResult> => {
  const response = await api.post<UserImportPreviewResult>('/users/import/preview', data)
  return response.data
}

/**
 * 执行批量导入
 */
export const executeUsersImport = async (data: UserImportRequest): Promise<UserImportExecuteResult> => {
  const response = await api.post<UserImportExecuteResult>('/users/import/execute', data)
  return response.data
}
