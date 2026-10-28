import { useCallback, useEffect, useRef, useState } from 'react'
import dayjs from 'dayjs'
import { formatTimezone } from '@/utils/timezone'
import * as echarts from 'echarts'
import {
  Alert,
  Button,
  Card,
  Collapse,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  message,
  Progress,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tabs,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  CalendarOutlined,
  CloudUploadOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  FilePdfOutlined,
  GithubOutlined,
  GlobalOutlined,
  PlusOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyOutlined,
  SearchOutlined,
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
  getRunDetail,
  ConfigCheckResult,
  getConfigCheck,
  OverviewData,
  BuildRun,
  BuildGroup,
  SyncTask,
  type Workflow,
} from '../../services/releaseManagement'
import ThirdPartyTab from './ThirdPartyTab'
import AIEvaluationTab from './AIEvaluationTab'
import SyncHistoryTab from './SyncHistoryTab'
import ComparisonTab from './ComparisonTab'
import ScheduleSettingsPanel from './ScheduleSettingsPanel'

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
 * Uses ResizeObserver + setTimeout to ensure correct size after re-init.
 */
function EChartBox({ option, height = 300 }: { option: any; height?: number }) {
  const domRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  // Stable option identity — only rebuild when JSON changes
  const optionRef = useRef(option)
  const needUpdate = JSON.stringify(option) !== JSON.stringify(optionRef.current)
  if (needUpdate) optionRef.current = option

  useEffect(() => {
    if (!domRef.current) return
    chartRef.current?.dispose()
    chartRef.current = echarts.init(domRef.current)
    if (chartRef.current && option) {
      chartRef.current.setOption(option)
    }

    const chart = chartRef.current
    const resize = () => chart?.resize()

    // Multi-frame resize to handle Modal/Tab/Route animation delays
    const timers: number[] = []
    for (let i = 0; i < 5; i++) {
      timers.push(window.setTimeout(resize, (i + 1) * 100))
    }

    // Also use rAF for immediate next-frame resize
    const rafId = requestAnimationFrame(() => requestAnimationFrame(resize))

    const onResize = () => chart?.resize()
    window.addEventListener('resize', onResize)

    // Watch container size changes (e.g., Modal open/close, parent reflow)
    let observer: ResizeObserver | null = null
    try {
      observer = new ResizeObserver(() => chart?.resize())
      observer.observe(domRef.current!)
    } catch { /* ResizeObserver not available */ }

    return () => {
      timers.forEach(clearTimeout)
      cancelAnimationFrame(rafId)
      window.removeEventListener('resize', onResize)
      observer?.disconnect()
      chart?.dispose()
      chartRef.current = null
    }
    // Rebuild when the option actually changes
  }, [JSON.stringify(option)])

  return <div ref={domRef} style={{ width: '100%', height }} />
}

