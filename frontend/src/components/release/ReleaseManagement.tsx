import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as echarts from 'echarts'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  message,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Tabs,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  BarChartOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  GlobalOutlined,
  FilePdfOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from '@ant-design/icons'

import {
  fmtDuration,
  getOverview,
  getRuns,
  getStatusColor,
  listWorkflows,
  triggerSync,
  getSyncTask,
  getSyncTasks,
  discoverWorkflows,
  exportExcel,
  exportPdf,
  ConfigCheckResult,
  getConfigCheck,
  OverviewData,
  BuildRun,
  SyncTask,
  type Workflow,
} from '../../services/releaseManagement'
import ThirdPartyTab from './ThirdPartyTab'
import AIEvaluationTab from './AIEvaluationTab'
import SyncHistoryTab from './SyncHistoryTab'
import ComparisonTab from './ComparisonTab'

const { Title, Text } = Typography
const { Option } = Select

function renderMissingCount(omitted: readonly any[]) {
  return <span>+{omitted.length} more</span>
}

interface KPIProps {
  label: string
  value: string | number
  color: string
  sub?: string
}

function KPICard({ label, value, color, sub }: KPIProps) {
  return (
    <Card bordered={false} style={{ borderRadius: 10 }}>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 28, fontWeight: 700, color }}>
          {value}
        </div>
        <div style={{ fontSize: 13, color: '#8c8c8c', marginTop: 4 }}>
          {label}
        </div>
        {sub && (
          <div style={{ fontSize: 11, color: '#bfbfbf', marginTop: 2 }}>
            {sub}
          </div>
        )}
      </div>
    </Card>
  )
}

/**
 * Generic ECharts component — safe for lazy-loaded Tabs.
 * Renders chart only after the DOM element is actually visible.
 */
function EChartBox({ option, height = 300 }: { option: any; height?: number }) {
  const domRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!domRef.current) return
    chartRef.current?.dispose()
    chartRef.current = echarts.init(domRef.current)
    if (chartRef.current && option) {
      chartRef.current.setOption(option)
    }
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [option])

  return <div ref={domRef} style={{ width: '100%', height }} />
}

