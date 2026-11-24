import type { DailyCollectionDetail, DailyExecutionResult } from '../../../types/dailyPipeline'

export interface RunAllResultRow {
  id: string
  githubUrl?: string | null
  name: string
  group: 'UT' | 'E2E' | 'UT/E2E'
  hardware: string
  vllmVersion?: 'main' | 'release'
  result: DailyExecutionResult
}

export function runAllResultRows(data?: Pick<DailyCollectionDetail, 'cases' | 'jobs'>): RunAllResultRow[] {
  return data?.cases.flatMap<RunAllResultRow>((item) => item.group === 'Daily'
    ? []
    : [{ ...item, group: item.group }]) ?? []
}
