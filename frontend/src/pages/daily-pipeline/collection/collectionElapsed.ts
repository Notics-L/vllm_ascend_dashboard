import type { DailyCollectionRun } from '../../../types/dailyPipeline'

export function collectionDurationSeconds(
  run: Pick<DailyCollectionRun, 'startedAt' | 'endedAt'>,
): number | null {
  if (!run.endedAt) return null
  const start = Date.parse(run.startedAt)
  const end = Date.parse(run.endedAt)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return Math.floor((end - start) / 1000)
}