export default function ReleaseManagement() {
  const navigate = useNavigate()
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [selectedWfId, setSelectedWfId] = useState<number | null>(null)
  const [daysBack, setDaysBack] = useState(30)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [configCheck, setConfigCheck] = useState<ConfigCheckResult | null>(null)
  const [activeSyncTask, setActiveSyncTask] = useState<SyncTask | null>(null)
  const [syncTasks, setSyncTasks] = useState<SyncTask[]>([])
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [runsData, setRunsData] = useState<{ runs: BuildRun[]; total: number }>({ runs: [], total: 0 })

  // Cleanup poll timer on unmount
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [])

  // Poll helper defined early so useEffect can reference it
  const startPollSync = (taskId: number) => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    pollTimerRef.current = setInterval(async () => {
      try {
        const task = await getSyncTask(taskId)
        setActiveSyncTask(task)
        if (['completed', 'failed'].includes(task.status)) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
          setActiveSyncTask(null)
          if (task.status === 'completed') {
            message.success('同步完成')
            getOverview(selectedWfId ? [selectedWfId] : null, daysBack).then(ov => setOverview(ov)).catch(() => {})
          } else {
            message.error(`同步失败: ${task.error_message || '未知错误'}`)
          }
        }
      } catch { /* polling error */ }
    }, 2000)
  }

  // Load config check + workflows + sync tasks
  useEffect(() => {
    getConfigCheck().then((r) => setConfigCheck(r)).catch(() => {})
    const loadAll = async () => {
      try {
        const w = await listWorkflows(false)
        setWorkflows(w)
      } catch { /* ignore */ }
      try {
        const tasksResult = await getSyncTasks(null, null, 10)
        setSyncTasks(tasksResult.tasks)
        const running = tasksResult.tasks.find((t: SyncTask) => t.status === 'running' || t.status === 'pending')
        if (running) startPollSync(running.id)
      } catch { /* ignore */ }
    }
    loadAll()
  }, [])

  // Auto-select first enabled workflow on initial load only
  const initialSelectDone = useRef(false)
  useEffect(() => {
    if (workflows.length > 0 && !initialSelectDone.current) {
      initialSelectDone.current = true
      const firstEnabled = workflows.find((w) => w.enabled)
      if (firstEnabled) setSelectedWfId(firstEnabled.id)
    }
  }, [workflows])

  // Load overview when selection changes
  useEffect(() => {
    if (!configCheck?.github_configured) return
    if (!selectedWfId) return

    setLoading(true)
    Promise.all([
      getOverview([selectedWfId], daysBack),
      getRuns([selectedWfId], statusFilter, null, 1, 50),
    ])
      .then(([ov, rd]) => {
        setOverview(ov)
        setRunsData({ runs: rd.runs, total: rd.total })
      })
      .catch(() => message.error('加载数据失败'))
      .finally(() => setLoading(false))
  }, [selectedWfId, daysBack, statusFilter, configCheck?.github_configured])

  const handleSync = async () => {
    if (!selectedWfId) return
    try {
      const result = await triggerSync(selectedWfId, daysBack, 100)
      if (result.task_id) {
        startPollSync(result.task_id)
      }
      message.success('同步任务已创建，后台执行中...')
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err ? (err as { detail: string }).detail : '同步触发失败'
      message.error(msg)
    }
  }

  const handleExportExcel = async () => {
    try {
      const blob = await exportExcel(selectedWfId ? [selectedWfId] : null, daysBack)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `release_build_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    }
  }

  const handleExportPdf = async () => {
    try {
      const blob = await exportPdf(selectedWfId ? [selectedWfId] : null, daysBack)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `release_report_${new Date().toISOString().slice(0, 10)}.pdf`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('PDF 导出成功')
    } catch {
      message.error('PDF 导出失败')
    }
  }

  // Workflow discovery state
  const [discoverModalOpen, setDiscoverModalOpen] = useState(false)
  const [discoverLoading, setDiscoverLoading] = useState(false)
  const [discoverForm] = Form.useForm()
  const [discoveredWorkflows, setDiscoveredWorkflows] = useState<Workflow[]>([])

  const handleDiscover = async () => {
    const values = await discoverForm.validateFields(['owner', 'repo'])
    setDiscoverLoading(true)
    try {
      const result = await discoverWorkflows(values.owner, values.repo)
      setDiscoveredWorkflows(result.workflows || [])
      // 刷新主页面工作流列表
      listWorkflows().then((w) => setWorkflows(w))
      if (result.newly_registered > 0) {
        message.success(`新注册 ${result.newly_registered} 个工作流，共 ${result.total_registered || result.workflows?.length || 0} 个`)
      } else {
        message.info(`${result.total_found} 个工作流已存在`)
      }
    } catch {
      message.error('发现工作流失败')
    } finally {
      setDiscoverLoading(false)
    }
  }

  const runsColumns = [
    { title: 'Run ID', dataIndex: 'github_run_id', width: 100, sorter: (a: BuildRun, b: BuildRun) => a.github_run_id - b.github_run_id },
    { title: '状态', dataIndex: 'status', width: 100, render: (s: string) => <Tag color={getStatusColor(s)}>{s}</Tag> },
    { title: '分支', dataIndex: 'branch', width: 150, ellipsis: true },
    { title: '事件', dataIndex: 'event', width: 80 },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '' },
    { title: '排队', dataIndex: 'wait_duration', width: 80, render: (v: number) => fmtDuration(v) },
    { title: '执行', dataIndex: 'exec_duration', width: 80, render: (v: number) => fmtDuration(v) },
    { title: '总耗时', dataIndex: 'wall_duration', width: 80, render: (v: number) => fmtDuration(v) },
    { title: 'Job数', dataIndex: 'num_jobs', width: 60 },
  ]

  if (!configCheck?.github_configured) {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4} style={{ marginBottom: 16 }}>发布件管理</Typography.Title>
        <Alert
          message="GitHub Token 未配置"
          description="请在系统配置中添加 GitHub Token 后才能使用该功能。"
          type="warning"
          showIcon
          icon={<ExclamationCircleOutlined />}
        />
      </div>
    )
  }

  return (
    <Spin spinning={loading}>
      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          发布件管理
        </Title>
        <Text type="secondary">
          镜像构建流程全链路分析 · 三方件版本监控 · AI 可信度评估
        </Text>
      </div>

      <Tabs
        size="large"
        items={[
          {
            key: 'build',
            label: <><BarChartOutlined /> 构建分析</>,
            children: (
              <>
                <Card bordered={false} style={{ marginBottom: 16 }}>
                  <Row gutter={16} align="middle">
                    <Col><Text strong>工作流:</Text></Col>
                    <Col span={6}>
                      <Select placeholder="选择工作流" value={selectedWfId} onChange={setSelectedWfId} style={{ width: '100%' }} showSearch filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}>
                        {workflows.filter((w) => w.enabled).map((w) => (<Option key={w.id} value={w.id} label={w.workflow_name}>{w.workflow_name}</Option>))}
                      </Select>
                    </Col>
                    <Col><Text strong>天数:</Text></Col>
                    <Col span={3}>
                      <Select value={daysBack} onChange={setDaysBack} style={{ width: 100 }}>
                        <Option value={7}>7 天</Option>
                        <Option value={14}>14 天</Option>
                        <Option value={30}>30 天</Option>
                        <Option value={60}>60 天</Option>
                        <Option value={90}>90 天</Option>
                      </Select>
                    </Col>
                    <Col><Text strong>状态:</Text></Col>
                    <Col span={3}>
                      <Select allowClear placeholder="全部" value={statusFilter} onChange={(v) => setStatusFilter(v)} style={{ width: 120 }}>
                        <Option value="success">成功</Option>
                        <Option value="failure">失败</Option>
                        <Option value="cancelled">取消</Option>
                      </Select>
                    </Col>
                    <Col flex="auto" style={{ textAlign: 'right' }}>
                      <Space>
                        <Button icon={<GlobalOutlined />} onClick={() => { discoverForm.setFieldsValue({ owner: configCheck?.github_owner, repo: configCheck?.github_repo }); setDiscoverModalOpen(true); }}>
                          发现工作流
                        </Button>
                        <Button icon={<Spin size="small" spinning={!!activeSyncTask} />} onClick={handleSync} disabled={!selectedWfId}>
                          同步数据{activeSyncTask ? ` (${activeSyncTask.processed_items}/${activeSyncTask.total_items})` : ''}
                        </Button>
                        <Button icon={<DownloadOutlined />} onClick={handleExportExcel} disabled={!overview || !overview.stats.total}>
                          导出 Excel
                        </Button>
                        <Button icon={<FilePdfOutlined />} onClick={handleExportPdf} disabled={!overview || !overview.stats.total}>
                          导出 PDF
                        </Button>
                      </Space>
                    </Col>
                  </Row>
                  {activeSyncTask && (
                    <div style={{ marginTop: 8 }}>
                      <Progress
                        percent={activeSyncTask.progress || 0}
                        status={activeSyncTask.status === 'failed' ? 'exception' : 'active'}
                        format={(percent) => `${activeSyncTask.processed_items}/${activeSyncTask.total_items} (${percent}%)`}
                      />
                    </div>
                  )}
                </Card>

                {!selectedWfId && configCheck?.github_configured && (
                  <Alert message="没有可用的工作流" description="请先点击「同步数据」或在系统配置中添加 GitHub Token 后发现工作流。" type="info" showIcon />
                )}

                {overview && (
                  <>
                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={6}>
                        <KPICard label="总运行次数" value={overview.stats.total} color="#1890ff" sub={`成功 ${overview.stats.successful} / 失败 ${overview.stats.failed}`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="成功率" value={`${overview.stats.success_rate}%`} color={overview.stats.success_rate >= 80 ? '#52c41a' : '#ff4d4f'} sub={`异常慢构建 ${overview.stats.abnormal_count} 次`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="平均执行耗时" value={fmtDuration(overview.stats.avg_exec)} color="#722ed1" sub={`最短 ${fmtDuration(overview.stats.min_exec)} / 最长 ${fmtDuration(overview.stats.max_exec)}`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="耗时波动系数" value={`${overview.stats.cv_exec}%`} color={overview.stats.cv_exec > 50 ? '#ff4d4f' : overview.stats.cv_exec > 30 ? '#faad14' : '#52c41a'} sub={`平均排队 ${fmtDuration(overview.stats.avg_wait)}`} />
                      </Col>
                    </Row>

                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={16}>
                        <Card bordered={false} title="构建耗时趋势（排队 vs 执行）">
                          {overview.timeline?.length ? (
                            <EChartBox
                              height={320}
                              option={{
                                tooltip: { trigger: 'axis', formatter: (p: any[]) => { const item = p[0]; const idx = parseInt(item.dataIndex); return `${item.name}<br/>排队 ${fmtDuration(overview.timeline[idx].wait)}<br/>执行 ${fmtDuration(overview.timeline[idx].exec)}<br/>合计 ${fmtDuration(overview.timeline[idx].wall)}`; } },
                                legend: { data: ['排队等待', '实际执行'], top: 0, textStyle: { fontSize: 11 } },
                                grid: { left: 50, right: 16, top: 36, bottom: 40 },
                                xAxis: { type: 'category', data: overview.timeline.map((d) => `${d.date} #${String(d.run_id).slice(-4)}`), axisLabel: { fontSize: 9, interval: Math.max(0, Math.floor(overview.timeline!.length / 15) - 1), rotate: 30 } },
                                yAxis: { type: 'value', name: '秒' },
                                series: [
                                  { name: '排队等待', type: 'bar', stack: 't', data: overview.timeline.map((d) => d.wait), itemStyle: { color: 'rgba(250,173,20,0.7)' } },
                                  { name: '实际执行', type: 'bar', stack: 't', data: overview.timeline.map((d) => d.exec), itemStyle: { color: 'rgba(24,144,255,0.7)' } },
                                ],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                      <Col span={8}>
                        <Card bordered={false} title="Phase 耗时分布">
                          {overview.phase_data?.length ? (
                            <EChartBox
                              height={320}
                              option={{
                                tooltip: { formatter: (p: any) => `${p.name}: 平均 ${fmtDuration(p.value)}, 共 ${overview.phase_data.find((d) => d.name === p.name)?.count} 次` },
                                series: [{ type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'], data: overview.phase_data.map((d, i) => ({ name: d.name, value: d.total, itemStyle: { color: ['#5b8ff9', '#5ada7a', '#fd9a31', '#e06024', '#9f7aea', '#17a2b8'][i % 6] } })), label: { fontSize: 11 } }],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                    </Row>

                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={12}>
                        <Card bordered={false} title="架构耗时对比">
                          {overview.arch_data?.length ? (
                            <EChartBox
                              height={280}
                              option={{
                                tooltip: { formatter: (p: any) => `${p.name}: 总耗时 ${fmtDuration(p.data.value)}` },
                                grid: { left: 60, right: 20, top: 20, bottom: 30 },
                                xAxis: { type: 'category', data: overview.arch_data.map((d) => d.name) },
                                yAxis: { type: 'value', name: '秒' },
                                series: [{ type: 'bar', data: overview.arch_data.map((d) => d.total), name: '总耗时' }],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                      <Col span={12}>
                        <Card bordered={false} title="Top 15 最耗时 Step">
                          {overview.step_data?.length ? (
                            <EChartBox
                              height={280}
                              option={{
                                tooltip: { formatter: (p: any) => `${p.data.name}: 平均 ${fmtDuration(p.data.value)}, 共 ${p.data.count} 次` },
                                grid: { left: 160, right: 60, top: 10, bottom: 20 },
                                xAxis: { type: 'value', name: '秒' },
                                yAxis: { type: 'category', data: overview.step_data.slice(0, 15).map((d) => d.name), axisLabel: { fontSize: 10 } },
                                series: [{ type: 'bar', data: overview.step_data.slice(0, 15).map((d) => ({ value: d.avg, name: d.name, count: d.count, itemStyle: { color: '#fd9a31' } })) }],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                    </Row>

                    {overview.target_data.length > 0 && (
                      <Card bordered={false} title="Target/OS 组合耗时" style={{ marginBottom: 16 }}>
                        <Table dataSource={overview.target_data} rowKey="name" size="small" pagination={false} columns={[
                          { title: 'Target', dataIndex: 'name', key: 'name' },
                          { title: '次数', dataIndex: 'count', key: 'count', width: 60 },
                          { title: '平均', dataIndex: 'avg', key: 'avg', render: (v: number) => fmtDuration(v) },
                          { title: '最短', dataIndex: 'min', key: 'min', render: (v: number) => fmtDuration(v) },
                          { title: '最长', dataIndex: 'max', key: 'max', render: (v: number) => fmtDuration(v) },
                          { title: '总耗时', dataIndex: 'total', key: 'total', render: (v: number) => fmtDuration(v) },
                        ]} />
                      </Card>
                    )}
                  </>
                )}

                <Card bordered={false} title={`运行记录 (${runsData.total})`}>
                  <Table
                    dataSource={runsData.runs}
                    rowKey="id"
                    size="small"
                    scroll={{ x: 900 }}
                    pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
                    columns={runsColumns}
                    onRow={(record) => ({ style: { cursor: 'pointer' }, onClick: () => navigate(`/release-management/runs/${record.id}`) })}
                  />
                </Card>
              </>
            ),
          },
          { key: 'sync-history', label: <><ReloadOutlined /> 同步记录</>, children: <SyncHistoryTab /> },
          { key: 'comparison', label: <><GlobalOutlined /> 多工作流对比</>, children: <ComparisonTab /> },
          { key: 'third-party', label: <><SafetyOutlined /> 三方件监控</>, children: <ThirdPartyTab selectedIds={selectedWfId ? [selectedWfId] : null} /> },
          { key: 'ai-evaluation', label: <><ExperimentOutlined /> AI 评估</>, children: <AIEvaluationTab selectedIds={selectedWfId ? [selectedWfId] : null} workflows={workflows} /> },
        ]}
      />

      {/* Workflow Discover Modal */}
      <Modal
        title="发现 GitHub 工作流"
        open={discoverModalOpen}
        onCancel={() => setDiscoverModalOpen(false)}
        onOk={handleDiscover}
        confirmLoading={discoverLoading}
        destroyOnClose
        width={640}
      >
        <Form form={discoverForm} layout="vertical">
          <Form.Item name="owner" label="仓库 Owner" rules={[{ required: true, message: '请输入 Owner' }]}>
            <Input placeholder="例如: vllm-project" />
          </Form.Item>
          <Form.Item name="repo" label="仓库名称" rules={[{ required: true, message: '请输入仓库名' }]}>
            <Input placeholder="例如: vllm-ascend" />
          </Form.Item>
        </Form>
        {discoveredWorkflows.length > 0 && (
          <Card size="small" title={`工作流列表 (${discoveredWorkflows.length})`} style={{ marginTop: 16, maxHeight: 360, overflow: 'auto' }}>
            {discoveredWorkflows.map((w, i) => (
              <div key={w.id ?? i} style={{ marginBottom: 8, padding: '4px 0', borderBottom: i < discoveredWorkflows.length - 1 ? '1px solid #f0f0f0' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {w.enabled ? <Tag color="green">已启用</Tag> : <Tag color="default">已禁用</Tag>}
                  <Tag color="blue">{w.workflow_type || 'other'}</Tag>
                  <Text strong style={{ fontSize: 13 }}>{w.workflow_name}</Text>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
                  <Text type="secondary" style={{ fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {w.workflow_path}
                  </Text>
                  {w.last_sync_at && (
                    <Text type="secondary" style={{ fontSize: 11, marginLeft: 'auto', whiteSpace: 'nowrap' }}>
                      同步: {new Date(w.last_sync_at).toLocaleString('zh-CN')}
                    </Text>
                  )}
                </div>
              </div>
            ))}
          </Card>
        )}
      </Modal>
    </Spin>
  )
}
