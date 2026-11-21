import { useMemo } from 'react'
import { Card, Empty } from 'antd'
import type { DailyOverviewData } from '../../../types/dailyPipeline'
import { formatDuration } from '../../../utils/ciRenderers'
import { collectionDurationSeconds } from '../collection/collectionElapsed'
import OverviewChart from './OverviewChart'
import { overviewRunDurations } from './overviewCharts'

const durationText = (seconds: number) => seconds === 0 ? '0s' : formatDuration(seconds)

export default function RunDurationCard({ runs }: { runs: DailyOverviewData['recentRuns'] }) {
  const samples = useMemo(() => runs.flatMap((run) => {
    const seconds = collectionDurationSeconds(run)
    return seconds === null ? [] : [{ id: run.id, seconds }]
  }), [runs])
  const chart = useMemo(() => overviewRunDurations(samples, durationText), [samples])
  const count = samples.length
  const average = count ? Math.round(samples.reduce((sum, item) => sum + item.seconds, 0) / count) : 0
  const longest = count ? Math.max(...samples.map((item) => item.seconds)) : 0

  return <Card title="流水线耗时对比" className="daily-overview-duration" bordered={false}
    extra={count > 0 && <span className="daily-overview-duration-count">{count} 次执行</span>}>
    {count ? <div className="daily-overview-duration-layout">
      <OverviewChart option={chart} label="按 Run ID 对比流水线耗时" height={Math.max(144, 42 * count + 32)} />
      <dl className="daily-overview-duration-summary">
        <div><dt>平均耗时</dt><dd>{durationText(average)}</dd></div>
        <div><dt>最长耗时</dt><dd>{durationText(longest)}</dd></div>
      </dl>
    </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已结束的执行记录" />}
  </Card>
}