export default function ReleaseManagement() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [selectedWfId, setSelectedWfId] = useState<number | null>(null)
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [daysBack, setDaysBack] = useState(30)
  const [syncMode, setSyncMode] = useState<'full' | 'incremental' | 'range'>('incremental')
  const [singleDate, setSingleDate] = useState<dayjs.Dayjs | null>(null)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [configCheck, setConfigCheck] = useState<ConfigCheckResult | null>(null)
  const [activeSyncTask, setActiveSyncTask] = useState<SyncTask | null>(null)
  const [syncTasks, setSyncTasks] = useState<SyncTask[]>([])
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [runsData, setRunsData] = useState<{ runs: BuildRun[]; total: number }>({ runs: [], total: 0 })
  const [analysisData, setAnalysisData] = useState<{ workflow_id: number; workflow_name: string | null; overview: OverviewData; runs: BuildRun[]; total_runs: number } | null>(null)
  const [runDetailOpen, setRunDetailOpen] = useState(false)
  const [runDetailData, setRunDetailData] = useState<{ run: BuildRun; groups: BuildGroup[] } | null>(null)
  const [runDetailLoading, setRunDetailLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('sync-manage')
  const activeTabRef = useRef(activeTab)
  activeTabRef.current = activeTab

  // Restore analysis data from sessionStorage on page refresh
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem('release-analysis-data')
      if (saved) {
        const data = JSON.parse(saved)
        setAnalysisData(data)
        setSelectedWfId(data.workflow_id)
      }
    } catch { /* ignore */ }
  }, [])

  // Cleanup poll timer + cross-tab events (including new release-view-archive)
  useEffect(() => {
    const handleViewArchive = (e: Event) => {
      const data = (e as CustomEvent).detail
      setSelectedWfId(data.workflow_id)
      setAnalysisData(data)
      setActiveTab('build')
      message.info('已加载同步采集的数据')
      try {
        sessionStorage.setItem('release-analysis-data', JSON.stringify(data))
      } catch { /* ignore */ }
    }
    const handleSyncTaskUpdate = (e: Event) => {
      const task = (e as CustomEvent).detail
      if (task.status === 'running' || task.status === 'pending') {
        setActiveSyncTask(task)
      }
    }
    window.addEventListener('release-view-archive', handleViewArchive)
    window.addEventListener('release-sync-task-update', handleSyncTaskUpdate)
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
      window.removeEventListener('release-view-archive', handleViewArchive)
      window.removeEventListener('release-sync-task-update', handleSyncTaskUpdate)
    }
  }, [])

  // Poll helper defined early so useEffect can reference it
  const startPollSync = (taskId: number) => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    pollTimerRef.current = setInterval(async () => {
      try {
        const task = await getSyncTask(taskId)
        setActiveSyncTask(task)
        if (['completed', 'failed', 'cancelled'].includes(task.status)) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
          // Don't clear immediately — keep showing the final state briefly
          if (task.status === 'completed') {
            message.success('同步完成')
            setSyncTasks(await (await getSyncTasks(null, null, 20, 0)).tasks)
            loadAnalysisFromSync()
            /* Notify child tabs that sync data is fresh */
            window.dispatchEvent(new Event('release-sync-completed'))
          } else if (task.status === 'cancelled') {
            message.info('同步任务已终止')
            setSyncTasks(await (await getSyncTasks(null, null, 20, 0)).tasks)
          } else {
            message.error(`同步失败: ${task.error_message || '未知错误'}`)
            setSyncTasks(await (await getSyncTasks(null, null, 20, 0)).tasks)
          }
          // Clear active task after a delay so UI shows final state
          setTimeout(() => setActiveSyncTask(null), 3000)
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
        const tasksResult = await getSyncTasks(null, null, 20, 0)
        setSyncTasks(tasksResult.tasks)
        // Restore running/pending task so progress bar survives page refresh
        const running = tasksResult.tasks.find((t: SyncTask) => t.status === 'running' || t.status === 'pending')
        if (running) {
          setActiveSyncTask(running)
          startPollSync(running.id)
        }
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

  // NO auto-load: data only shows after sync completes or "查看构建分析" is used
  // When sync completes, load data from API:
  const loadAnalysisFromSync = useCallback(() => {
    if (!selectedWfId) return
    setLoading(true)
    Promise.all([
      getOverview([selectedWfId], daysBack),
      getRuns([selectedWfId], statusFilter, null, 1, 50),
    ])
      .then(([ov, rd]) => {
        const data = {
          workflow_id: selectedWfId,
          workflow_name: workflows.find(w => w.id === selectedWfId)?.workflow_name || null,
          overview: ov,
          runs: rd.runs,
          total_runs: rd.total,
        }
        setAnalysisData(data)
        try {
          sessionStorage.setItem('release-analysis-data', JSON.stringify(data))
        } catch { /* ignore */ }
      })
      .catch(() => message.error('加载数据失败'))
      .finally(() => setLoading(false))
  }, [selectedWfId, daysBack, statusFilter, workflows])

  const hasActiveTask = syncTasks.some((t: SyncTask) =>
    t.workflow_id === selectedWfId && (t.status === 'running' || t.status === 'pending')
  )

  // Derive display data from analysisData (set by sync or 查看构建分析)
  const displayOverview = analysisData?.overview || null
  const displayRuns = analysisData?.runs || []
  const displayTotal = analysisData?.total_runs || 0

  const computeSyncParams = () => {
    if (syncMode === 'full') {
      return { daysBack: 365, sinceDate: undefined, untilDate: undefined }
    }
    if (syncMode === 'incremental') {
      const sd = singleDate ? singleDate.format('YYYY-MM-DD') : undefined
      if (!sd) return { daysBack: daysBack, sinceDate: undefined, untilDate: undefined }
      return { daysBack: Math.max(1, Math.ceil((Date.now() - new Date(sd).getTime()) / 86400000) + 1), sinceDate: sd, untilDate: undefined }
    }
    // range
    if (dateRange && dateRange[0] && dateRange[1]) {
      const sd = dateRange[0].format('YYYY-MM-DD')
      const ed = dateRange[1].format('YYYY-MM-DD')
      return { daysBack: Math.max(1, Math.ceil(Math.abs(dateRange[0].diff(dateRange[1], 'day'))) + 1), sinceDate: sd, untilDate: ed }
    }
    return { daysBack: daysBack, sinceDate: undefined, untilDate: undefined }
  }

  const handleSync = async () => {
    if (!selectedWfId) {
      message.warning('请选择工作流')
      return
    }
    if (syncMode === 'incremental' && !singleDate) {
      message.warning('请选择起始日期')
      return
    }
    if (syncMode === 'range' && (!dateRange || !dateRange[0] || !dateRange[1])) {
      message.warning('请选择完整的时间范围')
      return
    }
    if (hasActiveTask) {
      message.warning('该工作流已有同步任务正在执行中')
      return
    }
    try {
      const { daysBack: db, sinceDate, untilDate } = computeSyncParams()
      const result = await triggerSync(selectedWfId, db, 100, syncMode, sinceDate, untilDate)
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

  const statusLabelMap: Record<string, string> = {
    success: '成功', failure: '失败', cancelled: '已取消', skipped: '已跳过',
    completed: '已完成', in_progress: '进行中', running: '运行中', queued: '排队中',
    action_required: '需操作', timed_out: '超时',
  }

  // Compute stats for color thresholds
  const avgWall = displayOverview?.stats?.avg_wall || 0
  const maxWall = displayOverview?.stats?.max_wall || 0

  const getDurationColor = (val: number, avg: number, maxVal: number) => {
    if (!avg || !maxVal) return undefined
    const threshold = avg * 1.5
    if (val > threshold) return '#ff4d4f'
    return undefined
  }

  const handleViewRunDetail = async (runId: number) => {
    setRunDetailOpen(true)
    setRunDetailLoading(true)
    try {
      const data = await getRunDetail(runId)
      setRunDetailData(data)
    } catch {
      message.error('加载运行详情失败')
    } finally {
      setRunDetailLoading(false)
    }
  }

  const runsColumns = [
    { title: 'Run ID', dataIndex: 'github_run_id', width: 100, sorter: (a: BuildRun, b: BuildRun) => a.github_run_id - b.github_run_id },
    { title: '状态', dataIndex: 'status', width: 100, render: (s: string) => <Tag color={getStatusColor(s)}>{statusLabelMap[s?.toLowerCase()] || s}</Tag> },
    { title: '分支', dataIndex: 'branch', width: 150, ellipsis: true },
    { title: '事件', dataIndex: 'event', width: 80 },
    { title: '创建时间', dataIndex: 'created_at', width: 180, render: (v: string) => formatTimezone(v) },
    { title: '排队', dataIndex: 'wait_duration', width: 90, render: (v: number, r: BuildRun) => <span style={{ color: getDurationColor(r.wait_duration, displayOverview?.stats?.avg_wait || 0, displayOverview?.stats?.max_wait || 0) }}>{fmtDuration(v)}</span> },
    { title: '执行', dataIndex: 'exec_duration', width: 90, render: (v: number, r: BuildRun) => <span style={{ color: getDurationColor(r.exec_duration, avgWall, maxWall) }}>{fmtDuration(v)}</span> },
    { title: '总耗时', dataIndex: 'wall_duration', width: 90, render: (v: number, r: BuildRun) => <span style={{ color: getDurationColor(r.wall_duration, avgWall, maxWall) }}>{fmtDuration(v)}</span> },
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
        activeKey={activeTab}
        onChange={setActiveTab}
        size="large"
        items={[
          /* 1. 同步管理 — 数据仓库同步入口（含子Tab） */
          {
            key: 'sync-manage',
            label: <><CloudUploadOutlined /> 同步管理</>,
            children: (
              <Card size="small" bordered={false} style={{ borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.06)' }} bodyStyle={{ padding: 0 }}>
                <Tabs
                  tabPosition="left"
                  destroyInactiveTabPane
                  items={[
                    {
                      key: 'manual',
                      label: <span><CloudUploadOutlined /> 手动同步</span>,
                      children: (
                        <div style={{ padding: '16px 24px' }}>
                          <Row gutter={[16, 16]}>
                            <Col xs={24} md={8}>
                              <Card size="small" title={
                                <><CloudUploadOutlined style={{ marginRight: 6, color: '#1890ff' }} />同步模式</>
                              }
                              style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #f0f0f0' }}
                              bodyStyle={{ padding: '16px 20px' }}>
                                <Radio.Group
                                  value={syncMode}
                                  onChange={(e) => { if (!hasActiveTask) setSyncMode(e.target.value) }}
                                  disabled={hasActiveTask}
                                  optionType="button"
                                  buttonStyle="solid"
                                  size="middle"
                                >
                                  <Radio.Button value="full">全量</Radio.Button>
                                  <Radio.Button value="incremental">增量</Radio.Button>
                                  <Radio.Button value="range">范围</Radio.Button>
                                </Radio.Group>
                              </Card>
                            </Col>
                            <Col xs={24} md={8}>
                              <Card size="small" title={
                                <><CalendarOutlined style={{ marginRight: 6, color: '#52c41a' }} />{syncMode === 'incremental' ? '起始日期' : syncMode === 'full' ? '说明' : '时间范围'}</>
                              }
                              style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #f0f0f0' }}
                              bodyStyle={{ padding: '16px 20px' }}>
                                {syncMode === 'full' ? (
                                  <Alert message="拉取该工作流的全部历史构建记录，耗时较长" type="info" showIcon style={{ fontSize: 13 }} />
                                ) : syncMode === 'incremental' ? (
                                  <DatePicker
                                    value={singleDate}
                                    onChange={(d) => setSingleDate(d)}
                                    placeholder="选择起始日期"
                                    style={{ width: 220 }}
                                    disabledDate={(current) => current && current && current > dayjs().endOf('day')}
                                  />
                                ) : (
                                  <DatePicker.RangePicker
                                    value={dateRange}
                                    onChange={(dates) => {
                                      if (dates && dates.length === 2) {
                                        setDateRange(dates as [dayjs.Dayjs, dayjs.Dayjs])
                                      } else {
                                        setDateRange(null)
                                      }
                                    }}
                                    placeholder={['起始日期', '截止日期']}
                                    style={{ width: 340 }}
                                  />
                                )}
                              </Card>
                            </Col>
                            <Col xs={24} md={8}>
                              <Card size="small" title={
                                <><GithubOutlined style={{ marginRight: 6, color: '#722ed1' }} />目标工作流</>
                              }
                              extra={
                                <Space>
                                  {workflows.length > 1 ? (
                                    <Button icon={<GlobalOutlined />} onClick={() => { discoverForm.setFieldsValue({ owner: configCheck?.github_owner, repo: configCheck?.github_repo }); setDiscoverModalOpen(true); }}>
                                      发现
                                    </Button>
                                  ) : (
                                    <Button icon={<GlobalOutlined />} type="link" onClick={() => { discoverForm.setFieldsValue({ owner: configCheck?.github_owner, repo: configCheck?.github_repo }); setDiscoverModalOpen(true); }}>
                                      发现工作流
                                    </Button>
                                  )}
                                  <Button
                                    type="primary"
                                    icon={<CloudUploadOutlined />}
                                    loading={activeSyncTask !== null}
                                    disabled={!selectedWfId || hasActiveTask}
                                    onClick={handleSync}
                                  >
                                    {activeSyncTask ? '同步中' : '开始'}
                                  </Button>
                                </Space>
                              }
                              style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #f0f0f0' }}
                              bodyStyle={{ padding: '16px 20px' }}>
                                <Select
                                  placeholder="选择工作流（留空则同步全部）"
                                  value={selectedWfId}
                                  onChange={setSelectedWfId}
                                  style={{ width: '100%' }}
                                  allowClear
                                  showSearch
                                  filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
                                  suffixIcon={<SearchOutlined />}
                                >
                                  {workflows.filter((w) => w.enabled).map((w) => (<Option key={w.id} value={w.id} label={w.workflow_name}>{w.workflow_name}</Option>))}
                                </Select>
                              </Card>
                            </Col>
                          </Row>

                          {activeSyncTask && (
                            <Card size="small" style={{ background: '#fff', borderRadius: 12, boxShadow: '0 1px 4px rgba(0,0,0,0.06)', border: '1px solid #f0f0f0', marginTop: 16 }}
                              bodyStyle={{ padding: '16px 20px' }}>
                              <Space style={{ marginBottom: 6 }}>
                                {(activeSyncTask.status === 'running' || activeSyncTask.status === 'pending')
                                  ? <Tag color="processing"><ClockCircleOutlined /> 执行中</Tag>
                                  : <Tag color="default"><ClockCircleOutlined /> 等待中</Tag>}
                                <Text type="secondary" style={{ fontSize: 12 }}>
                                  {activeSyncTask.processed_items}/{activeSyncTask.total_items} 构建记录
                                </Text>
                              </Space>
                              <Progress
                                percent={activeSyncTask.progress || 0}
                                status={activeSyncTask.status === 'failed' ? 'exception' : 'active'}
                                strokeColor={{ '0%': '#108ee9', '100%': '#87d068' }}
                                size="small"
                              />
                            </Card>
                          )}
                          {!selectedWfId && configCheck?.github_configured && (
                            <Alert message="请先选择工作流" description="选择一个工作流后点击「开始」同步采集构建记录。" type="info" showIcon style={{ marginTop: 16 }} />
                          )}
                        </div>
                      ),
                    },
                    {
                      key: 'history',
                      label: <span><ReloadOutlined /> 同步记录</span>,
                      children: <SyncHistoryTab />,
                    },
                    {
                      key: 'schedule',
                      label: <span><ClockCircleOutlined /> 定时设置</span>,
                      children: (
                        <div style={{ padding: '0 24px' }}>
                          <ScheduleSettingsPanel
                            workflows={workflows.filter((w) => w.enabled).map((w) => ({ id: w.id, workflow_name: w.workflow_name }))}
                          />
                        </div>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          /* 2. 构建分析 — 纯查询 */
          {
            key: 'build',
            label: <><BarChartOutlined /> 构建分析</>,
            children: (
              <>
                <Card bordered={false} style={{ marginBottom: 16 }}>
                  <Space wrap size="middle" align="center" style={{ marginBottom: 8 }}>
                    <Text strong>工作流:</Text>
                    <Select placeholder="选择工作流" value={selectedWfId} onChange={setSelectedWfId} style={{ width: 260 }} showSearch filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}>
                      {workflows.filter((w) => w.enabled).map((w) => (<Option key={w.id} value={w.id} label={w.workflow_name}>{w.workflow_name}</Option>))}
                    </Select>
                    <Text strong>日期范围:</Text>
                    <DatePicker.RangePicker
                      value={dateRange}
                      onChange={(dates) => {
                        if (dates && dates.length === 2) {
                          setDateRange(dates as [dayjs.Dayjs, dayjs.Dayjs])
                        } else {
                          setDateRange(null)
                        }
                      }}
                      presets={[
                        { label: '最近7天', value: [dayjs().subtract(7, 'day'), dayjs()] },
                        { label: '最近14天', value: [dayjs().subtract(14, 'day'), dayjs()] },
                        { label: '最近30天', value: [dayjs().subtract(30, 'day'), dayjs()] },
                        { label: '最近60天', value: [dayjs().subtract(60, 'day'), dayjs()] },
                        { label: '最近90天', value: [dayjs().subtract(90, 'day'), dayjs()] },
                      ]}
                    />
                    {!dateRange && (
                      <Select value={daysBack} onChange={setDaysBack} style={{ width: 80 }}>
                        <Option value={7}>7 天</Option>
                        <Option value={14}>14 天</Option>
                        <Option value={30}>30 天</Option>
                        <Option value={60}>60 天</Option>
                        <Option value={90}>90 天</Option>
                      </Select>
                    )}
                    <Text strong>状态:</Text>
                    <Select allowClear placeholder="全部" value={statusFilter} onChange={(v) => setStatusFilter(v)} style={{ width: 120 }}>
                      <Option value="success">成功</Option>
                      <Option value="failure">失败</Option>
                      <Option value="cancelled">取消</Option>
                    </Select>
                    <Button type="primary" icon={<SearchOutlined />} onClick={loadAnalysisFromSync}>
                      查询
                    </Button>
                    <Button icon={<DownloadOutlined />} onClick={handleExportExcel} disabled={!displayOverview || !displayOverview.stats.total}>
                      导出 Excel
                    </Button>
                    <Button icon={<FilePdfOutlined />} onClick={handleExportPdf} disabled={!displayOverview || !displayOverview.stats.total}>
                      导出 PDF
                    </Button>
                  </Space>
                </Card>

                {displayOverview && (
                  <>
                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={6}>
                        <KPICard label="总运行次数" value={displayOverview.stats.total} color="#1890ff" sub={`成功 ${displayOverview.stats.successful} / 失败 ${displayOverview.stats.failed}`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="成功率" value={`${displayOverview.stats.success_rate}%`} color={displayOverview.stats.success_rate >= 80 ? '#52c41a' : '#ff4d4f'} sub={`异常慢构建 ${displayOverview.stats.abnormal_count} 次`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="平均执行耗时" value={fmtDuration(displayOverview.stats.avg_exec)} color="#722ed1" sub={`最短 ${fmtDuration(displayOverview.stats.min_exec)} / 最长 ${fmtDuration(displayOverview.stats.max_exec)}`} />
                      </Col>
                      <Col span={6}>
                        <KPICard label="耗时波动系数" value={`${displayOverview.stats.cv_exec}%`} color={displayOverview.stats.cv_exec > 50 ? '#ff4d4f' : displayOverview.stats.cv_exec > 30 ? '#faad14' : '#52c41a'} sub={`平均排队 ${fmtDuration(displayOverview.stats.avg_wait)}`} />
                      </Col>
                    </Row>

                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={16}>
                        <Card bordered={false} title="构建耗时趋势（排队 vs 执行）">
                          {displayOverview.timeline?.length ? (
                            <EChartBox
                              height={400}
                              option={{
                                tooltip: { trigger: 'axis', formatter: (p: any[]) => { const item = p[0]; const idx = parseInt(item.dataIndex); return `${item.name}<br/>排队 ${fmtDuration(displayOverview.timeline[idx].wait)}<br/>执行 ${fmtDuration(displayOverview.timeline[idx].exec)}<br/>合计 ${fmtDuration(displayOverview.timeline[idx].wall)}`; } },
                                legend: { data: ['排队等待', '实际执行'], top: 0, textStyle: { fontSize: 11 } },
                                grid: { left: 50, right: 16, top: 36, bottom: 40 },
                                xAxis: { type: 'category', data: displayOverview.timeline.map((d) => `${d.date_full || d.date} #${String(d.run_id).slice(-4)}`), axisLabel: { fontSize: 9, interval: Math.max(0, Math.floor(displayOverview.timeline!.length / 15) - 1), rotate: 30 } },
                                yAxis: { type: 'value', name: '秒' },
                                series: [
                                  { name: '排队等待', type: 'bar', stack: 't', data: displayOverview.timeline.map((d) => d.wait), itemStyle: { color: 'rgba(250,173,20,0.7)' } },
                                  { name: '实际执行', type: 'bar', stack: 't', data: displayOverview.timeline.map((d) => d.exec), itemStyle: { color: 'rgba(24,144,255,0.7)' } },
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
                          {displayOverview.phase_data?.length ? (
                            <div>
                              <EChartBox
                                height={200}
                                option={{
                                  tooltip: { formatter: (p: any) => { const pd = displayOverview.phase_data.find((d) => d.name === p.name); const totalAll = displayOverview.phase_data.reduce((s, d) => s + d.total, 0); return `${p.name}<br/>总耗时: ${fmtDuration(p.value)}<br/>占比: ${(p.value / totalAll * 100).toFixed(1)}%<br/>次数: ${pd?.count || 0}<br/>平均: ${fmtDuration(pd?.avg || 0)}<br/>最长: ${fmtDuration(pd?.max || 0)}`; } },
                                  series: [{ type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'], data: displayOverview.phase_data.map((d, i) => ({ name: d.name, value: d.total, itemStyle: { color: ['#5b8ff9', '#5ada7a', '#fd9a31', '#e06024', '#9f7aea', '#17a2b8'][i % 6] } })), label: { formatter: '{b} {d}%', fontSize: 11 } }],
                                }}
                              />
                              <Table dataSource={displayOverview.phase_data} rowKey="name" size="small" pagination={false} bordered
                                style={{ marginTop: 8 }}
                                columns={[
                                  { title: 'Phase', dataIndex: 'name', key: 'name', render: (v: string) => <Text strong style={{ fontSize: 12 }}>{v}</Text> },
                                  { title: '次数', dataIndex: 'count', key: 'count', width: 50, align: 'center' as const, render: (v: number) => <Text style={{ fontSize: 12 }}>{v}</Text> },
                                  { title: '平均', dataIndex: 'avg', key: 'avg', width: 70, render: (v: number) => <Text style={{ fontSize: 12 }}>{fmtDuration(v)}</Text> },
                                  { title: '最长', dataIndex: 'max', key: 'max', width: 70, render: (v: number) => <Text style={{ fontSize: 12 }}>{fmtDuration(v)}</Text> },
                                ]} />
                            </div>
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                    </Row>

                    <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                      <Col span={12}>
                        <Card bordered={false} title="架构耗时对比">
                          {displayOverview.arch_data?.length ? (
                            <EChartBox
                              height={280}
                              option={{
                                tooltip: { formatter: (p: any) => `${p.name}: 总耗时 ${fmtDuration(p.data.value)}` },
                                grid: { left: 60, right: 20, top: 20, bottom: 30 },
                                xAxis: { type: 'category', data: displayOverview.arch_data.map((d) => d.name) },
                                yAxis: { type: 'value', name: '秒' },
                                series: [{ type: 'bar', data: displayOverview.arch_data.map((d) => d.total), name: '总耗时' }],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                      <Col span={12}>
                        <Card bordered={false} title="Top 15 最耗时 Step">
                          {displayOverview.step_data?.length ? (
                            <EChartBox
                              height={280}
                              option={{
                                tooltip: { formatter: (p: any) => `${p.data.name}: 平均 ${fmtDuration(p.data.value)}, 共 ${p.data.count} 次` },
                                grid: { left: 160, right: 60, top: 10, bottom: 20 },
                                xAxis: { type: 'value', name: '秒' },
                                yAxis: { type: 'category', data: displayOverview.step_data.slice(0, 15).map((d) => d.name), axisLabel: { fontSize: 10 } },
                                series: [{ type: 'bar', data: displayOverview.step_data.slice(0, 15).map((d) => ({ value: d.avg, name: d.name, count: d.count, itemStyle: { color: '#fd9a31' } })) }],
                              }}
                            />
                          ) : (
                            <div style={{ textAlign: 'center', padding: 40, color: '#bfbfbf' }}>暂无数据</div>
                          )}
                        </Card>
                      </Col>
                    </Row>

                    {displayOverview.target_data.length > 0 && (
                      <Card bordered={false} title="Target/OS 组合耗时" style={{ marginBottom: 16 }}>
                        <Table dataSource={displayOverview.target_data} rowKey="name" size="small" pagination={false} columns={[
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

                {displayOverview && (
                  <Card bordered={false} title={`运行记录 (${displayTotal})`}>
                    <Table
                      dataSource={displayRuns}
                      rowKey="id"
                      size="small"
                      scroll={{ x: 900 }}
                      pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
                      columns={runsColumns}
                      onRow={(record) => ({ style: { cursor: 'pointer' }, onClick: () => handleViewRunDetail(record.id) })}
                    />
                  </Card>
                )}
              </>
            ),
          },
          { key: 'comparison', label: <><GlobalOutlined /> 多工作流对比</>, children: <ComparisonTab /> },
          { key: 'third-party', label: <><SafetyOutlined /> 三方件监控</>, children: <ThirdPartyTab selectedIds={selectedWfId ? [selectedWfId] : null} /> },
          { key: 'ai-evaluation', label: <><ExperimentOutlined /> AI 评估</>, children: <AIEvaluationTab selectedIds={selectedWfId ? [selectedWfId] : null} workflows={workflows} /> },
        ]}
      />

      {/* Run Detail Modal */}
      <Modal
        title={
          <Space>
            <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setRunDetailOpen(false)} />
            构建运行详情 #{runDetailData?.run?.id || runDetailData?.run?.github_run_id}
          </Space>
        }
        open={runDetailOpen}
        onCancel={() => setRunDetailOpen(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        <Spin spinning={runDetailLoading}>
          {runDetailData?.run && (
            <>
              <Descriptions column={{ xs: 1, sm: 2, md: 4 }} bordered size="small" style={{ marginBottom: 16 }}>
                <Descriptions.Item label="状态">
                  <Tag color={getStatusColor(runDetailData.run.status)}>
                    {statusLabelMap[runDetailData.run.status?.toLowerCase()] || runDetailData.run.status}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="分支">{runDetailData.run.branch}</Descriptions.Item>
                <Descriptions.Item label="事件">{runDetailData.run.event}</Descriptions.Item>
                <Descriptions.Item label="Commit">{runDetailData.run.commit_sha?.slice(0, 8)}</Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {formatTimezone(runDetailData.run.created_at)}
                </Descriptions.Item>
                <Descriptions.Item label="开始时间">
                  {formatTimezone(runDetailData.run.started_at)}
                </Descriptions.Item>
                <Descriptions.Item label="结束时间">
                  {formatTimezone(runDetailData.run.completed_at)}
                </Descriptions.Item>
              </Descriptions>

              <Space size="middle" style={{ width: '100%', justifyContent: 'space-around', marginBottom: 24 }}>
                {[
                  { label: '排队时间', value: fmtDuration(runDetailData.run.wait_duration) },
                  { label: '执行时间', value: fmtDuration(runDetailData.run.exec_duration) },
                  { label: '总耗时', value: fmtDuration(runDetailData.run.wall_duration) },
                  { label: 'Job 数', value: runDetailData.run.num_jobs },
                ].map((c) => (
                  <Card key={c.label} bordered={false} style={{ flex: 1, textAlign: 'center', borderRadius: 8, background: '#fafafa' }}>
                    <div style={{ fontSize: 22, fontWeight: 700, color: '#1890ff' }}>{c.value}</div>
                    <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>{c.label}</div>
                  </Card>
                ))}
              </Space>

              {runDetailData.run.conclusion_summary && Object.keys(runDetailData.run.conclusion_summary).length > 0 && (
                <Card bordered={false} title="结果汇总" style={{ marginBottom: 16 }}>
                  <Space wrap>
                    {Object.entries(runDetailData.run.conclusion_summary).map(([key, val]) => (
                      <Tag key={key} color={getStatusColor(key)}>{key}: {val}</Tag>
                    ))}
                  </Space>
                </Card>
              )}

              {runDetailData.groups.length > 0 ? (
                <Card bordered={false} title={`Jobs (${runDetailData.groups.length})`}>
                  <Collapse
                    items={runDetailData.groups.map((g) => ({
                      key: `${g.id}`,
                      label: (
                        <Space>
                          <Text strong>{g.group_name}</Text>
                          {g.sub_job_name && <><Text type="secondary">·</Text><Text>{g.sub_job_name}</Text></>}
                          <Tag color="blue">{g.phase}</Tag>
                          <Tag color={getStatusColor(g.status)}>
                            {statusLabelMap[g.status?.toLowerCase()] || g.status}
                          </Tag>
                          <Text type="secondary">{fmtDuration(g.total_duration)}</Text>
                          {g.architecture && <Tag>{g.architecture}</Tag>}
                          {g.target && <Text type="secondary">{g.target}</Text>}
                        </Space>
                      ),
                      children: (
                        <Table rowKey="number" size="small" dataSource={g.steps} pagination={false} scroll={{ y: 320 }} columns={[
                          { title: '步骤名称', dataIndex: 'name', key: 'name', ellipsis: true },
                          { title: '编号', dataIndex: 'number', key: 'number', width: 80, align: 'center' as const },
                          { title: '状态', dataIndex: 'status', key: 'status', width: 100, render: (s: string) => <Tag color={getStatusColor(s)}>{statusLabelMap[s?.toLowerCase()] || s}</Tag> },
                          { title: '耗时', dataIndex: 'duration', key: 'duration', width: 100, render: (v: number) => fmtDuration(v) },
                        ]} />
                      ),
                    }))}
                    defaultActiveKey={[String(runDetailData.groups[0]?.id)]}
                  />
                </Card>
              ) : !runDetailLoading && (
                <Text type="secondary">暂无 Job 数据</Text>
              )}
            </>
          )}
        </Spin>
      </Modal>

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
                      同步: {formatTimezone(w.last_sync_at)}
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
