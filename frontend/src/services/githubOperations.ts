import api from './api'

export type GitHubOperationResult = 'pending' | 'success' | 'redirect' | 'failed'
export type GitHubOperationType = 'read' | 'write'
export type GitHubCredentialSource = 'global' | 'daily' | 'actions'

export interface GitHubOperation {
  id: string
  occurred_at: string
  process: string
  source: string
  credential_source: GitHubCredentialSource | null
  actor: string | null
  repository: string | null
  method: string
  operation_type: GitHubOperationType
  endpoint: string
  result: GitHubOperationResult
  http_status: number | null
  duration_ms: number
  error_type: string | null
  github_request_id: string | null
}

export interface GitHubOperationFilters {
  repository?: string
  source?: string
  actor?: string
  credential_source?: GitHubCredentialSource
  operation_type?: GitHubOperationType
  result?: GitHubOperationResult
  start?: string
  end?: string
}

export async function getGitHubOperations(page: number, pageSize: number, filters: GitHubOperationFilters) {
  const response = await api.get<{ items: GitHubOperation[]; total: number }>('/github-operations', {
    params: { page, page_size: pageSize, ...filters },
  })
  return response.data
}
