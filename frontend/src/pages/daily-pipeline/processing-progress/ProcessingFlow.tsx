import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Badge, Button, Descriptions, Drawer, List, Space, Spin, Typography } from 'antd'
import type { DailyFailedCaseDetail, DailyProcessingStage, DailyVersionFlow } from '../../../types/dailyPipeline'
import { formatCollectionTime } from '../collection/collectionDisplay'
import { processingTimelinePaths } from './processingTimelinePaths'
import ManualProcessingAction from './ManualProcessingAction'
import RollbackProcessingAction from './RollbackProcessingAction'
import VersionCompatibilityFlow from './VersionCompatibilityFlow'
import './ProcessingProgress.css'

const stageStatus = {
  ended: { label: '已结束', color: '#8c8c8c' },
  pending: { label: '未开始', color: '#8c8c8c' },
  reset: { label: '待重新处理', color: '#d48806' },
  waiting: { label: '等待执行', color: '#1677ff' },
  running: { label: '进行中', color: '#1677ff' },
  completed: { label: '已完成', color: '#389e0d' },
  failed: { label: '处理失败', color: '#cf1322' },
}

function versionStages(flow: DailyVersionFlow): DailyProcessingStage[] {
  const resolving = flow.status === 'waiting_resolution' || flow.status === 'waiting_pr'
  const buildStatus = {
    waiting_resolution: 'pending', waiting_pr: 'pending', waiting_build: 'waiting',
    building: 'running', build_failed: 'failed', resolved: 'completed',
  } as const
  return [
    { id: 'version-resolution', stageCode: 'version_resolution', name: '确认配套恢复',
      status: resolving ? flow.canEdit ? 'waiting' : 'ended' : 'completed',
      canRollback: flow.rollbackStages?.includes('version_resolution'),
      result: resolving ? flow.message : '已确认配套恢复', updatedAt: flow.updatedAt,
      records: flow.rollbackRecords?.version_resolution ?? [] },
    { id: 'version-build', stageCode: 'version_build', name: '镜像构建',
      status: !flow.canEdit && flow.status !== 'resolved' && !resolving ? 'ended' : buildStatus[flow.status],
      canRollback: flow.rollbackStages?.includes('version_build'),
      result: resolving ? null : flow.message, updatedAt: flow.updatedAt,
      records: flow.rollbackRecords?.version_build ?? [] },
  ]
}

