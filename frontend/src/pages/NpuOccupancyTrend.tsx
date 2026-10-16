import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Drawer, Empty, Input, Popover, Progress, Row, Select, Space, Spin, Statistic, Tag, Typography, message } from 'antd'
import dayjs from 'dayjs'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useMutation, useQuery } from '@tanstack/react-query'
import CIBuildTimeRange, { type CIBuildTimeRangeValue } from '../components/CIBuildTimeRange'
import { type OccupancyRun, type OccupancyView, getOccupancyAnalysis, getOccupancyDetails, getOccupancyOptions, getOccupancyTrend, refreshOccupancySnapshot } from '../services/npuOccupancy'

const { Text } = Typography

const OCCUPANCY_CACHE_STALE_TIME = 5 * 60 * 1000

const formatCards = (value: number) => `${Math.round(value)} 卡`

function RunName({ run }: { run: OccupancyRun }) {
  const name = <>{run.run_display_name}</>
  return (run.github_url || run.run_url)
    ? <a href={run.github_url || run.run_url || undefined} target="_blank" rel="noreferrer">{name}</a>
    : name
}

function WorkflowName({ run }: { run: OccupancyRun }) {
  return <span title={run.workflow_name || ''}>
    {run.workflow_name || '未关联 Workflow'}
  </span>
}

function RunContext({ run }: { run: OccupancyRun }) {
  if (run.pr_number) return <Text type="secondary" style={{ fontSize: 12 }}>PR #{run.pr_number}</Text>
  if (run.event) return <Text type="secondary" style={{ fontSize: 12 }}>Trigger: {run.event}</Text>
  return run.trigger_scope ? <Text type="secondary" style={{ fontSize: 12 }}>Ref: {run.trigger_scope}</Text> : null
}

function RunProject({ run }: { run: OccupancyRun }) {
  const projects = [...new Set(run.tasks.map(task => task.project).filter(Boolean))]
  return <span title={projects.join(', ')}>{projects.join(', ') || '-'}</span>
}

function RunNodes({ run }: { run: OccupancyRun }) {
  const nodes = [...new Set(run.tasks.map(task => task.node_ip).filter(Boolean))]
  return <span title={nodes.join(', ')}>{nodes.join(', ') || '-'}</span>
}

function RunJobCount({ run }: { run: OccupancyRun }) {
  const jobs = [...new Set(run.tasks.map(task => task.job_name || task.workflow).filter(Boolean))]
  if (!jobs.length) return <span>{run.task_count} 个 Job</span>
  return <Popover
    trigger="hover"
    placement="left"
    overlayClassName="npu-occupancy-job-popover"
    content={<div className="npu-occupancy-job-popover-content">{jobs.map(job => <div key={job}>{job}</div>)}</div>}
  >
    <button type="button" className="npu-occupancy-job-count">{run.task_count} 个 Job</button>
  </Popover>
}

function RunJobs({ run }: { run: OccupancyRun }) {
  const jobs = [...new Set(run.tasks.map(task => task.job_name || task.workflow).filter(Boolean))]
  return <span title={jobs.join(', ')}>{jobs.join(', ') || '-'}</span>
}

function OccupancyTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const point = payload[0].payload
  return <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '10px 12px' }}>
    <Text strong>{dayjs(point.timestamp).format('MM-DD HH:mm')}</Text>
    <div style={{ color: '#1677ff', marginTop: 4, fontSize: 12 }}>占用：{formatCards(point.occupied_cards)}</div>
  </div>
}

