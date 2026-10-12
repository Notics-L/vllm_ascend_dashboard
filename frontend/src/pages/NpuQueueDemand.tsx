import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Drawer, Progress, Row, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { Area, AreaChart, Bar, CartesianGrid, ComposedChart, LabelList, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { getNpuQueueDashboard, type QueueItem, type QueueTrendPoint } from '../services/npuQueue'
import { getEnabledResourceClusters, getResourceDashboard } from '../services/resourceDashboard'
import './NpuQueueDemand.css'

const { Text } = Typography
const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 1 })
const comparablePoolName = (value: string) => value.toLowerCase().replace(/^dev[-_]?/, '').replace(/[^a-z0-9]/g, '')
export default function NpuQueueDemand() {
  const [queueProject, setQueueProject] = useState('all')
  const [queuePools, setQueuePools] = useState<string[]>([])
  const [selectedPoint, setSelectedPoint] = useState<QueueTrendPoint | null>(null)
  const [selectedQueue, setSelectedQueue] = useState<QueueItem | null>(null)
  const [poolMode, setPoolMode] = useState<'current' | 'usage'>('current')
  const [trendMode, setTrendMode] = useState<'current' | 'history'>('current')
  const [taskMode, setTaskMode] = useState<'queue' | 'cost'>('queue')
  const [costScopes, setCostScopes] = useState<Array<'actual' | 'full'>>(['actual'])
  const [expandedPoolKeys, setExpandedPoolKeys] = useState<string[]>([])
  const queueQuery = useQuery({ queryKey: ['npu-queue-dashboard'], queryFn: getNpuQueueDashboard, refetchInterval: 30_000 })
  const clusterConfigQuery = useQuery({ queryKey: ['npu-resource-clusters'], queryFn: getEnabledResourceClusters, staleTime: 30_000 })
  const resourceQuery = useQuery({
    queryKey: ['npu-queue-physical-resource'],
    queryFn: () => getResourceDashboard({ include_pods: true }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const data = useMemo(() => {
    const source = queueQuery.data
    if (!source) return source
    return {
      ...source,
      prCosts: [...source.prCosts]
        // A stable ranking makes actual vs. full consumption directly comparable.
        .sort((left, right) => right.allCardHours - left.allCardHours)
        .slice(0, 10),
    }
  }, [queueQuery.data])
  const pools = data?.pools ?? []
  const trend = useMemo(() => (data?.trend ?? []).map((point) => ({
    ...point,
  })), [data])
  const totals = useMemo(() => ({
    waitingJobs: pools.reduce((sum, pool) => sum + pool.waitingJobs, 0),
    waitingCards: pools.reduce((sum, pool) => sum + pool.waitingCards, 0),
    oldest: Math.max(...(data?.queue ?? []).map((item) => item.waitMinutes), 0),
  }), [data, pools])
  const queue = useMemo(() => (data?.queue ?? []).filter((item) => (
    (queueProject === 'all' || item.repository === queueProject) && (queuePools.length === 0 || queuePools.some((poolKey) => pools.find((pool) => pool.key === poolKey)?.name === item.pool))
  )), [data, queuePools, queueProject])
  const queueProjects = useMemo(() => Array.from(new Set((data?.queue ?? []).map((item) => item.repository).filter(Boolean))).sort(), [data])
  const physicalRows = useMemo(() => (resourceQuery.data?.clusters ?? []).map((cluster) => {
    const demand = pools.find((pool) => comparablePoolName(pool.name) === comparablePoolName(cluster.cluster_name))
    const resourceName = clusterConfigQuery.data?.find((item) => item.id === cluster.cluster_id)?.npu_resource_name ?? '未配置资源名'
    const waitingCards = demand?.waitingCards ?? 0
    const pods = cluster.executing_pods
    return {
      key: String(cluster.cluster_id),
      name: cluster.cluster_name,
      capacity: cluster.total.npu,
      runningCards: cluster.used.npu,
      waitingCards,
      idle: cluster.available.npu,
      utilization: cluster.total.npu ? Math.round(cluster.used.npu / cluster.total.npu * 100) : null,
      waitingUtilization: cluster.total.npu ? Math.min(100, waitingCards / cluster.total.npu * 100) : 0,
      executingPods: cluster.executing_pods_count,
      pods,
      queue: (data?.queue ?? []).filter((item) => comparablePoolName(item.pool) === comparablePoolName(cluster.cluster_name)),
      resourceName,
      error: cluster.error,
    }
  }), [clusterConfigQuery.data, data?.queue, pools, resourceQuery.data])
  const physicalCapacity = resourceQuery.data?.overall.total.npu ?? null
  const physicalRunning = resourceQuery.data?.overall.used.npu ?? null
  const physicalIdle = resourceQuery.data?.overall.available.npu ?? null
  const physicalUtilization = physicalCapacity && physicalRunning !== null ? Math.round(physicalRunning / physicalCapacity * 100) : 0

  if (queueQuery.isLoading) return <div className="npu-demand-loading">正在加载 NPU 队列数据…</div>
  if (queueQuery.isError || !data) return <Alert type="error" showIcon message="NPU 队列数据不可用" description="请检查 CI 事实表迁移、采集状态与访问权限。" />
  return <Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Row className="npu-demand-focus-row" gutter={[40, 20]}>
      <Col xs={24} lg={12}>
        <section className="npu-demand-pressure" aria-labelledby="npu-pressure-title">
          <div className="npu-demand-focus-heading"><h3 id="npu-pressure-title">01 当前资源压力</h3><Tag color={totals.waitingCards > 0 ? 'orange' : 'green'}>{totals.waitingCards > 0 ? '存在排队压力' : '资源充足'}</Tag></div>
          <div className="npu-demand-capacity-bar" aria-label={physicalCapacity === null ? '物理 NPU 资源暂不可用' : `物理 NPU 已运行 ${physicalRunning} 卡，空闲 ${physicalIdle} 卡`}><span style={{ width: `${physicalUtilization}%` }} /></div>
          <div className="npu-demand-capacity-label">{physicalCapacity === null ? <span>物理资源快照暂不可用</span> : <><span>已运行 <b>{physicalRunning}</b> 卡</span><span>空闲 <b>{physicalIdle}</b> 卡</span></>}</div>
          <div className="npu-demand-pressure-metrics">
            <div><span>当前等待需求</span><strong>{number(totals.waitingCards)} <em>卡</em></strong></div>
            <div><span>排队 Job</span><strong>{number(totals.waitingJobs)} <em>个</em></strong></div>
            <div><span>最长等待</span><strong>{number(totals.oldest)} <em>min</em></strong></div>
          </div>
          <Text className="npu-demand-pressure-note">等待需求按 CI runner 标签映射；具体可满足性请结合资源池型号与节点约束判断。</Text>
        </section>
      </Col>
      <Col xs={24} lg={12}>
        <section className="npu-demand-delay" aria-labelledby="npu-delay-title">
          <div className="npu-demand-focus-heading"><h3 id="npu-delay-title">Queue 延迟趋势</h3><Text type="secondary">最近 7 天</Text></div>
          <div className="npu-demand-delay-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.dailyQueue} margin={{ top: 4, right: 34, left: -24, bottom: 0 }}><XAxis dataKey="date" tickLine={false} axisLine={false} fontSize={11} interval={0} tickFormatter={(value: string) => value.slice(5)} /><YAxis tickLine={false} axisLine={false} unit="m" fontSize={11} /><Tooltip /><Line type="monotone" dataKey="p90" stroke="#f59e0b" strokeWidth={2.5} dot={false} connectNulls={false} name="P90" /><Line type="monotone" dataKey="p50" stroke="#3b82f6" strokeWidth={2.5} dot={false} connectNulls={false} name="P50" /></LineChart></ResponsiveContainer></div>
          <div className="npu-demand-delay-metrics"><span>P50 <b>{data.queueDelaySummary.p50 === null ? '—' : `${number(data.queueDelaySummary.p50)} min`}</b></span><span>P90 <b>{data.queueDelaySummary.p90 === null ? '—' : `${number(data.queueDelaySummary.p90)} min`}</b></span><span>已上卡样本 <b>{number(data.queueDelaySummary.samples)} 个</b></span></div>
        </section>
      </Col>
    </Row>

    <section className="npu-demand-pool-section" aria-labelledby="npu-pool-title">
      <div className="npu-demand-section-heading">
        <div><h2 id="npu-pool-title">02 各资源池资源状态</h2><Text type="secondary">Kubernetes 实时资源快照；CI 需求将在映射规则确认后关联展示。</Text></div>
        <Segmented value={poolMode} onChange={(value) => setPoolMode(value as 'current' | 'usage')} options={[{ label: '当前资源', value: 'current' }, { label: '资源消耗', value: 'usage' }]} />
      </div>
      {poolMode === 'current' ? <Table rowKey="key" className="npu-demand-pool-table" size="middle" pagination={false} loading={resourceQuery.isLoading} locale={{ emptyText: resourceQuery.isError ? 'Kubernetes 资源快照获取失败' : '暂无已配置的 Kubernetes 资源池' }} dataSource={physicalRows} expandable={{ expandedRowKeys: expandedPoolKeys, onExpandedRowsChange: (keys) => setExpandedPoolKeys(keys.map(String)), expandIconColumnIndex: 4, expandIcon: ({ expanded, onExpand, record }) => <button type="button" className="npu-demand-pool-expand" aria-label={expanded ? `收起 ${record.name} 明细` : `展开 ${record.name} 明细`} onClick={(event) => onExpand(record, event)}>{expanded ? <UpOutlined /> : <DownOutlined />}</button>, expandedRowRender: (record) => <div className="npu-demand-pool-details"><div><h4>当前占用 Pod</h4><Text type="secondary">资源名：{record.resourceName}</Text>{record.pods.length ? <div className="npu-demand-pod-list">{record.pods.map((pod) => <div className="npu-demand-pod-row" key={`${pod.namespace}/${pod.name}`}><strong>{pod.name}</strong><span>{pod.namespace || '—'}</span><span>{pod.phase || 'Unknown'}</span><span>申请 {number(pod.requests.npu)} 卡</span></div>)}</div> : <Text type="secondary">暂无执行中 Pod 明细</Text>}</div><div><h4>等待中的 CI Job</h4>{record.queue.length ? <div className="npu-demand-pod-list">{record.queue.map((item) => <div className="npu-demand-pod-row" key={item.key}><strong>{item.job || item.run}</strong><span>{item.repository}</span><span>等待 {item.waitMinutes} 分钟</span><span>申请 {number(item.cards)} 卡</span></div>)}</div> : <Text type="secondary">无等待</Text>}</div></div> }} columns={[
        { title: '资源池', dataIndex: 'name', key: 'name', render: (value: string) => <strong>{value}</strong> },
        { title: '当前卡占用', key: 'utilization', width: 340, render: (_, record) => record.capacity && record.utilization !== null ? <div className="npu-demand-utilization"><Progress percent={record.utilization} showInfo={false} size="small" strokeColor="#316dff" /><span>{record.runningCards} / {record.capacity} 卡 · {record.utilization}%</span><div className="npu-demand-waiting-demand" title={`等待需求 ${number(record.waitingCards)} 卡`}><Progress percent={record.waitingUtilization} showInfo={false} size="small" strokeColor="#f5b642" trailColor="#fff3d8" /></div></div> : <Text type="secondary">未配置物理容量</Text> },
        { title: '空闲卡', dataIndex: 'idle', key: 'idle', render: (value: number | null) => value === null ? '—' : `${value} 卡` },
        { title: '执行中 Pod', dataIndex: 'executingPods', key: 'executingPods', render: (value: number) => `${value} 个` },
      ]} /> : <div className="npu-demand-single-history"><h3>每日资源消耗（卡时）</h3><Text type="secondary">已结束 NPU Job 的累计卡时（含失败）</Text><ResponsiveContainer width="100%" height={280}><ComposedChart data={data.dailyUsage} margin={{ top: 18, right: 18, left: -18, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f4" /><XAxis dataKey="date" tickLine={false} axisLine={false} /><YAxis yAxisId="left" tickLine={false} axisLine={false} /><YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} /><Tooltip /><Legend /><Bar yAxisId="left" dataKey="cardHours" fill="#52c41a" radius={[4, 4, 0, 0]} name="卡时" /><Line yAxisId="right" type="monotone" dataKey="jobs" stroke="#1677ff" strokeWidth={2} name="已结束 NPU Job 数" /></ComposedChart></ResponsiveContainer></div>}
    </section>

    <section className="npu-demand-trend-section" aria-labelledby="npu-running-trend-title">
      <div className="npu-demand-section-heading"><div><h2 id="npu-running-trend-title">03 Queue / Running 趋势</h2><Text type="secondary">等待来自 CI 队列；运行来自 Kubernetes 实时资源快照。</Text></div><Segmented value={trendMode} onChange={(value) => setTrendMode(value as 'current' | 'history')} options={[{ label: '当前 / 近 24h', value: 'current' }, { label: '历史', value: 'history' }]} /></div>
      {trendMode === 'current' ? <><ResponsiveContainer width="100%" height={320}><AreaChart data={trend} onClick={(state: { activePayload?: Array<{ payload: QueueTrendPoint }> }) => setSelectedPoint(state?.activePayload?.[0]?.payload ?? null)}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="timestamp" minTickGap={28} /><YAxis /><Tooltip /><Legend /><Area type="monotone" dataKey="waitingCards" name="等待卡数" stackId="cards" stroke="#cf1322" fill="#ffccc7" /><Area type="monotone" dataKey="runningCards" name="运行卡数" stackId="cards" stroke="#1677ff" fill="#bae0ff" /></AreaChart></ResponsiveContainer><Text type="secondary">最近 24h；点击图中的时间点，可查看对应时刻的排队与运行明细。</Text></> : <div className="npu-demand-single-history"><h3>每日队列时间</h3><Text type="secondary">已上卡样本的 Queue P50 / P90</Text><ResponsiveContainer width="100%" height={280}><LineChart data={data.dailyQueue} margin={{ top: 18, right: 18, left: -18, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#edf0f4" /><XAxis dataKey="date" tickLine={false} axisLine={false} /><YAxis tickLine={false} axisLine={false} unit="m" /><Tooltip formatter={(value: number) => `${value} min`} /><Legend /><Line type="monotone" dataKey="p50" stroke="#3b82f6" strokeWidth={2.5} dot={false} name="P50" /><Line type="monotone" dataKey="p90" stroke="#f59e0b" strokeWidth={2.5} dot={false} name="P90" /></LineChart></ResponsiveContainer></div>}
    </section>

    <section className="npu-demand-task-section" aria-labelledby="npu-task-title">
      <div className="npu-demand-section-heading"><div><h2 id="npu-task-title">04 任务与资源需求分析</h2></div><Segmented value={taskMode} onChange={(value) => setTaskMode(value as 'queue' | 'cost')} options={[{ label: '当前排队', value: 'queue' }, { label: '历史消耗', value: 'cost' }]} /></div>
      {taskMode === 'queue' ? <><div className="npu-demand-queue-filter"><Select value={queueProject} onChange={setQueueProject} options={[{ value: 'all', label: '全部项目' }, ...queueProjects.map((repository) => ({ value: repository, label: repository }))]} /><Select mode="multiple" allowClear value={queuePools} onChange={setQueuePools} placeholder="全部资源池" options={pools.map((pool) => ({ value: pool.key, label: pool.name }))} /></div><Table<QueueItem> rowKey="key" className="npu-demand-queue-table" dataSource={queue} pagination={false} locale={{ emptyText: '当前没有处于排队状态的匹配 NPU Job' }} columns={[
        { title: '等待时长', dataIndex: 'waitMinutes', width: 150, defaultSortOrder: 'descend', sorter: (a, b) => b.waitMinutes - a.waitMinutes, render: (value: number, record) => <Tag color={record.state === 'short' ? 'default' : 'red'}>{value} 分钟{record.state === 'short' ? ' · 短暂' : ''}</Tag> },
        { title: '项目 / 仓库', dataIndex: 'repository', width: 250, render: (value: string) => <Text type="secondary">{value}</Text> },
        { title: '申请卡数', dataIndex: 'cards', width: 130, align: 'right', render: (value: number) => <strong>{value} 卡</strong> },
        { title: '等待资源池', dataIndex: 'pool', render: (value: string) => <strong>{value}</strong> },
        { title: '', key: 'detail', width: 100, render: (_, record) => <Button type="link" size="small" onClick={() => setSelectedQueue(record)}>查看详情</Button> },
      ]} /></> : <div className="npu-demand-cost-view">
        <div className="npu-demand-cost-toolbar"><Text type="secondary">按 CI runner 标签映射的卡数 × GitHub Actions 实际运行时长计算；可单独或同时显示两种口径。</Text><div className="npu-demand-cost-toggle"><button type="button" aria-pressed={costScopes.includes('actual')} className={`npu-demand-scope-actual ${costScopes.includes('actual') ? 'is-active' : ''}`} onClick={() => setCostScopes((scopes) => scopes.includes('actual') ? scopes.length > 1 ? scopes.filter((scope) => scope !== 'actual') : scopes : [...scopes, 'actual'])}><span className="npu-demand-scope-dot" aria-hidden="true" />成功 Job 卡时</button><button type="button" aria-pressed={costScopes.includes('full')} className={`npu-demand-scope-full ${costScopes.includes('full') ? 'is-active' : ''}`} onClick={() => setCostScopes((scopes) => scopes.includes('full') ? scopes.length > 1 ? scopes.filter((scope) => scope !== 'full') : scopes : [...scopes, 'full'])}><span className="npu-demand-scope-dot" aria-hidden="true" />全部 Job 尝试卡时</button></div></div>
        <ResponsiveContainer width="100%" height={320}><ComposedChart layout="vertical" data={data.prCosts} margin={{ top: 8, right: 58, left: 154, bottom: 8 }} barGap={1} barCategoryGap="35%"><CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#d9e0ea" /><XAxis type="number" tickLine={false} axisLine={{ stroke: '#98a2b3' }} label={{ value: '卡时', position: 'insideBottomRight', offset: -2, fill: '#667085', fontSize: 12 }} /><YAxis type="category" dataKey="branch" width={148} tickLine={false} axisLine={{ stroke: '#98a2b3' }} tick={{ fontSize: 12, fill: '#475467' }} tickFormatter={(value: string) => value.length > 22 ? `${value.slice(0, 22)}…` : value} /><Tooltip labelFormatter={(value) => `流水线：${value}`} formatter={(value: number, name: string) => [`${number(value)} 卡时`, name]} />{costScopes.includes('actual') && <Bar dataKey="cardHours" name="成功 Job 卡时" fill="#1677ff" barSize={8} radius={[0, 4, 4, 0]}><LabelList dataKey="cardHours" position="right" formatter={(value: number) => value ? number(value) : ''} fill="#1677ff" fontSize={11} /></Bar>}{costScopes.includes('full') && <Bar dataKey="allCardHours" name="全部 Job 尝试卡时" fill="#722ed1" barSize={8} radius={[0, 4, 4, 0]}><LabelList dataKey="allCardHours" position="right" formatter={(value: number) => value ? number(value) : ''} fill="#722ed1" fontSize={11} /></Bar>}</ComposedChart></ResponsiveContainer>
      </div>}
    </section>

    <Drawer title={selectedPoint ? `队列时点明细 · ${selectedPoint.timestamp}` : '队列时点明细'} open={Boolean(selectedPoint)} onClose={() => setSelectedPoint(null)} width={760}>
      {selectedPoint && <Space direction="vertical" size="large" style={{ width: '100%' }}><Alert type="info" showIcon message={`${selectedPoint.timestamp}：等待 ${selectedPoint.waitingCards} 卡 / ${selectedPoint.waitingJobs} Job；运行 ${selectedPoint.runningCards} 卡 / ${selectedPoint.runningJobs} Job`} /><Table<QueueItem> rowKey="key" dataSource={queue} pagination={false} columns={[{ title: 'Job', dataIndex: 'job' }, { title: '资源池', dataIndex: 'pool' }, { title: '状态', render: (_, record) => <Tag color={record.state === 'short' ? 'default' : 'red'}>{record.state === 'short' ? '短暂排队' : '排队中'}</Tag> }, { title: '卡数', dataIndex: 'cards', align: 'right' }]} /></Space>}
    </Drawer>
    <Drawer title={selectedQueue ? `排队任务详情 · ${selectedQueue.job}` : '排队任务详情'} open={Boolean(selectedQueue)} onClose={() => setSelectedQueue(null)} width={560}>
      {selectedQueue && <Space direction="vertical" size="middle" style={{ width: '100%' }}><div className="npu-demand-detail-grid"><Text type="secondary">项目 / 仓库</Text><strong>{selectedQueue.repository}</strong><Text type="secondary">等待时长</Text><strong>{selectedQueue.waitMinutes} 分钟</strong><Text type="secondary">申请卡数</Text><strong>{selectedQueue.cards} 卡</strong><Text type="secondary">等待资源池</Text><strong>{selectedQueue.pool}</strong><Text type="secondary">Run</Text><span>{selectedQueue.run}</span><Text type="secondary">创建时间</Text><span>{selectedQueue.createdAt}</span></div><div><Text strong>PR / 标题</Text><br /><Text>{selectedQueue.title}</Text></div></Space>}
    </Drawer>
  </Space>
}
