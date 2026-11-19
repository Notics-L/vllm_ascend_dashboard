import type { DailyCase } from '../../../types/dailyPipeline'

export interface DailyCaseFilters {
  name?: string
  hardware?: string[]
  testType?: string[]
  tags?: string[]
  status?: DailyCase['status'][]
}

type FilterableCase = Pick<DailyCase, 'name' | 'hardware' | 'testType' | 'tags' | 'status'>

export function filterDailyCases<T extends FilterableCase>(cases: T[], filters: DailyCaseFilters): T[] {
  const keyword = filters.name?.trim().toLowerCase()
  return cases.filter((item) => (
    (!keyword || item.name.toLowerCase().includes(keyword)) &&
    (!filters.hardware?.length || filters.hardware.includes(item.hardware)) &&
    (!filters.testType?.length || filters.testType.includes(item.testType)) &&
    (!filters.status?.length || filters.status.includes(item.status)) &&
    (!filters.tags?.length || filters.tags.some((tag) => item.tags.includes(tag)))
  ))
}
