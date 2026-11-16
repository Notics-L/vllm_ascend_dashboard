import type { DailyFailedCase } from '../../../types/dailyPipeline'

export interface FailedCaseFilters {
  runId?: string
  name?: string
  hardware?: string[]
  group?: string[]
  problemCategory?: string[]
  collectedAt?: [string, string]
  issue?: string[]
  processingProgress?: string[]
  processingResult?: string[]
}

export const failedCaseIssueValue = (row: DailyFailedCase) => row.issue ? `#${row.issue.number}-${row.issue.state === 'open' ? 'open' : 'close'}` : '—'

export const failedCaseProcessingResult = (row: DailyFailedCase): '成功' | '失败' | '执行中' => row.processingProgress === '已解决' ? '成功' : row.processingProgress === '未解决' ? '失败' : '执行中'

export function filterFailedCases(data: DailyFailedCase[], filters: FailedCaseFilters): DailyFailedCase[] {
  const name = filters.name?.trim().toLowerCase()
  const runId = filters.runId?.trim()
  return data.filter((row) => {
    const collectedAt = row.collectedAt ? Date.parse(row.collectedAt) : NaN
    return (!name || row.name.toLowerCase().includes(name)) &&
      (!runId || String(row.runId).includes(runId)) &&
      (!filters.hardware?.length || filters.hardware.includes(row.hardware)) &&
      (!filters.group?.length || filters.group.includes(row.group)) &&
      (!filters.problemCategory?.length || filters.problemCategory.includes(row.problemCategory ?? '未分类')) &&
      (!filters.issue?.length || filters.issue.includes(failedCaseIssueValue(row))) &&
      (!filters.processingProgress?.length || filters.processingProgress.includes(row.processingProgress)) &&
      (!filters.processingResult?.length || filters.processingResult.includes(failedCaseProcessingResult(row))) &&
      (!filters.collectedAt?.length || (collectedAt >= Date.parse(filters.collectedAt[0]) && collectedAt <= Date.parse(filters.collectedAt[1])))
  })
}