export default function NpuOccupancyTrend() {
  const [view, setView] = useState<OccupancyView>('pool')
  const [analysisMode, setAnalysisMode] = useState<'table' | 'timeline'>('table')
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set())
  const [analysisSearch, setAnalysisSearch] = useState('')
  const [workflowFilter, setWorkflowFilter] = useState('all')
  const [drawerExpandedGroups, setDrawerExpandedGroups] = useState<Set<string>>(() => new Set())
  const [drawerSearch, setDrawerSearch] = useState('')
  const [drawerGroupBy, setDrawerGroupBy] = useState<OccupancyView>('pool')
  const [timeRange, setTimeRange] = useState<CIBuildTimeRangeValue>(() => {
    const end = dayjs()
    return { start: end.subtract(1, 'hour'), end, preset: '1h' }
  })
  const [selectedNames, setSelectedNames] = useState<string[]>([])
  const [drillTime, setDrillTime] = useState<string | null>(null)
  const [refreshStatus, setRefreshStatus] = useState<string | null>(null)
  const [metadataRefreshAttempts, setMetadataRefreshAttempts] = useState(0)
  // The chart stays an all-pool trend. Filters below belong to range analysis only.
  const chartParams = useMemo(() => ({ dimension: 'pool' as OccupancyView, names: [], start: timeRange.start.toISOString(), end: timeRange.end.toISOString() }), [timeRange])
  const analysisParams = useMemo(() => ({ dimension: view, names: selectedNames, start: timeRange.start.toISOString(), end: timeRange.end.toISOString() }), [view, selectedNames, timeRange])
  const optionsQuery = useQuery({ queryKey: ['npu-occupancy-options', view], queryFn: () => getOccupancyOptions(view), staleTime: OCCUPANCY_CACHE_STALE_TIME, refetchOnWindowFocus: false })
  const trendQuery = useQuery({ queryKey: ['npu-occupancy-trend', chartParams], queryFn: () => getOccupancyTrend(chartParams), staleTime: OCCUPANCY_CACHE_STALE_TIME, refetchOnWindowFocus: false })
  const analysisQuery = useQuery({ queryKey: ['npu-occupancy-analysis', analysisParams], queryFn: () => getOccupancyAnalysis(analysisParams), enabled: trendQuery.isSuccess, staleTime: OCCUPANCY_CACHE_STALE_TIME, refetchOnWindowFocus: false })
  const refreshMutation = useMutation({
    mutationFn: () => refreshOccupancySnapshot({ start: chartParams.start, end: chartParams.end }),
    onMutate: () => setRefreshStatus('正在同步占用快照…'),
    onSuccess: async () => {
      setRefreshStatus('占用快照已更新，正在刷新页面数据…')
      await Promise.all([trendQuery.refetch(), analysisQuery.refetch()])
      setRefreshStatus('页面数据已更新；Workflow 名称正在后台批量补全。')
      message.success('占用快照已更新')
    },
    onError: () => setRefreshStatus('更新失败，请稍后重试。'),
  })
  const trend = trendQuery.data
  const snapshotDetailsQuery = useQuery({
    queryKey: ['npu-occupancy-details', drillTime, chartParams, drawerGroupBy],
    queryFn: () => getOccupancyDetails({ ...chartParams, timestamp: drillTime!, group_by: drawerGroupBy }),
    enabled: Boolean(drillTime),
  })
  const [loadingSeconds, setLoadingSeconds] = useState(0)
  useEffect(() => {
    if (!trendQuery.isFetching) {
      setLoadingSeconds(0)
      return
    }
    const startedAt = Date.now()
    const updateElapsed = () => setLoadingSeconds(Math.floor((Date.now() - startedAt) / 1000))
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 1000)
    return () => window.clearInterval(timer)
  }, [trendQuery.isFetching, chartParams])
  useEffect(() => {
    setDrawerSearch('')
    setDrawerExpandedGroups(new Set())
  }, [drillTime])
  useEffect(() => {
    setDrawerExpandedGroups(new Set())
  }, [drawerGroupBy])
  useEffect(() => {
    setMetadataRefreshAttempts(0)
  }, [analysisParams])
  const options = view === 'pool'
    ? (optionsQuery.data?.options ?? [])
    : (analysisQuery.data?.groups ?? []).map(group => ({ name: group.name, label: group.name, capacity: null, configured: true }))
  const workflowOptions = useMemo(() => [...new Set(
    (analysisQuery.data?.groups ?? []).flatMap(group => group.runs.map(run => run.workflow_name).filter(Boolean)),
  )].sort() as string[], [analysisQuery.data?.groups])
  const analysisGroups = useMemo(() => {
    const keyword = analysisSearch.trim().toLowerCase()
    return (analysisQuery.data?.groups ?? []).flatMap(group => {
      const workflowRuns = workflowFilter === 'all' ? group.runs : group.runs.filter(run => run.workflow_name === workflowFilter)
      if (!workflowRuns.length) return []
      if (!keyword || group.name.toLowerCase().includes(keyword)) return [{ ...group, runs: workflowRuns }]
      const runs = workflowRuns.filter(run => [run.run_display_name, run.workflow_name, run.workflow_ref, run.event, run.trigger_scope, run.pr_number ? `pr #${run.pr_number}` : null, ...run.tasks.flatMap(task => [task.project, task.workflow, task.job_name, task.node_ip, task.env_id])].some(value => value?.toLowerCase().includes(keyword)))
      return runs.length ? [{ ...group, runs }] : []
    })
  }, [analysisQuery.data?.groups, analysisSearch, workflowFilter])
  const hasUnresolvedWorkflow = useMemo(
    () => (analysisQuery.data?.groups ?? []).some(group => group.runs.some(
      run => Boolean(run.workflow_run_id) && !run.workflow_name,
    )),
    [analysisQuery.data?.groups],
  )
  useEffect(() => {
    // Metadata is fetched by the backend in one bounded background batch.
    // Re-read local data briefly so the page updates without a manual refresh.
    if (!hasUnresolvedWorkflow || metadataRefreshAttempts >= 12 || analysisQuery.isFetching) return
    const timer = window.setTimeout(() => {
      setMetadataRefreshAttempts(attempts => attempts + 1)
      void analysisQuery.refetch()
    }, 3_000)
    return () => window.clearTimeout(timer)
  }, [analysisQuery, hasUnresolvedWorkflow, metadataRefreshAttempts])
  const drawerGroups = useMemo(() => {
    const keyword = drawerSearch.trim().toLowerCase()
    const groups = snapshotDetailsQuery.data?.groups ?? []
    if (!keyword) return groups
    return groups.flatMap(group => {
      if (group.name.toLowerCase().includes(keyword)) return [group]
      const runs = group.runs.filter(run => [run.run_display_name, run.workflow_name, run.workflow_ref, run.event, run.trigger_scope, run.pr_number ? `pr #${run.pr_number}` : null, ...run.tasks.flatMap(task => [task.project, task.workflow, task.job_name, task.node_ip, task.env_id, task.npu_list, task.status])].some(value => value?.toLowerCase().includes(keyword)))
      return runs.length ? [{ ...group, runs }] : []
    })
  }, [snapshotDetailsQuery.data?.groups, drawerSearch])
  const chartData = trend?.series.map(point => ({ ...point, label: dayjs(point.timestamp).format('MM-DD HH:mm') })) ?? []
  const capacity = trend?.metrics.capacity ?? null
  const analysisStart = dayjs(analysisQuery.data?.start ?? timeRange.start)
  const analysisEnd = dayjs(analysisQuery.data?.end ?? timeRange.end)
  const estimatedProgress = Math.min(95, Math.max(5, Math.round(loadingSeconds / 90 * 100)))

  const changeView = (next: OccupancyView) => {
    setView(next)
    setSelectedNames([])
    setDrillTime(null)
    setExpandedGroups(new Set())
  }

  const toggleGroup = (name: string) => {
    setExpandedGroups(current => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleDrawerGroup = (name: string) => {
    setDrawerExpandedGroups(current => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const initialLoading = trendQuery.isLoading && !trend

  useEffect(() => {
    if (!import.meta.env.DEV) return
    const debugRows = (analysisQuery.data?.groups ?? []).flatMap(group => group.runs.map(run => ({
      workflow_run_id: run.workflow_run_id,
      workflow_name: run.workflow_name,
      run_display_title: run.run_display_title,
      workflow_file: run.workflow_file,
      workflow_ref: run.workflow_ref,
      workflow_config_match: run.workflow_config_match,
      github_url: run.github_url,
    })))
    console.debug('[npu occupancy run metadata]', debugRows)
  }, [analysisQuery.data?.groups])

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space direction="vertical" size={0}>
          {trend ? <Text style={{ color: '#756f87', fontSize: 13, fontWeight: 500 }}>数据快照：{dayjs(trend.snapshot_at).format('YYYY-MM-DD HH:mm:ss')}</Text> : <span />}
          {refreshStatus ? <Text type={refreshMutation.isError ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>{refreshStatus}</Text> : null}
        </Space>
        <Space size={10}><CIBuildTimeRange value={timeRange} onApply={setTimeRange} maxDays={30} showLabel={false} width={164} buttonClassName="npu-occupancy-range-button" /><Button className="npu-occupancy-refresh-button" onClick={() => refreshMutation.mutate()} loading={refreshMutation.isPending}>更新数据</Button></Space>
      </div>
      {initialLoading ? <Card style={{ minHeight: 360, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
        <div style={{ width: 'min(460px, 88%)' }}>
          <Spin size="large" />
          <div style={{ marginTop: 22 }}><Progress percent={estimatedProgress} status="active" showInfo={false} /></div>
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>估算进度 {estimatedProgress}%</Text>
        </div>
      </Card> : trendQuery.isError ? <Alert type="error" showIcon message="NPU 占用数据加载失败" description="无法从 pod-history-api 获取完整数据，请缩小时间范围后重试。" /> : <>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="当前范围峰值占用" value={trend?.metrics.peak ?? 0} suffix="卡" valueStyle={{ color: '#1677ff' }} /><Text type="secondary">2 分钟采样最高值</Text></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="当前范围平均占用" value={trend?.metrics.average ?? 0} precision={1} suffix="卡" /><Text type="secondary">按采样点平均</Text></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="范围末时刻占用" value={trend?.metrics.latest ?? 0} suffix="卡" valueStyle={{ color: '#13c2c2' }} /><Text type="secondary">{trend?.series.length ? dayjs(trend.series[trend.series.length - 1].timestamp).format('MM-DD HH:mm') : '-'}</Text></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card size="small"><Statistic title="容量基线" value={capacity ?? '-'} suffix={capacity ? '卡' : ''} /><Text type="secondary">{capacity ? `仅所选已配置资源池 · 峰值占比 ${Math.round((trend?.metrics.peak ?? 0) / capacity * 100)}%` : '合计范围含未配置资源池，不显示容量线'}</Text></Card></Col>
      </Row>
      <Card title="NPU 卡占用存量趋势" extra={<Text type="secondary">点击曲线中的时刻查看占用明细</Text>}>
        {chartData.length === 0 ? <Empty description="当前范围暂无 NPU 占用记录" /> : <ResponsiveContainer width="100%" height={370}><LineChart data={chartData} onClick={(state: any) => state?.activePayload?.[0]?.payload?.timestamp && setDrillTime(state.activePayload[0].payload.timestamp)}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" minTickGap={48} tick={{ fontSize: 12 }} /><YAxis label={{ value: '占用卡数', angle: -90, position: 'insideLeft' }} allowDecimals={false} /><Tooltip content={<OccupancyTooltip />} />{capacity ? <ReferenceLine y={capacity} stroke="#fa8c16" strokeDasharray="6 4" label={{ value: `已配置容量 ${capacity} 卡`, position: 'insideTopRight', fill: '#ad6800', fontSize: 12 }} /> : null}<Line type="monotone" dataKey="occupied_cards" name="合计占用" stroke="#1677ff" strokeWidth={3} dot={false} activeDot={{ r: 5 }} /></LineChart></ResponsiveContainer>}
      </Card>
      <Card title="占用分析" size="small" className="npu-occupancy-analysis-card">
        <div className="npu-occupancy-analysis-toolbar">
          <div className="npu-occupancy-view-toggle" data-view={view}><span className="npu-occupancy-view-indicator" aria-hidden="true" /><button type="button" className={view === 'pool' ? 'is-active' : ''} onClick={() => changeView('pool')}>按资源池</button><button type="button" className={view === 'project' ? 'is-active' : ''} onClick={() => changeView('project')}>按项目</button></div>
          <Select mode="multiple" allowClear maxTagCount="responsive" loading={optionsQuery.isLoading || analysisQuery.isLoading} value={selectedNames} onChange={setSelectedNames} placeholder={`${view === 'pool' ? '资源池' : '项目 / 社区'}（留空为合计）`} className="npu-occupancy-analysis-select" options={options.map(option => ({ value: option.name, label: option.capacity ? `${option.label} · ${option.capacity} 卡` : option.label }))} />
          <Input allowClear value={analysisSearch} onChange={event => setAnalysisSearch(event.target.value)} placeholder="搜索项目、任务或节点" className="npu-occupancy-analysis-search" />
          <div className="npu-occupancy-analysis-mode"><button type="button" className={analysisMode === 'table' ? 'is-active' : ''} onClick={() => setAnalysisMode('table')}>表格视图</button><button type="button" className={analysisMode === 'timeline' ? 'is-active' : ''} onClick={() => setAnalysisMode('timeline')}>时间线视图</button></div>
        </div>
        <Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 14 }}>统计区间：{analysisStart.format('MM-DD HH:mm')} — {analysisEnd.format('MM-DD HH:mm')}</Text>
        {analysisQuery.isLoading ? <Spin /> : analysisGroups.length ? analysisMode === 'table' ? <div className="npu-occupancy-analysis-table">
          <div className="npu-occupancy-analysis-groups-v2">
            <div className="npu-occupancy-analysis-head"><span>{view === 'pool' ? '资源池' : '项目 / 社区'}</span><span>峰值占用</span><span>平均占用</span><span>任务数</span></div>
            {analysisGroups.map(group => {
              const expanded = expandedGroups.has(group.name)
              return <section key={`v2-${group.name}`} className="npu-occupancy-analysis-group">
                <div className="npu-occupancy-analysis-row"><Text strong>{group.name}</Text><span>{group.peak_cards} 卡</span><span>{group.average_cards} 卡</span><span>{group.task_count}</span><button type="button" className={`npu-occupancy-expand ${expanded ? 'is-expanded' : ''}`} aria-label={expanded ? '收起任务' : '展开任务'} onClick={() => toggleGroup(group.name)}><span className="npu-occupancy-expand-dot" /><span className="npu-occupancy-expand-arrow" /></button></div>
                {expanded ? <div className="npu-occupancy-run-table">
                  <div className="npu-occupancy-run-head"><span>项目</span><span className="npu-occupancy-workflow-head">Workflow <Select size="small" variant="borderless" value={workflowFilter} onChange={setWorkflowFilter} suffixIcon={<span className="npu-occupancy-workflow-caret" />} options={[{ value: 'all', label: '全部' }, ...workflowOptions.map(name => ({ value: name, label: name }))]} /></span><span>Run</span><span>来源</span><span>节点</span><span>占用卡数</span><span>Job 数</span><span>占用时段</span></div>
                  {group.runs.map(run => <div key={run.key} className="npu-occupancy-analysis-task"><RunProject run={run} /><WorkflowName run={run} /><span><RunName run={run} /></span><RunContext run={run} /><RunNodes run={run} /><span>{run.cards} NPU</span><RunJobCount run={run} /><span>{dayjs(run.created_at).format('MM-DD HH:mm')} — {dayjs(run.effective_end).format('MM-DD HH:mm')}</span></div>)}
                </div> : null}
              </section>
            })}
          </div>
          <div className="npu-occupancy-analysis-head"><span>{view === 'pool' ? '资源池' : '项目 / 社区'}</span><span>峰值占用</span><span>平均占用</span><span>任务数</span></div>
          {analysisGroups.map(group => { const expanded = expandedGroups.has(group.name); return <section key={group.name} className="npu-occupancy-analysis-group"><div className="npu-occupancy-analysis-row"><Text strong>{group.name}</Text><span>{group.peak_cards} 卡</span><span>{group.average_cards} 卡</span><span>{group.task_count}</span><button type="button" className={`npu-occupancy-expand ${expanded ? 'is-expanded' : ''}`} aria-label={expanded ? '收起任务' : '展开任务'} onClick={() => toggleGroup(group.name)}><span className="npu-occupancy-expand-dot" /><span className="npu-occupancy-expand-arrow">{expanded ? '↑' : '↓'}</span></button></div>{expanded ? <div className="npu-occupancy-run-table"><div className="npu-occupancy-run-head"><span>项目</span><span>Workflow</span><span>Run</span><span>Job</span><span>来源</span><span>占用卡数</span><span>Job 数</span><span>占用时段</span></div>{group.runs.map(run => <div key={run.key} className="npu-occupancy-analysis-task"><RunProject run={run} /><WorkflowName run={run} /><span><RunName run={run} /></span><RunJobs run={run} /><RunContext run={run} /><span>{run.cards} NPU</span><span>{run.task_count} 个 Job</span><span>{dayjs(run.created_at).format('MM-DD HH:mm')} — {dayjs(run.effective_end).format('MM-DD HH:mm')}</span></div>)}</div> : null}</section> })}
        </div> : <div className="npu-occupancy-timeline"><div className="npu-occupancy-timeline-axis"><span>资源信息</span><span>{timeRange.start.format('HH:mm')}</span><span>{timeRange.start.add(timeRange.end.diff(timeRange.start) / 3, 'millisecond').format('HH:mm')}</span><span>{timeRange.start.add(timeRange.end.diff(timeRange.start) * 2 / 3, 'millisecond').format('HH:mm')}</span><span>{timeRange.end.format('HH:mm')}</span></div>{analysisGroups.map(group => { const expanded = expandedGroups.has(group.name); return <section key={group.name} className="npu-occupancy-timeline-group"><div className="npu-occupancy-timeline-group-head"><div><Text strong>{group.name}</Text><Text type="secondary">峰值 {group.peak_cards} 卡 · {group.task_count} 个任务</Text></div><button type="button" className={`npu-occupancy-expand ${expanded ? 'is-expanded' : ''}`} aria-label={expanded ? '收起任务时间线' : '展开任务时间线'} onClick={() => toggleGroup(group.name)}><span className="npu-occupancy-expand-dot" /><span className="npu-occupancy-expand-arrow">{expanded ? '↑' : '↓'}</span></button></div>{expanded ? group.runs.map(run => { const rangeMs = timeRange.end.diff(timeRange.start); const left = Math.max(0, dayjs(run.created_at).diff(timeRange.start) / rangeMs * 100); const right = Math.min(100, dayjs(run.effective_end).diff(timeRange.start) / rangeMs * 100); return <div key={run.key} className="npu-occupancy-timeline-row"><div className="npu-occupancy-timeline-task-name"><Text><RunName run={run} /></Text><RunContext run={run} /></div><div className="npu-occupancy-timeline-lane"><Text type="secondary">{run.cards} NPU · {run.task_count} 个 Job</Text><div className="npu-occupancy-timeline-track"><span style={{ left: `${left}%`, width: `${Math.max(1, right - left)}%` }} /></div></div></div> }) : null}</section> })}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的占用任务" />}
      </Card>
      <Drawer width={820} title={`占用明细 · ${drillTime ? dayjs(drillTime).format('YYYY-MM-DD HH:mm') : ''}`} open={Boolean(drillTime)} onClose={() => setDrillTime(null)}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {snapshotDetailsQuery.isLoading ? <Spin /> : snapshotDetailsQuery.isError ? <Alert type="error" showIcon message="明细加载失败" /> : <>
            <Input allowClear value={drawerSearch} onChange={event => setDrawerSearch(event.target.value)} placeholder="搜索资源池、项目、任务或节点" className="npu-occupancy-drawer-search" />
            <div className="npu-occupancy-drawer-group-tabs" data-view={drawerGroupBy}>
              <button type="button" className={drawerGroupBy === 'pool' ? 'is-active' : ''} onClick={() => setDrawerGroupBy('pool')}>按资源池</button>
              <button type="button" className={drawerGroupBy === 'project' ? 'is-active' : ''} onClick={() => setDrawerGroupBy('project')}>按项目</button>
              <span aria-hidden="true" />
            </div>
            <div className="npu-occupancy-drawer-head">
              <Text type="secondary">{drawerGroupBy === 'pool' ? '资源池' : '项目 / 社区'}</Text><Text type="secondary">占用</Text><Text type="secondary">任务数</Text>
            </div>
            {drawerGroups.length ? drawerGroups.map(group => { const expanded = drawerExpandedGroups.has(group.name); return <section key={group.name} className="npu-occupancy-drawer-group">
              <div className="npu-occupancy-drawer-row">
                <Text strong>{group.name}</Text><Text strong>{group.occupied_cards} 卡</Text><Text strong>{group.task_count}</Text>
                <button type="button" className={`npu-occupancy-expand ${expanded ? 'is-expanded' : ''}`} aria-label={expanded ? '收起任务' : '展开任务'} onClick={() => toggleDrawerGroup(group.name)}><span className="npu-occupancy-expand-dot" /><span className="npu-occupancy-expand-arrow" /></button>
              </div>
              {expanded ? <div className="npu-occupancy-drawer-tasks">
                {group.runs.map(run => <div key={run.key} className="npu-occupancy-drawer-task">
                  <div><Text style={{ display: 'block', fontSize: 13 }}><RunName run={run} /></Text><RunContext run={run} /></div>
                  <Text style={{ fontSize: 13 }}>{run.cards} 卡</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>{run.task_count} 个 Job</Text>
                  <Tag color="blue" style={{ width: 'fit-content', marginInlineEnd: 0 }}>{run.event || run.trigger_scope || 'Run'}</Tag>
                </div>)}
              </div> : null}
            </section> }) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的占用任务" />}
          </>}
        </Space>
      </Drawer>
      </>}
    </Space>
  )
}
