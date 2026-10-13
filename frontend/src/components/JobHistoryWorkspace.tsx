import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Checkbox, Collapse, Empty, Modal, Segmented, Space, Spin, Table, Tag, Typography, message } from 'antd'
import { CopyOutlined, GithubOutlined, RobotOutlined } from '@ant-design/icons'
import api from '../services/api'
import { createJobLogRootCause, getJobComparison, getJobLogRootCause, getJobPerformanceTrends, refreshRunVersionSnapshot } from '../services/ci'
import type { CIJob, CIJobPerformanceMetric } from '../services/ci'
import { formatDuration } from '../utils/ciRenderers'
import { formatTimezone } from '../utils/timezone'
import { Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const { Text, Link } = Typography

function TimelineNode({
  record, currentJobId, selectedAsStart, selectedAsEnd, onStart, onEnd, onOpenSummary,
}: {
  record: CIJob
  currentJobId: number
  selectedAsStart: boolean
  selectedAsEnd: boolean
  onStart: () => void
  onEnd: () => void
  onOpenSummary?: (jobId: number) => void
}) {
  const [hovered, setHovered] = useState(false)
  const [linkHovered, setLinkHovered] = useState(false)
  const failedStep = record.steps_summary?.find(step => ['failure', 'timed_out', 'cancelled'].includes(step.conclusion || ''))?.name
  const nodeColor = record.conclusion === 'success' ? '#237a45' : record.conclusion === 'failure' ? '#b8323c' : '#595959'
  const statusText = record.conclusion === 'success' ? '成功' : record.conclusion === 'failure' ? '失败' : record.conclusion || '未知'

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 190,
        flex: '0 0 190px',
        position: 'relative',
        textAlign: 'center',
        zIndex: 2,
      }}
    >
      <div style={{ height: 28, color: '#595959', fontSize: 12 }}>{record.started_at ? formatTimezone(record.started_at, 'MM-DD') : '时间未知'}</div>
      <div style={{ height: 50, display: 'flex', justifyContent: 'center' }}>
        <a
          href={record.github_job_url}
          target="_blank"
          rel="noopener noreferrer"
          title="打开 GitHub Job 日志"
          onMouseEnter={() => setLinkHovered(true)}
          onMouseLeave={() => setLinkHovered(false)}
          style={{
            width: 76,
            height: 36,
            lineHeight: '32px',
            background: nodeColor,
            color: '#fff',
            border: `2px solid ${nodeColor}`,
            borderRadius: 2,
            fontWeight: 700,
            fontSize: 13,
            zIndex: 1,
            textDecoration: 'none',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: linkHovered ? 5 : 0,
            transition: 'gap 160ms ease, transform 160ms ease, filter 160ms ease',
            transform: linkHovered ? 'translateY(-2px)' : 'none',
            filter: linkHovered ? 'brightness(1.08)' : 'none',
          }}
        >
          <GithubOutlined style={{ width: linkHovered ? 15 : 0, opacity: linkHovered ? 1 : 0, overflow: 'hidden', transition: 'width 160ms ease, opacity 120ms ease' }} />
          <span>{statusText}</span>
        </a>
      </div>
      <div style={{ fontSize: 12, fontWeight: 600 }}>{record.started_at ? formatTimezone(record.started_at, 'HH:mm') : '--:--'}</div>
      <div style={{ color: '#595959', fontSize: 12, marginTop: 2 }}>{formatDuration(record.duration_seconds)}</div>
      <div style={{ height: 17, marginTop: 2, color: '#595959', fontSize: 10 }}>
        {record.vllm_ascend_commit_date ? `版本 ${formatTimezone(record.vllm_ascend_commit_date, 'MM-DD HH:mm')}` : '版本时间未知'}
      </div>
      <div title={failedStep || undefined} style={{ height: 36, marginTop: 3, padding: '0 5px', color: record.conclusion === 'success' ? '#8c8c8c' : '#a61d24', fontSize: 11, lineHeight: '17px', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
        {record.conclusion === 'success' ? '—' : `失败阶段：${failedStep || '未识别'}`}
      </div>
      <div style={{ height: 24 }}>
        {record.conclusion !== 'success' && onOpenSummary && (
          <Button type="link" size="small" icon={<RobotOutlined />} onClick={() => onOpenSummary(record.job_id)} style={{ height: 22, padding: 0, fontSize: 12 }}>
            日志根因摘要
          </Button>
        )}
      </div>
      {record.job_id === currentJobId && <Tag style={{ margin: '4px 0 0' }}>当前</Tag>}
      <div style={{ height: 30, marginTop: 6, overflow: 'hidden', position: 'relative' }}>
        {(selectedAsStart || selectedAsEnd) && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#17133f', fontSize: 13, fontWeight: 700, opacity: hovered ? 0 : 1, transform: hovered ? 'scale(0.88)' : 'scale(1)', transition: hovered ? 'opacity 120ms ease, transform 180ms ease' : 'opacity 360ms ease, transform 460ms cubic-bezier(0.22, 1, 0.36, 1)' }}>
            {selectedAsStart ? 'Start' : 'End'}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'center', gap: 4 }}>
          <Button size="small" type={selectedAsStart ? 'primary' : 'default'} style={{ borderRadius: 2, paddingInline: 7, opacity: hovered ? 1 : 0, transform: hovered ? 'translateX(0)' : 'translateX(44px)', transition: hovered ? 'opacity 140ms ease-out, transform 220ms cubic-bezier(0.34, 1.56, 0.64, 1)' : 'opacity 360ms ease-in, transform 480ms cubic-bezier(0.22, 1, 0.36, 1)', pointerEvents: hovered ? 'auto' : 'none' }} onClick={onStart}>设为 Start</Button>
          <Button size="small" type={selectedAsEnd ? 'primary' : 'default'} style={{ borderRadius: 2, paddingInline: 7, opacity: hovered ? 1 : 0, transform: hovered ? 'translateX(0)' : 'translateX(-44px)', transition: hovered ? 'opacity 140ms ease-out, transform 220ms cubic-bezier(0.34, 1.56, 0.64, 1)' : 'opacity 360ms ease-in, transform 480ms cubic-bezier(0.22, 1, 0.36, 1)', pointerEvents: hovered ? 'auto' : 'none' }} onClick={onEnd}>设为 End</Button>
        </div>
      </div>
    </div>
  )
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return <Text type="secondary">未知</Text>
  if (Array.isArray(value)) return value.length ? value.join(', ') : <Text type="secondary">空</Text>
  return String(value)
}

