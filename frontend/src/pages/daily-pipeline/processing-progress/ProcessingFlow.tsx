import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Badge, Descriptions, Drawer, Typography } from 'antd'
import type { DailyFailedCaseDetail } from '../../../types/dailyPipeline'
import { formatCollectionTime } from '../collection/collectionDisplay'
import { processingTimelinePaths } from './processingTimelinePaths'
import './ProcessingProgress.css'

const stageStatus = {
  pending: { label: '未开始', color: '#8c8c8c' },
  waiting: { label: '等待执行', color: '#1677ff' },
  running: { label: '进行中', color: '#1677ff' },
  completed: { label: '已完成', color: '#389e0d' },
  failed: { label: '处理失败', color: '#cf1322' },
}

function ProcessingFlow({ flow }: { flow: DailyFailedCaseDetail['processingFlow'] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const stages = flow.stages.filter((stage) => stage.status !== 'pending' || stage.id === flow.currentStageId)
  const selectedStage = stages.find((stage) => stage.id === selectedId)
  const timelineRef = useRef<HTMLDivElement>(null)
  const [connections, setConnections] = useState<string[]>([])
  useEffect(() => {
    const container = timelineRef.current
    if (!container) return
    const update = () => {
      const bounds = container.getBoundingClientRect()
      const points = [...container.querySelectorAll<HTMLLIElement>('.daily-processing-event')].flatMap((item) => {
        const dot = item.querySelector('.daily-processing-event-dot')
        if (!dot) return []
        const point = dot.getBoundingClientRect()
        const row = item.getBoundingClientRect()
        return [{ x: point.left + point.width / 2 - bounds.left, y: point.top + point.height / 2 - bounds.top,
          rowTop: row.top - bounds.top, rowBottom: row.bottom - bounds.top }]
      })
      const paths = processingTimelinePaths(points, bounds.width)
      setConnections((current) => current.join('|') === paths.join('|') ? current : paths)
    }
    update()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(container)
    container.querySelectorAll('.daily-processing-event').forEach((item) => observer?.observe(item))
    window.addEventListener('resize', update)
    return () => { observer?.disconnect(); window.removeEventListener('resize', update) }
  }, [flow.stages, flow.currentStageId])
  return <section className="daily-processing-flow" aria-label="自动处理进展">
    <Typography.Text className="daily-failure-section-title">处理进展</Typography.Text>
    <div className="daily-processing-timeline-layout" ref={timelineRef}>
    <svg className="daily-processing-timeline-connections" aria-hidden="true">
      {connections.map((path, index) => <path key={index} d={path} />)}
    </svg>
    <ol className="daily-processing-timeline" aria-label="处理事件" aria-live="polite">
      {stages.map((stage) => {
        const status = stageStatus[stage.status]
        const time = stage.records[0]?.createdAt ?? stage.updatedAt
        return <li key={stage.id}
          className={'daily-processing-event' + (stage.id === flow.currentStageId ? ' daily-processing-event-current' : '')}
          style={{ '--daily-stage-color': status.color } as CSSProperties}>
          <div className="daily-processing-event-point">
            <button type="button" className="daily-processing-event-trigger" aria-haspopup="dialog"
              aria-label={`${stage.name}：${formatCollectionTime(time)}，查看详情`}
              aria-expanded={selectedStage?.id === stage.id} onClick={() => setSelectedId(stage.id)}>
              <time className="daily-processing-event-time" dateTime={time ?? undefined}>{formatCollectionTime(time)}</time>
              <span className="daily-processing-event-dot" aria-hidden="true" />
            </button>
          </div>
          <div className="daily-processing-event-body">
            <div className="daily-processing-event-heading">
              <span>{stage.name}</span><Badge color={status.color} text={status.label} />
            </div>
          </div>
        </li>
      })}
    </ol>
    </div>
    <Drawer rootClassName="daily-processing-stage-drawer" title={selectedStage?.name} placement="right" width={520}
      styles={{ wrapper: { maxWidth: '100vw' } }} open={Boolean(selectedStage)} onClose={() => setSelectedId(null)} destroyOnHidden>
      {selectedStage && <Descriptions size="small" column={1} items={[
        { key: 'status', label: '状态', children: <Badge color={stageStatus[selectedStage.status].color} text={stageStatus[selectedStage.status].label} /> },
        { key: 'time', label: '更新时间', children: formatCollectionTime(selectedStage.updatedAt) },
        { key: 'result', label: selectedStage.id === 'locating' ? '定位结果' : '处理结果', children: selectedStage.result ?? '—' },
      ]} />}
    </Drawer>
  </section>
}

export default ProcessingFlow
