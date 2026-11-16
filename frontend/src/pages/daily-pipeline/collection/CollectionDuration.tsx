import type { DailyCollectionRun } from '../../../types/dailyPipeline'
import { formatDuration } from '../../../utils/ciRenderers'
import { collectionDurationSeconds } from './collectionElapsed'

function CollectionDuration({ run }: { run: DailyCollectionRun }) {
  const seconds = collectionDurationSeconds(run)
  return <span>{seconds === null ? '—' : seconds === 0 ? '0s' : formatDuration(seconds)}</span>
}

export default CollectionDuration