function PerformanceAccuracyView({ timeline, loading }: { timeline: CIJob[]; loading: boolean }) {
  const recentJobIds = timeline.slice(-30).map(item => item.job_id)
  const [visibleMetrics, setVisibleMetrics] = useState<Array<CIJobPerformanceMetric['metric_type']>>(['ott', 'accuracy'])
  const trends = useQuery({
    queryKey: ['job-performance-trends', recentJobIds],
    queryFn: () => getJobPerformanceTrends(recentJobIds),
    enabled: recentJobIds.length > 0,
    staleTime: 5 * 60 * 1000,
  })
  const items = [...(trends.data?.items || [])]
    .sort((left, right) => new Date(left.started_at || 0).getTime() - new Date(right.started_at || 0).getTime())
  const chartPoints = [...items.reduce((points, item) => {
    const key = String(item.job_id)
    const point = points.get(key) || {
      jobId: item.job_id,
      timestamp: item.started_at ? formatTimezone(item.started_at, 'MM-DD HH:mm') : '未知时间',
      fullTimestamp: item.started_at ? formatTimezone(item.started_at, 'YYYY-MM-DD HH:mm:ss') : '未知时间',
      startedAt: item.started_at || '',
      ottValue: null as number | null,
      ottBaseline: null as number | null,
      ottThreshold: null as number | null,
      accuracyValue: null as number | null,
      accuracyBaseline: null as number | null,
      accuracyThresholdLower: null as number | null,
      accuracyThresholdUpper: null as number | null,
      ott: undefined as CIJobPerformanceMetric | undefined,
      accuracy: undefined as CIJobPerformanceMetric | undefined,
    }
    if (item.metric_type === 'ott') {
      point.ott = item
      point.ottValue = item.value
      point.ottBaseline = item.baseline
      point.ottThreshold = item.baseline !== null && item.threshold_ratio !== null && item.threshold_ratio !== undefined
        ? item.baseline * item.threshold_ratio : null
    } else {
      point.accuracy = item
      point.accuracyValue = item.value
      point.accuracyBaseline = item.baseline
      if (item.baseline !== null && item.threshold_tolerance !== null && item.threshold_tolerance !== undefined) {
        point.accuracyThresholdLower = item.baseline - item.threshold_tolerance
        point.accuracyThresholdUpper = item.baseline + item.threshold_tolerance
      } else if (item.baseline !== null && item.threshold_ratio !== null && item.threshold_ratio !== undefined) {
        point.accuracyThresholdLower = item.baseline * item.threshold_ratio
      }
    }
    points.set(key, point)
    return points
  }, new Map<string, {
    jobId: number
    timestamp: string
    fullTimestamp: string
    startedAt: string
    ottValue: number | null
    ottBaseline: number | null
    ottThreshold: number | null
    accuracyValue: number | null
    accuracyBaseline: number | null
    accuracyThresholdLower: number | null
    accuracyThresholdUpper: number | null
    ott?: CIJobPerformanceMetric
    accuracy?: CIJobPerformanceMetric
  }>()).values()].sort((left, right) => new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime())
  const allMetricsVisible = visibleMetrics.length === 2
  const xAxisInterval = Math.max(0, Math.ceil(chartPoints.length / 9) - 1)
  const commonChartValue = (field: 'ottThreshold' | 'accuracyThresholdLower' | 'accuracyThresholdUpper') => {
    const values = chartPoints.map(point => point[field]).filter((value): value is number => value !== null)
    return values.length && values.every(value => Math.abs(value - values[0]) < 0.000001) ? values[0] : null
  }
  const ottThreshold = commonChartValue('ottThreshold')
  const accuracyThresholdLower = commonChartValue('accuracyThresholdLower')
  const accuracyThresholdUpper = commonChartValue('accuracyThresholdUpper')
  const baselineExtent = (field: 'ottBaseline' | 'accuracyBaseline') => {
    const points = chartPoints.filter(point => point[field] !== null)
    return points.length >= 2 ? { start: points[0].timestamp, end: points[points.length - 1].timestamp } : null
  }
  const ottBaselineExtent = baselineExtent('ottBaseline')
  const accuracyBaselineExtent = baselineExtent('accuracyBaseline')
  const maxAccuracyValue = Math.max(
    0,
    ...chartPoints.flatMap(point => [
      point.accuracyValue,
      point.accuracyBaseline,
      point.accuracyThresholdLower,
      point.accuracyThresholdUpper,
    ]).filter((value): value is number => value !== null),
  )
  // Accuracy gates usually sit close to 100 while failed actual values can be
  // close to zero. Compress the unused middle range so the two gates remain
  // visually distinguishable without changing the values shown in the axis or tooltip.
  const useAccuracyAxisBreak = maxAccuracyValue >= 85
  const scaleAccuracy = (value: number | null) => {
    if (value === null || !useAccuracyAxisBreak) return value
    return value <= 80 ? value * 0.5 : 40 + (value - 80) * 3
  }
  const accuracyChartMax = Math.max(
    0,
    ...chartPoints.flatMap(point => [
      scaleAccuracy(point.accuracyValue),
      scaleAccuracy(point.accuracyBaseline),
      scaleAccuracy(point.accuracyThresholdLower),
      scaleAccuracy(point.accuracyThresholdUpper),
    ]).filter((value): value is number => value !== null),
  )
  const accuracyAxisMax = useAccuracyAxisBreak
    ? Math.max(100, (Math.ceil(accuracyChartMax / 10) + 1) * 10)
    : Math.max(25, (Math.ceil(maxAccuracyValue / 25) + 1) * 25)
  const accuracyAxisTicks = useAccuracyAxisBreak ? [0, 20, 40, 70, 100].filter(value => value <= accuracyAxisMax) : undefined
  const formatAccuracyTick = (value: number) => {
    const originalValue = useAccuracyAxisBreak ? (value <= 40 ? value * 2 : 80 + (value - 40) / 3) : value
    return String(Number(originalValue.toFixed(0)))
  }
  const chartData = chartPoints.map(point => ({
    ...point,
    accuracyChartValue: scaleAccuracy(point.accuracyValue),
    accuracyChartBaseline: scaleAccuracy(point.accuracyBaseline),
    accuracyChartThresholdLower: scaleAccuracy(point.accuracyThresholdLower),
    accuracyChartThresholdUpper: scaleAccuracy(point.accuracyThresholdUpper),
  }))
  const legendPayload = [
    ...(visibleMetrics.includes('ott') ? [
      { value: 'OTT Actual', type: 'line' as const, color: '#1677ff' },
      { value: 'OTT Baseline', type: 'line' as const, color: '#91caff' },
      { value: 'OTT Gate', type: 'line' as const, color: '#bae0ff' },
    ] : []),
    ...(visibleMetrics.includes('accuracy') ? [
      { value: 'Accuracy Actual', type: 'line' as const, color: '#722ed1' },
      { value: 'Accuracy Baseline', type: 'line' as const, color: '#d3adf7' },
      { value: 'Accuracy Gate Lower', type: 'line' as const, color: '#efdbff' },
      { value: 'Accuracy Gate Upper', type: 'line' as const, color: '#efdbff' },
    ] : []),
  ]
  const formatValue = (value: number, unit: string) => `${Number(value.toFixed(2))}${unit ? ` ${unit}` : ''}`
  const metricDetails = (title: string, item: CIJobPerformanceMetric | undefined) => {
    if (!item || item.value === null) return <div style={{ marginTop: 10 }}><Text strong>{title}</Text><div>No valid result</div>{item?.reason && <Text type="secondary">Reason: {item.reason}</Text>}</div>
    const ratio = item.threshold_ratio
    const tolerance = item.threshold_tolerance
    const effectiveThreshold = ratio !== null && ratio !== undefined && item.baseline !== null ? item.baseline * ratio : null
    const passed = effectiveThreshold !== null ? item.value >= effectiveThreshold
      : tolerance !== null && tolerance !== undefined && item.baseline !== null ? Math.abs(item.value - item.baseline) <= tolerance
        : item.conclusion === 'success'
    return <div style={{ marginTop: 10 }}>
      <Text strong>{title}</Text>
      <div style={{ display: 'grid', gridTemplateColumns: '92px 1fr', columnGap: 10, rowGap: 3, marginTop: 4 }}>
        <span>Actual</span><span>{formatValue(item.value, item.unit)}</span>
        <span>Baseline</span><span>{item.baseline === null ? 'N/A' : formatValue(item.baseline, item.unit)}</span>
        {ratio !== null && ratio !== undefined && <><span>Threshold</span><span>{ratio * 100}%{effectiveThreshold !== null && item.baseline !== null ? ` · ${ratio} × ${item.baseline} = ${formatValue(effectiveThreshold, item.unit)}` : ''}</span></>}
        {tolerance !== null && tolerance !== undefined && <><span>Threshold</span><span>±{tolerance}{item.baseline !== null ? ` · ${formatValue(item.baseline - tolerance, item.unit)} – ${formatValue(item.baseline + tolerance, item.unit)}` : ''}</span></>}
        <span>Result</span><span style={{ color: passed ? '#389e0d' : '#cf1322' }}>{passed ? 'Passed' : 'Failed'}</span>
      </div>
    </div>
  }
  const setMetricVisible = (metricType: CIJobPerformanceMetric['metric_type'], checked: boolean) => {
    setVisibleMetrics(current => checked
      ? [...new Set([...current, metricType])]
      : current.filter(value => value !== metricType))
  }

  if (loading || trends.isLoading) return <div className="job-history-view-enter" style={{ padding: 40, textAlign: 'center' }}><Spin tip="正在读取 Run Pytest 与 Stream logs" /></div>
  if (trends.isError) return <Alert className="job-history-view-enter" type="error" showIcon message="性能精度数据加载失败" description="请检查 GitHub Token 或稍后重试。" />
  return <div className="job-history-view-enter" style={{ marginTop: 12 }}>
    {chartPoints.length === 0 ? <Empty description="最近 30 次运行中未识别到 OTT 或 Accuracy" /> : <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '4px 0 8px' }}>
        <Text strong style={{ fontSize: 16 }}>性能精度趋势</Text>
        <Space size={12}>
          <Checkbox checked={allMetricsVisible} onChange={event => setVisibleMetrics(event.target.checked ? ['ott', 'accuracy'] : [])}>全部</Checkbox>
          <Checkbox checked={visibleMetrics.includes('ott')} onChange={event => setMetricVisible('ott', event.target.checked)}>OTT</Checkbox>
          <Checkbox checked={visibleMetrics.includes('accuracy')} onChange={event => setMetricVisible('accuracy', event.target.checked)}>Accuracy</Checkbox>
        </Space>
      </div>
      <div style={{ width: '100%', height: 460, padding: '4px 18px 0 10px', boxSizing: 'border-box' }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 24, right: 68, left: 56, bottom: 72 }}>
            <XAxis dataKey="timestamp" interval={xAxisInterval} angle={-30} textAnchor="end" height={62} tickMargin={12} />
            <YAxis yAxisId="ott" width={84} label={{ value: 'token/s', angle: -90, position: 'insideLeft', offset: -22 }} />
            <YAxis yAxisId="accuracy" orientation="right" width={68} domain={[0, accuracyAxisMax]} ticks={accuracyAxisTicks} tickFormatter={formatAccuracyTick} tickCount={6} label={{ value: '%', angle: 90, position: 'insideRight', offset: -18 }} />
            <Tooltip content={({ active, payload }) => {
              const point = payload?.[0]?.payload as (typeof chartPoints)[number] | undefined
              if (!active || !point) return null
              return <div style={{ width: 400, maxWidth: 'calc(100vw - 32px)', padding: '12px 14px', background: '#fff', border: '1px solid #d9d9d9', borderRadius: 6, boxShadow: '0 3px 10px rgba(0,0,0,.12)' }}>
                <Text strong>{point.fullTimestamp}</Text>
                {metricDetails('Output Token Throughput', point.ott)}
                {metricDetails('Accuracy', point.accuracy)}
              </div>
            }} />
            <Legend payload={legendPayload} />
            {visibleMetrics.includes('ott') && <>
              <Line yAxisId="ott" type="monotone" dataKey="ottValue" name="OTT Actual" stroke="#1677ff" strokeWidth={2.5} connectNulls dot={{ r: 4 }} activeDot={{ r: 5 }} />
              <Line yAxisId="ott" type="monotone" dataKey="ottBaseline" name="OTT Baseline" stroke="#91caff" strokeDasharray="7 5" strokeWidth={1.5} connectNulls dot={false} />
              {ottThreshold !== null && ottBaselineExtent
                ? <ReferenceLine yAxisId="ott" segment={[{ x: ottBaselineExtent.start, y: ottThreshold }, { x: ottBaselineExtent.end, y: ottThreshold }]} stroke="#bae0ff" strokeDasharray="2 5" strokeWidth={1.25} />
                : <Line yAxisId="ott" type="monotone" dataKey="ottThreshold" name="OTT Threshold" stroke="#bae0ff" strokeDasharray="2 5" strokeWidth={1.25} connectNulls dot={false} />}
            </>}
            {visibleMetrics.includes('accuracy') && <>
              <Line yAxisId="accuracy" type="monotone" dataKey="accuracyChartValue" name="Accuracy Actual" stroke="#722ed1" strokeWidth={2.5} connectNulls={false} dot={{ r: 4 }} activeDot={{ r: 5 }} />
              <Line yAxisId="accuracy" type="monotone" dataKey="accuracyChartBaseline" name="Accuracy Baseline" stroke="#d3adf7" strokeDasharray="7 5" strokeWidth={1.5} connectNulls dot={false} />
              {accuracyThresholdLower !== null && accuracyBaselineExtent
                ? <ReferenceLine yAxisId="accuracy" segment={[{ x: accuracyBaselineExtent.start, y: scaleAccuracy(accuracyThresholdLower)! }, { x: accuracyBaselineExtent.end, y: scaleAccuracy(accuracyThresholdLower)! }]} stroke="#efdbff" strokeDasharray="2 5" strokeWidth={1.25} />
                : <Line yAxisId="accuracy" type="monotone" dataKey="accuracyChartThresholdLower" name="Accuracy Threshold" stroke="#efdbff" strokeDasharray="2 5" strokeWidth={1.25} connectNulls dot={false} />}
              {accuracyThresholdUpper !== null && accuracyBaselineExtent
                ? <ReferenceLine yAxisId="accuracy" segment={[{ x: accuracyBaselineExtent.start, y: scaleAccuracy(accuracyThresholdUpper)! }, { x: accuracyBaselineExtent.end, y: scaleAccuracy(accuracyThresholdUpper)! }]} stroke="#efdbff" strokeDasharray="2 5" strokeWidth={1.25} />
                : <Line yAxisId="accuracy" type="monotone" dataKey="accuracyChartThresholdUpper" name="Accuracy Threshold Upper" stroke="#efdbff" strokeDasharray="2 5" strokeWidth={1.25} connectNulls dot={false} />}
            </>}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>}
    {(trends.data?.unavailable.length || 0) > 0 && <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{trends.data?.unavailable.length} 个 Job 无可用指标（日志过期、不可访问或未包含指标）。</Text>}
  </div>
}