function ProcessingFlow({ flow, allowManual = false, loading = false }: { flow: DailyFailedCaseDetail['processingFlow']; allowManual?: boolean; loading?: boolean }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const version = flow.versionFlow
  const currentId = flow.currentStageId ?? (version?.canEdit
    ? version.status === 'waiting_resolution' || version.status === 'waiting_pr' ? 'version-resolution' : 'version-build'
    : null)
  const stages = useMemo(() => [
    ...flow.stages.filter((stage) => stage.stageCode !== 'end'),
    ...(version ? versionStages(version) : []),
    ...flow.stages.filter((stage) => stage.stageCode === 'end'),
  ].filter((stage) => stage.status !== 'pending' || stage.id === currentId), [flow.stages, version, currentId])
  const selectedStage = stages.find((stage) => stage.id === selectedId)
    ?? flow.archivedStages?.find((stage) => stage.id === selectedId)
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
  }, [stages])
  return <section className="daily-processing-flow" aria-label="自动处理进展">
    <Typography.Text className="daily-failure-section-title">
      处理进展{(flow.sharedMemberCount ?? 0) > 1 && <Typography.Text type="secondary">（{flow.sharedMemberCount} 条用例共用）</Typography.Text>}
    </Typography.Text>
    <div className="daily-processing-timeline-layout" ref={timelineRef}>
    <svg className="daily-processing-timeline-connections" aria-hidden="true">
      {connections.map((path, index) => <path key={index} d={path} />)}
    </svg>
    <ol className="daily-processing-timeline" aria-label="处理事件" aria-live="polite">
      {stages.map((stage) => {
        const status = stageStatus[stage.status]
        const time = stage.startedAt ?? stage.updatedAt ?? stage.records[0]?.createdAt
        return <li key={stage.id}
          className={'daily-processing-event' + (stage.id === currentId ? ' daily-processing-event-current' : '')}
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
    {!!flow.archivedStages?.length && <details className="daily-processing-rollback-history">
      <summary>回退记录（{flow.archivedStages.length}）</summary>
      <Space wrap>
        {flow.archivedStages.map((stage) => <Button key={stage.id} type="link" size="small"
          onClick={() => setSelectedId(stage.id)}>
          {stage.name} · {formatCollectionTime(stage.updatedAt)}
        </Button>)}
      </Space>
    </details>}
    <Drawer rootClassName="daily-processing-stage-drawer" title={selectedStage?.name} placement="right" width={520}
      styles={{ wrapper: { maxWidth: '100vw' } }} open={Boolean(selectedStage)} onClose={() => setSelectedId(null)} destroyOnHidden>
      <Spin spinning={loading}>
      {selectedStage && <><Descriptions size="small" column={1} items={[
        { key: 'status', label: '状态', children: <Badge color={stageStatus[selectedStage.status].color} text={stageStatus[selectedStage.status].label} /> },
        { key: 'time', label: '更新时间', children: formatCollectionTime(selectedStage.updatedAt) },
        ...(selectedStage.result ? [{
          key: 'result',
          label: selectedStage.stageCode === 'classify' ? '分类结果' : (selectedStage.stageCode ?? selectedStage.id) === 'locating' ? '定位结果' : '处理结果',
          children: selectedStage.result,
        }] : []),
        ...(selectedStage.resultData?.category ? [{ key: 'category', label: '问题分类', children: ({ code: '代码问题', version: '版本配套问题', environment: '环境问题' })[selectedStage.resultData.category] }] : []),
        ...(selectedStage.resultData?.note ? [{ key: 'note', label: '备注说明', children: selectedStage.resultData.note }] : []),
        ...(selectedStage.resultData?.pr_number ? [{ key: 'pr', label: 'PR 编号', children: selectedStage.resultData.pr_url ? <a href={selectedStage.resultData.pr_url} target="_blank" rel="noopener noreferrer">#{selectedStage.resultData.pr_number}</a> : selectedStage.resultData.pr_number }] : []),
        ...(selectedStage.resultData?.original_pr_url ? [{ key: 'original-pr', label: '原问题 PR', children: <a href={selectedStage.resultData.original_pr_url} target="_blank" rel="noopener noreferrer">{selectedStage.resultData.original_pr_url}</a> }] : []),
        ...(selectedStage.resultData?.run_id ? [{ key: 'run', label: '构建 Run ID', children: selectedStage.resultData.run_id }] : []),
      ]} />
        {(selectedStage.stageCode === 'version_resolution' || selectedStage.stageCode === 'version_build') && version
          ? <VersionCompatibilityFlow flow={version} allowManual={allowManual && selectedStage.id === currentId} />
          : allowManual && <ManualProcessingAction key={selectedStage.id} stage={selectedStage} />}
        {allowManual && selectedStage.canRollback && <RollbackProcessingAction
          key={selectedStage.id} stage={selectedStage} failureId={version?.failureId}
          sharedMemberCount={selectedStage.stageCode === 'failure_notification' ? 0 : version?.memberCount ?? flow.sharedMemberCount ?? 0}
          onRolledBack={() => setSelectedId(null)} />}
        {selectedStage.records.length > 0 && <div className="daily-processing-execution-records">
          <Typography.Text className="daily-failure-section-title">执行记录</Typography.Text>
          <List size="small" dataSource={selectedStage.records} renderItem={(record) => <List.Item key={record.id}>
            <div>
              <div className="daily-processing-record-heading">
                <span>{record.source === 'manual' ? '人工' : '自动'} · {record.author}</span>
                {record.status && <Badge color={stageStatus[record.status].color} text={stageStatus[record.status].label} />}
              </div>
              <time>{formatCollectionTime(record.createdAt)}</time>
              {record.content && <div className="daily-processing-record-content">{record.content}</div>}
              {record.resultData?.note && <div className="daily-processing-record-content">{record.resultData.note}</div>}
              {record.resultData?.pr_number && <div>PR 编号：{record.resultData.pr_number}</div>}
              {record.resultData?.run_id && <div>构建 Run ID：{record.resultData.run_id}</div>}
            </div>
          </List.Item>} />
        </div>}
      </>}
      </Spin>
    </Drawer>
  </section>
}

export default ProcessingFlow