export function JobHistoryWorkspace({ job, workflowName }: { job: CIJob; workflowName?: string }) {
  const [startJobId, setStartJobId] = useState<number | null>(null)
  const [endJobId, setEndJobId] = useState<number | null>(null)
  const [summaryJobId, setSummaryJobId] = useState<number | null>(null)
  const [historyView, setHistoryView] = useState<'overview' | 'performance'>('overview')
  const attemptedVersionRuns = useRef(new Set<number>())
  const timelineViewportRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const storedSummary = useQuery({
    queryKey: ['job-log-root-cause', summaryJobId],
    queryFn: () => getJobLogRootCause(summaryJobId!),
    enabled: Boolean(summaryJobId),
  })
  const createSummary = useMutation({
    mutationFn: () => createJobLogRootCause(
      summaryJobId!,
      storedSummary.data?.status === 'completed',
    ),
    onSuccess: data => {
      queryClient.setQueryData(['job-log-root-cause', summaryJobId], data)
      if (data.status === 'completed') message.success('日志根因摘要已生成并永久保存')
      else message.error(data.error_message || '摘要生成失败')
    },
    onError: (error: any) => message.error(error?.response?.data?.detail || '摘要生成失败'),
  })
  const canonicalWorkflowName = workflowName || job.workflow_name
  const { data = [], isLoading, isError } = useQuery<CIJob[]>({
    queryKey: ['inline-job-history', canonicalWorkflowName, job.job_name],
    queryFn: async () => (await api.get<CIJob[]>('/job-owners/jobs/runs', {
      params: { workflow_name: canonicalWorkflowName, job_name: job.job_name, limit: 500, official_only: true },
    })).data,
  })

  useEffect(() => {
    const runIds = [...new Set(
      data
        .filter(record => !record.vllm_ascend_commit && !attemptedVersionRuns.current.has(record.run_id))
        .map(record => record.run_id),
    )]
    if (runIds.length === 0) return
    runIds.forEach(runId => attemptedVersionRuns.current.add(runId))
    let cancelled = false
    void (async () => {
      let collected = false
      for (const runId of runIds) {
        if (cancelled) break
        try {
          await refreshRunVersionSnapshot(runId)
          collected = true
        } catch {
          // Missing/expired logs are represented as "版本未知"; do not turn
          // automatic evidence collection into a page-level error.
        }
      }
      if (!cancelled && collected) {
        await queryClient.invalidateQueries({ queryKey: ['inline-job-history', job.workflow_name, job.job_name] })
        await queryClient.invalidateQueries({ queryKey: ['job-comparison'] })
      }
    })()
    return () => { cancelled = true }
  }, [data, job.job_name, job.workflow_name, queryClient])

  const start = data.find(item => item.job_id === startJobId)
  const end = data.find(item => item.job_id === endJobId)
  const timeline = [...data].sort((a, b) => new Date(a.started_at || 0).getTime() - new Date(b.started_at || 0).getTime())
  const startIndex = timeline.findIndex(record => record.job_id === startJobId)
  const endIndex = timeline.findIndex(record => record.job_id === endJobId)
  const rangeStartIndex = startIndex >= 0 && endIndex >= 0 ? Math.min(startIndex, endIndex) : -1
  const rangeEndIndex = startIndex >= 0 && endIndex >= 0 ? Math.max(startIndex, endIndex) : -1
  const hasSelectedRange = rangeStartIndex >= 0 && rangeEndIndex >= 0

  useEffect(() => {
    const viewport = timelineViewportRef.current
    if (!viewport || timeline.length === 0) return
    const frame = window.requestAnimationFrame(() => {
      viewport.scrollLeft = viewport.scrollWidth
    })
    return () => window.cancelAnimationFrame(frame)
  }, [job.job_id, timeline.length])
  const intervalInvalid = Boolean(start?.started_at && end?.started_at && new Date(start.started_at) >= new Date(end.started_at))
  const canCompare = Boolean(startJobId && endJobId && !intervalInvalid)
  const comparison = useQuery({
    queryKey: ['job-comparison', startJobId, endJobId],
    queryFn: () => getJobComparison(startJobId!, endJobId!),
    enabled: canCompare,
  })
  const intervalRecords = canCompare && start?.started_at && end?.started_at
    ? timeline.filter(record => {
        const timestamp = new Date(record.started_at || 0).getTime()
        return timestamp >= new Date(start.started_at!).getTime()
          && timestamp <= new Date(end.started_at!).getTime()
      })
    : []
  const intervalLogSummaries = useQuery({
    queryKey: ['interval-log-summaries', startJobId, endJobId, intervalRecords.map(item => item.job_id)],
    queryFn: async () => {
      const summaries = []
      // Fetch sequentially to avoid a burst of GitHub log-download requests.
      for (const record of intervalRecords) {
        if (record.conclusion === 'success') {
          summaries.push({ record, summary: '运行成功，未发现失败日志。', source: 'status' })
          continue
        }
        try {
          const evidence = await getJobLogRootCause(record.job_id)
          summaries.push({
            record,
            summary: evidence?.status === 'completed' && evidence.summary
              ? evidence.summary
              : '尚未生成日志根因摘要，请点击该节点的“日志根因摘要”生成。',
            source: evidence?.status === 'completed'
              ? `${evidence.llm_provider || 'LLM'}/${evidence.llm_model || '未知模型'}`
              : '未生成',
          })
        } catch {
          summaries.push({ record, summary: '日志不可用或已过期', source: 'unavailable' })
        }
      }
      return summaries
    },
    enabled: canCompare && intervalRecords.length > 0,
  })

  const copyComparisonEvidence = async () => {
    if (!comparison.data || !start || !end) return
    const evidence = comparison.data
    const versionLine = (label: string, side: 'start' | 'end') => {
      const snapshot = evidence[side]
      const sha = snapshot.vllm_ascend_commit || '未知'
      return `- ${label}: ${sha}${snapshot.vllm_ascend_commit_date ? ` (${snapshot.vllm_ascend_commit_date})` : ''}${snapshot.vllm_ascend_commit ? `\n  https://github.com/vllm-project/vllm-ascend/commit/${snapshot.vllm_ascend_commit}` : ''}`
    }
    const prLines = [...evidence.pr_changes.items]
      .sort((a, b) => new Date(a.merged_at || 0).getTime() - new Date(b.merged_at || 0).getTime())
      .map(item => `- ${item.merged_at ? formatTimezone(item.merged_at) : '合入时间未知'} | ${item.number ? `PR #${item.number}` : item.sha?.slice(0, 7)} | ${item.title}${item.author ? ` | ${item.author}` : ''}${item.url ? `\n  ${item.url}` : ''}`)
    const revertLines = (evidence.pr_changes.revert_commits || []).map(item =>
      `- ${item.revert_pr ? `PR #${item.revert_pr}` : '回退提交'} 回退 PR #${item.reverted_pr} | ${item.cancellation_status === 'cancelled' ? '双方均在区间内，已抵消' : '仅单方在区间内，不抵消'}${item.url ? `\n  ${item.url}` : ''}`,
    )
    const linkLines = intervalRecords.map(record => `- ${formatTimezone(record.started_at)} | ${record.conclusion || '未知'} | ${record.github_job_url || '无链接'}`)
    const logLines = (intervalLogSummaries.data || []).map(({ record, summary, source }) => `- ${formatTimezone(record.started_at)} | ${record.conclusion || '未知'} | ${source}\n  ${summary}`)
    const text = [
      `故障定位证据：${job.job_name}`,
      `区间：${formatTimezone(start.started_at)} -> ${formatTimezone(end.started_at)}`,
      '',
      '【区间链接】',
      ...(linkLines.length ? linkLines : ['- 无']),
      '',
      '【vllm-ascend 版本】',
      versionLine('Start', 'start'),
      versionLine('End', 'end'),
      '',
      '【PR 合入时间线】',
      ...(prLines.length ? prLines : ['- 区间内无有效 PR']),
      evidence.pr_changes.compare_url ? `- GitHub commit 对比：${evidence.pr_changes.compare_url}` : '',
      ...(revertLines.length ? ['', '【回退关系】', ...revertLines] : []),
      '',
      '【区间日志汇总】',
      ...(logLines.length ? logLines : ['- 尚无日志汇总']),
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      message.success('故障定位证据已复制到剪贴板')
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const copied = document.execCommand('copy')
      document.body.removeChild(textarea)
      copied ? message.success('故障定位证据已复制到剪贴板') : message.error('复制失败，请检查浏览器剪贴板权限')
    }
  }

  const revertRelations = comparison.data?.pr_changes.revert_commits || []
  const cancelledRelationCount = revertRelations.filter(item => item.cancellation_status === 'cancelled').length
  const retainedRelationCount = revertRelations.length - cancelledRelationCount

  return (
    <Card
      className="job-history-workspace"
      size="small"
      title={`Job 运行历史 · ${job.job_name}`}
      style={{
        margin: '8px 0',
        width: '100%',
        minWidth: 0,
        maxWidth: '100%',
        overflow: 'hidden',
        // Do not let comparison tables contribute their intrinsic width to
        // the parent Job table's max-content width.
        contain: 'inline-size',
      }}
    >
      {intervalInvalid && <Alert type="warning" showIcon message="Start 必须早于 End，请重新选择边界。" style={{ marginTop: 10 }} />}
      {isError && <Alert type="error" showIcon message="历史记录加载失败" style={{ marginTop: 10 }} />}
      {!isLoading && !isError && !data.some(item => item.job_id === job.job_id) && (
        <Alert type="info" showIcon message="当前 Job 不属于首页 CI 配置定义的正式运行，因此未加入时间线。" style={{ marginTop: 10 }} />
      )}
      <div style={{ marginTop: 12 }}>
        <Segmented
          value={historyView}
          onChange={value => setHistoryView(value as 'overview' | 'performance')}
          options={[{ label: '概览', value: 'overview' }, { label: '性能精度', value: 'performance' }]}
        />
      </div>

      {historyView === 'overview' && (isLoading ? <div className="job-history-view-enter" style={{ padding: 40, textAlign: 'center' }}><Spin /></div> : timeline.length === 0 ? (
        <Empty description="暂无同名 Job 历史记录" />
      ) : (
        <div className="job-history-view-enter"
          style={{
            marginTop: 12,
            border: '1px solid #d9d9d9',
            borderRadius: 2,
            background: '#fafafa',
            padding: '8px 10px 4px',
            overflow: 'hidden',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 2 }}>
            <Text strong>运行时间链</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              共 {timeline.length} 个节点，默认定位最新 10 个，可左右滚动查看
            </Text>
          </div>
          <div
            ref={timelineViewportRef}
            style={{ width: '100%', maxWidth: 1980, overflowX: 'auto', overflowY: 'hidden', padding: '12px 4px 10px' }}
          >
            <div style={{ display: 'flex', width: 'max-content', position: 'relative', gap: 8 }}>
            <div style={{ position: 'absolute', left: 48, right: 48, top: 53, height: 1, background: '#8c8c8c', zIndex: 1 }} />
            {hasSelectedRange && rangeEndIndex > rangeStartIndex && (
              <div
                style={{
                  position: 'absolute',
                  left: rangeStartIndex * 198 + 95,
                  width: (rangeEndIndex - rangeStartIndex) * 198,
                  top: 52,
                  height: 3,
                  borderRadius: 2,
                  background: '#17133f',
                  boxShadow: '0 3px 7px rgba(23, 19, 63, 0.28)',
                  zIndex: 1,
                }}
              />
            )}
            {timeline.map((record, index) => {
              const selectedAsStart = startJobId === record.job_id
              const selectedAsEnd = endJobId === record.job_id
              return (
                <TimelineNode
                  key={record.job_id}
                  record={record}
                  currentJobId={job.job_id}
                  selectedAsStart={selectedAsStart}
                  selectedAsEnd={selectedAsEnd}
                  onStart={() => { setStartJobId(record.job_id); if (endJobId === record.job_id) setEndJobId(null) }}
                  onEnd={() => { setEndJobId(record.job_id); if (startJobId === record.job_id) setStartJobId(null) }}
                  onOpenSummary={setSummaryJobId}
                />
              )
            })}
            </div>
          </div>
        </div>
      ))}

      {historyView === 'performance' && <PerformanceAccuracyView timeline={timeline} loading={isLoading} />}

      {historyView === 'overview' && canCompare && comparison.isLoading && (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin tip="正在获取 PR 证据链，请稍候…" size="large" />
          <div style={{ marginTop: 34 }}><Text type="secondary">PR 较多时需要从 GitHub 分批获取合入时间和详情，请不要重复点击 Start / End。</Text></div>
        </div>
      )}
      {historyView === 'overview' && canCompare && comparison.isError && <Alert type="error" showIcon message="差异证据加载失败，请检查后端日志。" />}
      {historyView === 'overview' && comparison.data && (
        <div style={{ marginTop: 12, width: '100%', minWidth: 0, maxWidth: '100%', overflow: 'hidden' }}>
          <Button type="primary" icon={<CopyOutlined />} loading={intervalLogSummaries.isLoading} onClick={copyComparisonEvidence} style={{ marginBottom: 8 }}>一键复制全部故障定位证据</Button>
          <div style={{ width: '100%', maxWidth: 620, padding: '6px 2px 14px' }}>
            <Text type="secondary" style={{ display: 'block', marginBottom: 10, fontSize: 12 }}>vllm-ascend 版本区间</Text>
            {(['start', 'end'] as const).map((side, index) => {
              const sha = comparison.data?.[side].vllm_ascend_commit
              const commitDate = comparison.data?.[side].vllm_ascend_commit_date
              return <div key={side}>
                {index > 0 && (
                  <div style={{ width: 132, height: 22, position: 'relative', margin: '4px 0' }} aria-hidden="true">
                    <span style={{ position: 'absolute', left: 0, right: 2, top: 10, borderTop: '1px solid #a6a6a6' }} />
                    <span style={{ position: 'absolute', right: 2, top: 7, width: 7, height: 7, borderTop: '1px solid #a6a6a6', borderRight: '1px solid #a6a6a6', transform: 'rotate(45deg)' }} />
                  </div>
                )}
                <div style={{ fontSize: 12, lineHeight: '20px', textAlign: 'left' }}>
                  <div style={{ color: '#595959' }}>{side === 'start' ? 'Start' : 'End'}</div>
                  <div>
                    {sha ? (
                      <Link href={`https://github.com/vllm-project/vllm-ascend/commit/${sha}`} target="_blank" title={sha}>
                        <code style={{ fontSize: 12 }}>{sha.slice(0, 12)}</code>
                      </Link>
                    ) : <Text type="secondary" style={{ fontSize: 12 }}>未知</Text>}
                  </div>
                  <div><Text type="secondary" style={{ fontSize: 12 }}>{displayValue(commitDate)}</Text></div>
                </div>
              </div>
            })}
          </div>
          <Collapse style={{ width: '100%', minWidth: 0, maxWidth: '100%' }} size="small" defaultActiveKey={['pr']} items={[
            { key: 'pr', label: `PR / 提交修改（${comparison.data.pr_changes.items.length} 个）`, children: <>
              {comparison.data.pr_changes.compare_url && <div style={{ marginBottom: 8 }}><Link href={comparison.data.pr_changes.compare_url} target="_blank">打开 GitHub commit 对比</Link></div>}
              {comparison.data.pr_changes.error && <Alert type="warning" showIcon message={`GitHub compare 获取失败：${comparison.data.pr_changes.error}`} style={{ marginBottom: 8 }} />}
              {comparison.data.pr_changes.items.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Start / End 的 commit 范围内没有提交，或 compare 暂不可用" /> :
                <div style={{ width: '100%', maxWidth: 980, minWidth: 0, overflowX: 'auto' }}>
                  <Table scroll={{ x: 910 }} size="small" pagination={false} tableLayout="fixed" rowKey={row => row.sha || String(row.number)} dataSource={[...comparison.data.pr_changes.items].sort((a, b) => new Date(a.merged_at || 0).getTime() - new Date(b.merged_at || 0).getTime())} columns={[
                    { title: '', width: 34, render: () => <div style={{ position: 'relative', height: 36, borderLeft: '2px solid #64748b', marginLeft: 8 }}><span style={{ position: 'absolute', top: 12, left: -6, width: 10, height: 10, background: '#334155', border: '2px solid #fff', boxShadow: '0 0 0 1px #334155' }} /></div> },
                    { title: 'PR', width: 66, render: (_, row) => row.url ? <Link href={row.url} target="_blank">{row.number ? `#${row.number}` : row.sha?.slice(0, 7)}</Link> : row.number ? `#${row.number}` : row.sha?.slice(0, 7) },
                    { title: '合入时间', dataIndex: 'merged_at', width: 112, render: value => value ? <span title={formatTimezone(value)}>{formatTimezone(value, 'MM-DD HH:mm')}</span> : '未知' },
                    { title: '标题', dataIndex: 'title', width: 520, ellipsis: true },
                    { title: '作者', dataIndex: 'author', width: 100, ellipsis: true },
                    { title: 'Commit', width: 78, render: (_, row) => row.commit_url ? <Link href={row.commit_url} target="_blank">{row.sha?.slice(0, 7)}</Link> : row.sha?.slice(0, 7) },
                  ]} />
                </div>}
              {(comparison.data.pr_changes.revert_commits?.length || 0) > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 8 }}
                  message={`${revertRelations.length} 组 PR 回退关系：${cancelledRelationCount} 组区间内抵消，${retainedRelationCount} 组单方在区间不抵消`}
                  description={(
                    <Space direction="vertical" size={2}>
                      {(comparison.data.pr_changes.revert_commits || []).map(item => (
                        <span key={`${item.sha}-${item.reverted_pr}`}>
                          {item.revert_pr ? <Link href={`https://github.com/vllm-project/vllm-ascend/pull/${item.revert_pr}`} target="_blank">#{item.revert_pr}</Link> : '回退提交'}
                          {' 回退 '}
                          <Link href={`https://github.com/vllm-project/vllm-ascend/pull/${item.reverted_pr}`} target="_blank">#{item.reverted_pr}</Link>
                          {item.url && <>（<Link href={item.url} target="_blank">Commit</Link>）</>}
                          <Tag color={item.cancellation_status === 'cancelled' ? 'green' : 'gold'} style={{ marginLeft: 8 }}>
                            {item.cancellation_status === 'cancelled' ? '双方在区间，已抵消' : '单方在区间，不抵消'}
                          </Tag>
                        </span>
                      ))}
                      {(comparison.data.pr_changes.revert_commits || []).length === 0 && comparison.data.pr_changes.reverted_prs!.map(item => <span key={item.number}>#{item.number} {item.title}</span>)}
                    </Space>
                  )}
                />
              )}
            </> },
            {
              key: 'logs',
              label: `区间日志汇总（${intervalRecords.length} 个节点）`,
              children: intervalLogSummaries.isLoading
                ? <div style={{ padding: 20, textAlign: 'center' }}><Spin tip="正在汇总区间日志" /></div>
                : <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {(intervalLogSummaries.data || []).map(({ record, summary, source }) => (
                      <div key={record.job_id} style={{ borderLeft: `3px solid ${record.conclusion === 'success' ? '#237b4b' : '#b72f3a'}`, padding: '6px 10px', background: '#fafafa' }}>
                        <Space size={8} wrap>
                          <Text strong>{formatTimezone(record.started_at)}</Text>
                          <Tag color={record.conclusion === 'success' ? 'success' : 'error'}>{record.conclusion === 'success' ? '成功' : '失败'}</Tag>
                          <Text type="secondary">{source}</Text>
                        </Space>
                        <div style={{ marginTop: 4 }}>{summary}</div>
                      </div>
                    ))}
                  </Space>,
            },
          ]} />
        </div>
      )}
      <Modal
        open={Boolean(summaryJobId)}
        onCancel={() => setSummaryJobId(null)}
        title="日志根因摘要"
        width={760}
        footer={<Button onClick={() => setSummaryJobId(null)}>关闭</Button>}
      >
        {storedSummary.isLoading ? <div style={{ padding: 30, textAlign: 'center' }}><Spin /></div> : storedSummary.data?.status === 'completed' ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="error"
              showIcon
              message="根因摘要"
              description={storedSummary.data.summary}
              action={(
                <Button
                  type="primary"
                  icon={<RobotOutlined />}
                  loading={createSummary.isPending}
                  onClick={() => createSummary.mutate()}
                >
                  重新生成
                </Button>
              )}
            />
            <Text type="secondary">
              {storedSummary.data.llm_provider}/{storedSummary.data.llm_model} · {storedSummary.data.generation_time_seconds ?? '-'}s · 已持久化
            </Text>
            <Collapse size="small" items={[{ key: 'log', label: '查看提交给模型的日志证据', children: <pre style={{ maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{storedSummary.data.log_excerpt}</pre> }]} />
          </Space>
        ) : (
          <Alert
            type={storedSummary.data?.status === 'failed' ? 'error' : 'info'}
            showIcon
            message={storedSummary.data?.status === 'failed' ? '上次生成失败' : '尚未生成根因摘要'}
            description={storedSummary.data?.error_message || '点击后只下载日志并调用一次模型生成根因摘要，不执行故障修复、代码分析或多 Agent 流程。结果将按 Job 永久保存。'}
            action={<Button type="primary" icon={<RobotOutlined />} loading={createSummary.isPending} onClick={() => createSummary.mutate()}>{storedSummary.data?.status === 'failed' ? '重试' : '生成摘要'}</Button>}
          />
        )}
      </Modal>
    </Card>
  )
}
