import { useState, useMemo, useEffect } from 'react'
import {
  Card,
  Table,
  Tag,
  Select,
  Space,
  Typography,
  Button,
  Row,
  Col,
  Statistic,
  Modal,
  Input,
  message,
  Tooltip,
  DatePicker,
  Segmented,
  Drawer,
} from 'antd'
import {
  ReloadOutlined,
  EditOutlined,
  GithubOutlined,
  UserOutlined,
  ExclamationCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import {
  useBatchUpdateFailureStatus,
  useDailyFailures,
  useUpdateFailureStatus,
} from '../hooks/useCI'
import { renderConclusionTag } from '../utils/ciRenderers'
import { formatTimezone } from '../utils/timezone'
import {
  WorkflowDateFilter,
  getDateRangeForMode,
  getInitialCustomDateRange,
  getInitialDateFilterMode,
  type DateFilterMode,
} from '../components/WorkflowDateFilter'
import {
  exportDailyFailures,
  type DailyFailureBatchUpdateRequest,
  type DailyFailureJob,
} from '../services/ci'
import dayjs, { Dayjs } from 'dayjs'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ReferenceDot, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'

const { Search } = Input
const { Text, Title } = Typography
const { TextArea } = Input

function FailureChartTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const item = payload[0]
  const name = item?.payload?.name || item?.name || '-'
  return (
    <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, boxShadow: '0 6px 18px rgba(15, 23, 42, 0.12)', padding: '8px 10px' }}>
      <div style={{ color: '#334155', fontWeight: 600, marginBottom: 3 }}>{name}</div>
      <div style={{ color: '#64748b' }}>失败数量：<span style={{ color: '#111827', fontWeight: 600 }}>{item.value}</span></div>
    </div>
  )
}

type DailyFailureRow = DailyFailureJob & { _date: string }
type FailureDetailFilter = {
  title: string
  field?: keyof DailyFailureJob
  value?: string
  date?: string
}

type DailyFailurePreferences = {
  dateFilterMode?: DateFilterMode
  dateRange?: { start: string | null; end: string | null } | null
  workflowFilter?: string | null
  statusFilter?: string | null
  notesSearch?: string | null
  displayMode?: 'list' | 'analysis'
  breakdownDimension?: 'workflow' | 'owner'
}

const DAILY_FAILURE_PREFERENCES_KEY = 'ci-daily-failure-preferences'
const KNOWN_DAILY_FAILURE_WORKFLOWS = ['Nightly-A2', 'Nightly-A3', 'Nightly-A5', 'Nightly-310P']

function readDailyFailurePreferences(): DailyFailurePreferences {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(DAILY_FAILURE_PREFERENCES_KEY)
    return raw ? JSON.parse(raw) as DailyFailurePreferences : {}
  } catch {
    return {}
  }
}

const STATUS_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  '未处理': { color: '#ff4d4f', icon: <ExclamationCircleOutlined />, label: '未处理' },
  '处理中': { color: '#fa8c16', icon: <SyncOutlined spin />, label: '处理中' },
  '已关闭': { color: '#8c8c8c', icon: <CloseCircleOutlined />, label: '已关闭' },
}

const PROBLEM_CATEGORIES = ['基础设施', '测试框架', '开发代码', '测试用例', '不稳定用例']

const jobColumns = [
  {
    title: '日期',
    dataIndex: '_date',
    key: '_date',
    width: 110,
    fixed: 'left' as const,
    render: (date: string) => <Text strong>{date}</Text>,
  },
  {
    title: 'Workflow',
    dataIndex: 'workflow_name',
    key: 'workflow_name',
    width: 130,
    ellipsis: true,
    render: (text: string) => <Tag color="blue">{text}</Tag>,
  },
  {
    title: '运行结果',
    dataIndex: 'conclusion',
    key: 'conclusion',
    width: 90,
    render: renderConclusionTag,
  },
  {
    title: 'Job',
    dataIndex: 'job_name',
    key: 'job_name',
    width: 220,
    ellipsis: true,
    render: (text: string, record: DailyFailureJob) => (
      <Tooltip title={text}>
        <Space direction="vertical" size={0}>
          <Text strong ellipsis style={{ maxWidth: 200 }}>{text}</Text>
          {record.display_name && (
            <Text style={{ fontSize: 12, color: '#1677ff' }}>{record.display_name}</Text>
          )}
        </Space>
      </Tooltip>
    ),
  },
  {
    title: '责任人',
    dataIndex: 'owner',
    key: 'owner',
    width: 90,
    render: (owner: string | null) => {
      if (!owner) return <Text type="secondary">-</Text>
      return (
        <Space size={4}>
          <UserOutlined style={{ color: '#1677ff' }} />
          <Text>{owner}</Text>
        </Space>
      )
    },
  },
  {
    title: '模型 FO',
    dataIndex: 'model_fo',
    key: 'model_fo',
    width: 80,
    render: (text: string | null) => text || '-',
  },
  {
    title: '问题分类',
    dataIndex: 'problem_category',
    key: 'problem_category',
    width: 100,
    render: (text: string | null) => text ? <Tag>{text}</Tag> : <Text type="secondary">-</Text>,
  },
  {
    title: 'PR',
    dataIndex: 'related_pr',
    key: 'related_pr',
    width: 70,
    render: (text: string | null) => text ? <a href={`https://github.com/vllm-project/vllm-ascend/pull/${text}`} target="_blank" rel="noopener noreferrer">#{text}</a> : <Text type="secondary">-</Text>,
  },
  {
    title: '处理状态',
    key: 'processing_status',
    width: 90,
    render: (_: any, record: DailyFailureJob) => {
      const config = STATUS_CONFIG[record.processing_status] || STATUS_CONFIG['未处理']
      return (
        <Tooltip title={`${record.updated_by || '-'} · ${record.status_updated_at ? formatTimezone(record.status_updated_at, 'MM-DD HH:mm') : '-'}`}>
          <Tag color={config.color} icon={config.icon}>{config.label}</Tag>
        </Tooltip>
      )
    },
  },
  {
    title: '备注',
    dataIndex: 'notes',
    key: 'notes',
    width: 160,
    ellipsis: true,
    render: (notes: string | null) => {
      if (!notes) return <Text type="secondary">-</Text>
      return (
        <Tooltip title={notes}>
          <Text ellipsis style={{ maxWidth: 140 }}>{notes}</Text>
        </Tooltip>
      )
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (_: any, record: DailyFailureJob) => (
      <Space size={4}>
        <Button type="link" size="small" icon={<EditOutlined />}
          onClick={(e) => {
            e.stopPropagation()
            ;(window as any).__openEditDailyFailure?.(record)
          }}>
          更新
        </Button>
        {record.github_job_url && (
          <a href={record.github_job_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
            <Button type="link" size="small" icon={<GithubOutlined />} />
          </a>
        )}
      </Space>
    ),
  },
]

const failureTimingColumns: any[] = [
  {
    title: '处理时间',
    dataIndex: 'processing_time',
    key: 'processing_time',
    width: 155,
    render: (value: string | null) => value ? formatTimezone(value, 'YYYY-MM-DD HH:mm') : <Text type="secondary">-</Text>,
  },
  {
    title: '闭环时间',
    dataIndex: 'closure_time',
    key: 'closure_time',
    width: 155,
    render: (value: string | null) => value ? formatTimezone(value, 'YYYY-MM-DD HH:mm') : <Text type="secondary">-</Text>,
  },
]

function DailyFailureTracking() {
  const [savedPreferences] = useState(readDailyFailurePreferences)
  const initialDateFilterMode = getInitialDateFilterMode(savedPreferences)
  const [dateFilterMode, setDateFilterMode] = useState<DateFilterMode>(initialDateFilterMode)
  const [customDateRange, setCustomDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(() => (
    getInitialCustomDateRange(savedPreferences, initialDateFilterMode)
  ))
  const [currentDay, setCurrentDay] = useState(() => dayjs().format('YYYY-MM-DD'))
  const [workflowFilter, setWorkflowFilter] = useState<string | undefined>(() => (
    savedPreferences.workflowFilter === null ? undefined : savedPreferences.workflowFilter ?? 'Nightly-A3'
  ))
  const [statusFilter, setStatusFilter] = useState<string | undefined>(() => (
    savedPreferences.statusFilter === null ? undefined : savedPreferences.statusFilter
  ))
  const [notesSearch, setNotesSearch] = useState<string | undefined>(() => (
    savedPreferences.notesSearch === null ? undefined : savedPreferences.notesSearch
  ))
  const [editingJob, setEditingJob] = useState<DailyFailureJob | null>(null)
  const [editStatus, setEditStatus] = useState<string>('未处理')
  const [editOwner, setEditOwner] = useState<string>('')
  const [editProblemCategory, setEditProblemCategory] = useState<string>('')
  const [editRelatedPr, setEditRelatedPr] = useState<string>('')
  const [editNotes, setEditNotes] = useState<string>('')
  const [editProcessingTime, setEditProcessingTime] = useState<Dayjs | null>(null)
  const [editClosureTime, setEditClosureTime] = useState<Dayjs | null>(null)
  const [displayMode, setDisplayMode] = useState<'list' | 'analysis'>(() => savedPreferences.displayMode ?? 'list')
  const [breakdownDimension, setBreakdownDimension] = useState<'workflow' | 'owner'>(() => (
    savedPreferences.breakdownDimension === 'owner' ? 'owner' : 'workflow'
  ))
  const [detailFilter, setDetailFilter] = useState<FailureDetailFilter | null>(null)
  const [isExporting, setIsExporting] = useState(false)

  useEffect(() => {
    const timer = window.setInterval(() => {
      const nextDay = dayjs().format('YYYY-MM-DD')
      setCurrentDay((previousDay) => previousDay === nextDay ? previousDay : nextDay)
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const dateRange = useMemo(
    () => dateFilterMode === 'custom' ? customDateRange : getDateRangeForMode(dateFilterMode, dayjs(currentDay)),
    [currentDay, customDateRange, dateFilterMode],
  )

  useEffect(() => {
    window.localStorage.setItem(DAILY_FAILURE_PREFERENCES_KEY, JSON.stringify({
      dateFilterMode,
      dateRange: dateFilterMode === 'custom' && customDateRange
        ? {
            start: customDateRange[0]?.format('YYYY-MM-DD') ?? null,
            end: customDateRange[1]?.format('YYYY-MM-DD') ?? null,
          }
        : null,
      workflowFilter: workflowFilter ?? null,
      statusFilter: statusFilter ?? null,
      notesSearch: notesSearch ?? null,
      displayMode,
      breakdownDimension,
    } satisfies DailyFailurePreferences))
  }, [customDateRange, dateFilterMode, workflowFilter, statusFilter, notesSearch, displayMode, breakdownDimension])

  const startDate = dateRange?.[0]?.format('YYYY-MM-DD')
  const endDate = dateRange?.[1]?.format('YYYY-MM-DD')

  const { data, isLoading, refetch } = useDailyFailures({
    start_date: startDate,
    end_date: endDate,
    workflow_name: workflowFilter,
    processing_status: statusFilter,
    notes_search: notesSearch,
  })

  const handleExport = async () => {
    setIsExporting(true)
    try {
      const blob = await exportDailyFailures({
        start_date: startDate,
        end_date: endDate,
        workflow_name: workflowFilter,
        processing_status: statusFilter,
        notes_search: notesSearch,
      })
      const objectUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = `每日失败追踪_${startDate || '全部'}_${endDate || '全部'}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(objectUrl)
      message.success('导出完成')
    } catch {
      message.error('导出失败')
    } finally {
      setIsExporting(false)
    }
  }

  const updateMutation = useUpdateFailureStatus()
  const batchUpdateMutation = useBatchUpdateFailureStatus()
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [batchEditing, setBatchEditing] = useState(false)
  const [batchStatus, setBatchStatus] = useState<string | undefined>()
  const [batchOwner, setBatchOwner] = useState<string | undefined>()
  const [batchProblemCategory, setBatchProblemCategory] = useState<string | undefined>()
  const [batchRelatedPr, setBatchRelatedPr] = useState<string | undefined>()
  const [batchNotes, setBatchNotes] = useState<string | undefined>()
  const [batchProcessingTime, setBatchProcessingTime] = useState<Dayjs | null | undefined>()
  const [batchClosureTime, setBatchClosureTime] = useState<Dayjs | null | undefined>()

  const openBatchUpdate = () => {
    setBatchStatus(undefined)
    setBatchOwner(undefined)
    setBatchProblemCategory(undefined)
    setBatchRelatedPr(undefined)
    setBatchNotes(undefined)
    setBatchProcessingTime(undefined)
    setBatchClosureTime(undefined)
    setBatchEditing(true)
  }

  const handleBatchUpdate = async () => {
    const update: DailyFailureBatchUpdateRequest = {}
    if (batchStatus !== undefined) update.processing_status = batchStatus
    if (batchOwner !== undefined) update.owner = batchOwner.trim() || null
    if (batchProblemCategory !== undefined) update.problem_category = batchProblemCategory || null
    if (batchRelatedPr !== undefined) update.related_pr = batchRelatedPr || null
    if (batchNotes !== undefined) update.notes = batchNotes || null
    if (batchProcessingTime !== undefined) {
      update.processing_time = batchProcessingTime?.toISOString() || null
    }
    if (batchClosureTime !== undefined) {
      update.closure_time = batchClosureTime?.toISOString() || null
    }
    if (Object.keys(update).length === 0) {
      message.warning('请至少填写一个需要批量更新的字段')
      return
    }
    try {
      const result = await batchUpdateMutation.mutateAsync({
        ids: selectedRowKeys.map(Number),
        data: update,
      })
      message.success(`已更新 ${result.count} 条记录`)
      setBatchEditing(false)
      setSelectedRowKeys([])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '批量更新失败')
    }
  }

  // Expose edit handler to column renderers
  ;(window as any).__openEditDailyFailure = (job: DailyFailureJob) => {
    setEditingJob(job)
    setEditStatus(job.processing_status)
    setEditOwner(job.owner || '')
    setEditProblemCategory(job.problem_category || ''); setEditRelatedPr(job.related_pr || '')
    setEditNotes(job.notes || '')
    setEditProcessingTime(job.processing_time ? dayjs(job.processing_time) : null)
    setEditClosureTime(job.closure_time ? dayjs(job.closure_time) : null)
  }

  const handleSaveStatus = async () => {
    if (!editingJob) return
    try {
      await updateMutation.mutateAsync({
        jobDbId: editingJob.id,
        data: {
          processing_status: editStatus,
          owner: editOwner.trim() || null,
          problem_category: editProblemCategory || null,
          related_pr: editRelatedPr || null,
          notes: editNotes || null,
          processing_time: editProcessingTime?.toISOString() || null,
          closure_time: editClosureTime?.toISOString() || null,
        },
      })
      message.success('失败记录已更新')
      setEditingJob(null)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '更新失败')
    }
  }

  // Aggregate stats across all days
  const totalStats = useMemo(() => {
    if (!data || data.length === 0) return { total: 0, cancelled: 0, unprocessed: 0, processing: 0, fixed: 0, closed: 0 }
    return data.reduce(
      (acc, day) => {
        acc.total += day.stats.total_failed_jobs
        acc.cancelled += day.stats.cancelled || 0
        acc.unprocessed += day.stats.unprocessed
        acc.processing += day.stats.processing
        acc.fixed += day.stats.fixed
        acc.closed += day.stats.closed
        return acc
      },
      { total: 0, cancelled: 0, unprocessed: 0, processing: 0, fixed: 0, closed: 0 }
    )
  }, [data])

  const allJobs = useMemo(() => {
    if (!data) return []
    return data.flatMap(day => day.jobs.map(job => ({ ...job, _date: day.date })))
  }, [data])

  const chartData = useMemo(() => {
    const byDate = new Map((data || []).map(day => [day.date, day]))
    const dates = Array.from(byDate.keys()).sort()
    const start = startDate || dates[0]
    const end = endDate || dates[dates.length - 1]
    const trend: Array<{ date: string; isoDate: string; total: number; unprocessed: number; processing: number; closed: number; dataStatus?: string }> = []
    if (start && end) {
      let cursor = dayjs(start)
      const last = dayjs(end)
      while (cursor.isBefore(last, 'day') || cursor.isSame(last, 'day')) {
        const iso = cursor.format('YYYY-MM-DD')
        const day = byDate.get(iso)
        trend.push(day
          ? { date: cursor.format('MM-DD'), isoDate: iso, total: day.stats.total_failed_jobs, unprocessed: day.stats.unprocessed, processing: day.stats.processing, closed: day.stats.closed }
          : { date: cursor.format('MM-DD'), isoDate: iso, total: 0, unprocessed: 0, processing: 0, closed: 0, dataStatus: 'no-data' })
        cursor = cursor.add(1, 'day')
      }
    }
    const countBy = (values: string[]) => values.reduce<Record<string, number>>((result, value) => {
      const key = value || '未填写'
      result[key] = (result[key] || 0) + 1
      return result
    }, {})
    const field = { workflow: 'workflow_name', owner: 'owner' }[breakdownDimension] as keyof DailyFailureJob
    const breakdown = Object.entries(countBy(allJobs.map(job => String(job[field] || '')))).map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value).slice(0, 10)
    const categories = Object.entries(countBy(allJobs.map(job => job.problem_category || '未分类'))).map(([name, value]) => ({ name, value }))
    const status = [
      { name: '未处理', value: totalStats.unprocessed, color: '#ff4d4f' },
      { name: '处理中', value: totalStats.processing, color: '#fa8c16' },
      { name: '已关闭', value: totalStats.closed, color: '#52c41a' },
    ].filter(item => item.value > 0)
    return { trend, breakdown, categories, status }
  }, [allJobs, breakdownDimension, data, endDate, startDate, totalStats])

  const openChartDetail = (title: string, field?: keyof DailyFailureJob, value?: string, date?: string) => {
    setDetailFilter({ title, field, value, date })
  }
  const openCategoryDetail = (category: string) => openChartDetail(`${category} · 失败详情`, 'problem_category', category)
  const detailJobs: DailyFailureRow[] = detailFilter
    ? allJobs.filter(job => {
        if (detailFilter.date && job._date !== detailFilter.date) return false
        if (!detailFilter.field) return true
        const rawValue = job[detailFilter.field]
        const emptyLabel = detailFilter.field === 'problem_category' ? '未分类' : '未填写'
        return (rawValue || emptyLabel) === detailFilter.value
      })
    : []
  const breakdownField = { workflow: 'workflow_name', owner: 'owner' }[breakdownDimension] as keyof DailyFailureJob

  const workflowOptions = useMemo(() => {
    const workflows = new Set(KNOWN_DAILY_FAILURE_WORKFLOWS)
    data?.forEach(day => day.jobs.forEach(job => workflows.add(job.workflow_name)))
    if (workflowFilter) workflows.add(workflowFilter)
    return Array.from(workflows)
  }, [data, workflowFilter])

  return (
    <div>
      {/* 标题和操作区 */}
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>每日失败追踪</Title>
          <Text type="secondary">按天查看失败 Job，追踪处理进展与责任人</Text>
        </div>
        <Space>
          <WorkflowDateFilter
            mode={dateFilterMode}
            customDateRange={customDateRange}
            onModeChange={(value) => {
              setDateFilterMode(value)
              if (value !== 'custom') setCustomDateRange(null)
            }}
            onRangeChange={(range) => {
              if (!range) {
                setCustomDateRange(null)
                setDateFilterMode('all')
              } else {
                setCustomDateRange(range)
              }
            }}
          />
          <Search
            value={notesSearch}
            placeholder="搜索备注..."
            allowClear
            onSearch={(value) => setNotesSearch(value || undefined)}
            onChange={(e) => { if (!e.target.value) setNotesSearch(undefined) }}
            style={{ width: 180 }}
          />
          <Select
            value={statusFilter}
            onChange={setStatusFilter}
            allowClear
            placeholder="处理状态"
            options={Object.entries(STATUS_CONFIG).map(([v, c]) => ({ label: c.label, value: v }))}
            style={{ width: 120 }}
          />
          <Select
            value={workflowFilter}
            onChange={setWorkflowFilter}
            allowClear
            placeholder="Workflow"
            options={workflowOptions.map(wf => ({ label: wf, value: wf }))}
            style={{ width: 160 }}
          />
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
          <Button
            icon={<DownloadOutlined />}
            loading={isExporting}
            onClick={handleExport}
          >
            导出 CSV
          </Button>
          <Segmented
            value={displayMode}
            onChange={(value) => setDisplayMode(value as 'list' | 'analysis')}
            options={[{ label: '列表', value: 'list' }, { label: '视图', value: 'analysis' }]}
          />
        </Space>
      </div>

      {/* 总览统计 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={4}>
          <Card size="small">
            <Statistic title="失败总数" value={totalStats.total} suffix="个" valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="取消" value={totalStats.cancelled} suffix="条" valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="未处理" value={totalStats.unprocessed} suffix="个" valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="处理中" value={totalStats.processing} suffix="个" valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="已关闭" value={totalStats.closed} suffix="个" valueStyle={{ color: '#8c8c8c' }} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic
              title="处理率"
              value={totalStats.total > 0 ? Math.round(totalStats.closed / totalStats.total * 100) : 0}
              suffix="%"
              valueStyle={{ color: totalStats.total > 0 && totalStats.closed / totalStats.total >= 0.8 ? '#3f8600' : '#cf1322' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 按天分组的折叠面板 */}
      {displayMode === 'analysis' && (
        <Card title="失败趋势与维度分析" style={{ marginBottom: 24 }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={14}>
              <Card size="small" title="每日失败趋势" extra={<Text type="secondary">灰色点：当天无失败记录或尚未同步</Text>}>
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart
                    data={chartData.trend}
                    margin={{ top: 20, right: 20, left: 0, bottom: 0 }}
                    onClick={(event: any) => {
                      const point = event?.activePayload?.[0]?.payload
                      if (point?.isoDate && point.dataStatus !== 'no-data') openChartDetail(`${point.isoDate} · 失败详情`, undefined, undefined, point.isoDate)
                    }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                    <XAxis dataKey="date" /><YAxis allowDecimals={false} /><ChartTooltip /><Legend />
                    <Line type="monotone" dataKey="total" name="失败总数" stroke="#ff4d4f" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                    <Line type="monotone" dataKey="unprocessed" name="未处理" stroke="#fa8c16" strokeWidth={2} dot={{ r: 3 }} />
                    <Line type="monotone" dataKey="closed" name="已关闭" stroke="#52c41a" strokeWidth={2} dot={{ r: 3 }} />
                    {chartData.trend.filter(point => point.dataStatus === 'no-data').map(point => (
                      <ReferenceDot key={point.date} x={point.date} y={0} r={5} fill="#94a3b8" stroke="#fff" label={{ value: '无数据', position: 'top', fill: '#64748b', fontSize: 11 }} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card size="small" title="处理状态分布">
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart><Pie data={chartData.status} dataKey="value" nameKey="name" innerRadius={65} outerRadius={100} paddingAngle={0} stroke="none" labelLine={false} onClick={(entry: any) => {
                    const status = entry?.name || entry?.payload?.name
                    if (status) openChartDetail(`${status} · 失败详情`, 'processing_status', status)
                  }}>
                    {chartData.status.map(item => <Cell key={item.name} fill={item.color} />)}
                  </Pie><ChartTooltip /><Legend /></PieChart>
                </ResponsiveContainer>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title="失败来源排行" extra={<Select size="small" value={breakdownDimension} onChange={setBreakdownDimension} options={[{ label: 'Workflow', value: 'workflow' }, { label: '负责人', value: 'owner' }]} />}>
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={chartData.breakdown} layout="vertical" barCategoryGap="42%" margin={{ left: 12, right: 18, top: 8, bottom: 8 }}>
                    <CartesianGrid horizontal={false} stroke="#eef2f7" />
                    <XAxis type="number" allowDecimals={false} tickLine={false} axisLine={false} />
                    <YAxis dataKey="name" type="category" width={88} tickLine={false} axisLine={false} />
                    <ChartTooltip content={<FailureChartTooltip />} cursor={false} />
                    <Bar dataKey="value" name="失败数量" fill="#3788e8" radius={[0, 3, 3, 0]} barSize={12} activeBar={{ opacity: 0.72 }} cursor="pointer" onClick={(entry: any) => {
                      const value = entry?.payload?.name || entry?.name
                      if (value) openChartDetail(`${value} · 失败详情`, breakdownField, value)
                    }} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card size="small" title="问题分类分布">
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={chartData.categories} barCategoryGap="42%" margin={{ left: 8, right: 18, top: 8, bottom: 8 }} onClick={(event: any) => openCategoryDetail(event?.activeLabel || event?.activePayload?.[0]?.payload?.name)}>
                    <CartesianGrid vertical={false} stroke="#eef2f7" /><XAxis dataKey="name" tickLine={false} axisLine={false} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} /><ChartTooltip content={<FailureChartTooltip />} cursor={false} />
                    <Bar dataKey="value" name="失败数量" fill="#f59e0b" radius={[3, 3, 0, 0]} barSize={16} cursor="pointer" activeBar={{ opacity: 0.72 }} onClick={(entry: any) => openCategoryDetail(entry?.payload?.name || entry?.name)} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </Col>
          </Row>
        </Card>
      )}

      {displayMode === 'list' && <Card>
        <div style={{ marginBottom: 12, padding: '8px 12px', background: '#f8fafc', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 12 }}>
          <Text type={selectedRowKeys.length > 0 ? undefined : 'secondary'}>
            已选 {selectedRowKeys.length} 条
          </Text>
          <Button
            type="primary"
            size="small"
            icon={<EditOutlined />}
            disabled={selectedRowKeys.length === 0}
            onClick={openBatchUpdate}
          >
            批量更新
          </Button>
          {selectedRowKeys.length > 0 && (
            <Button size="small" onClick={() => setSelectedRowKeys([])}>取消选择</Button>
          )}
        </div>
        <Table
          columns={[...jobColumns, ...failureTimingColumns]}
          dataSource={allJobs}
          loading={isLoading}
          rowKey="id"
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
          }}
          pagination={{
            pageSize: 30,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条记录`,
          }}
          scroll={{ x: 1280 }}
          size="middle"
        />
      </Card>}

      <Drawer title={detailFilter?.title || '失败详情'} open={!!detailFilter} onClose={() => setDetailFilter(null)} width={1200}>
        <Table
          rowKey={(record) => `${(record as DailyFailureJob & { _date: string })._date}-${record.id}`}
          dataSource={detailJobs}
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (total) => `共 ${total} 条记录`,
          }}
          columns={[
            { title: '日期', dataIndex: '_date', width: 110 },
            { title: 'Workflow', dataIndex: 'workflow_name' },
            { title: '失败用例', dataIndex: 'display_name', render: (value: string | null, record: DailyFailureJob) => value || record.job_name },
            { title: '结果', dataIndex: 'conclusion', render: renderConclusionTag },
            { title: '负责人', dataIndex: 'owner', render: (value: string | null) => value || '-' },
          ].concat(failureTimingColumns)}
          scroll={{ x: 760 }}
          size="small"
        />
      </Drawer>

      <Modal
        title={<Space><EditOutlined /><span>批量更新（{selectedRowKeys.length} 条）</span></Space>}
        open={batchEditing}
        onOk={handleBatchUpdate}
        onCancel={() => setBatchEditing(false)}
        confirmLoading={batchUpdateMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={480}
      >
        <Text type="secondary">只更新填写的字段，未填写的字段保持原值。</Text>
        <Space direction="vertical" size={16} style={{ width: '100%', marginTop: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text type="secondary">责任人：</Text>
              <Space size={4}>
                <Button type="link" size="small" onClick={() => setBatchOwner(undefined)}>
                  不修改
                </Button>
                <Button type="link" size="small" onClick={() => setBatchOwner('')}>
                  清空责任人
                </Button>
              </Space>
            </div>
            <Input
              value={batchOwner ?? ''}
              onChange={(event) => setBatchOwner(event.target.value)}
              allowClear
              placeholder="不修改；输入责任人账号"
              style={{ marginTop: 4 }}
            />
            {batchOwner !== undefined && (
              <Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
                {batchOwner.trim() ? `将责任人更新为 ${batchOwner.trim()}` : '将清空责任人'}
              </Text>
            )}
          </div>
          <div>
            <Text type="secondary">处理时间：</Text>
            <DatePicker
              value={batchProcessingTime}
              onChange={setBatchProcessingTime}
              showTime
              allowClear
              placeholder="不修改"
              format="YYYY-MM-DD HH:mm:ss"
              style={{ width: '100%', marginTop: 4 }}
            />
          </div>
          <div>
            <Text type="secondary">闭环时间：</Text>
            <DatePicker
              value={batchClosureTime}
              onChange={setBatchClosureTime}
              showTime
              allowClear
              placeholder="不修改"
              format="YYYY-MM-DD HH:mm:ss"
              style={{ width: '100%', marginTop: 4 }}
            />
          </div>
          <div>
            <Text type="secondary">处理状态：</Text>
            <Select
              value={batchStatus}
              onChange={setBatchStatus}
              allowClear
              placeholder="不修改"
              style={{ width: '100%', marginTop: 4 }}
              options={Object.entries(STATUS_CONFIG).map(([value, config]) => ({
                label: <Space>{config.icon}<span>{config.label}</span></Space>,
                value,
              }))}
            />
          </div>
          <div>
            <Text type="secondary">问题分类：</Text>
            <Select
              value={batchProblemCategory}
              onChange={setBatchProblemCategory}
              allowClear
              placeholder="不修改"
              style={{ width: '100%', marginTop: 4 }}
              options={PROBLEM_CATEGORIES.map(category => ({ label: category, value: category }))}
            />
          </div>
          <div>
            <Text type="secondary">关联 PR：</Text>
            <Input
              value={batchRelatedPr}
              onChange={(event) => setBatchRelatedPr(event.target.value)}
              placeholder="不修改；填写如 1234"
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Text type="secondary">备注：</Text>
            <TextArea
              value={batchNotes}
              onChange={(event) => setBatchNotes(event.target.value)}
              placeholder="不修改"
              rows={4}
              style={{ marginTop: 4 }}
            />
          </div>
        </Space>
      </Modal>

      {/* 编辑失败记录弹窗 */}
      <Modal
        title={<Space><EditOutlined /><span>更新失败记录</span></Space>}
        open={!!editingJob}
        onOk={handleSaveStatus}
        onCancel={() => setEditingJob(null)}
        confirmLoading={updateMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={480}
      >
        {editingJob && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <div>
              <Text type="secondary">处理时间：</Text>
              <DatePicker
                value={editProcessingTime}
                onChange={setEditProcessingTime}
                showTime
                allowClear
                format="YYYY-MM-DD HH:mm:ss"
                style={{ width: '100%', marginTop: 4 }}
              />
            </div>
            <div>
              <Text type="secondary">闭环时间：</Text>
              <DatePicker
                value={editClosureTime}
                onChange={setEditClosureTime}
                showTime
                allowClear
                format="YYYY-MM-DD HH:mm:ss"
                style={{ width: '100%', marginTop: 4 }}
              />
            </div>
            <div>
              <Text strong>{editingJob.workflow_name}</Text>
              <Text type="secondary"> / </Text>
              <Text>{editingJob.job_name}</Text>
              {editingJob.display_name && (
                <div><Text style={{ fontSize: 12, color: '#1677ff' }}>{editingJob.display_name}</Text></div>
              )}
            </div>
            <div>
              <Text type="secondary">责任人：</Text>
              <Input
                value={editOwner}
                onChange={(event) => setEditOwner(event.target.value)}
                allowClear
                placeholder="输入责任人账号；留空表示清除"
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Text type="secondary">失败时间：</Text>
              <Text>{editingJob.started_at ? formatTimezone(editingJob.started_at, 'YYYY-MM-DD HH:mm:ss') : '-'}</Text>
            </div>
            <div>
              <Text type="secondary">处理状态：</Text>
              <Select
                value={editStatus}
                onChange={setEditStatus}
                style={{ width: '100%', marginTop: 4 }}
                options={Object.entries(STATUS_CONFIG).map(([value, config]) => ({
                  label: <Space>{config.icon}<span>{config.label}</span></Space>,
                  value,
                }))}
              />
            </div>
            <div>
              <Text type="secondary">问题分类：</Text>
              <Select
                value={editProblemCategory || undefined}
                onChange={(v) => setEditProblemCategory(v)}
                placeholder="选择问题分类"
                allowClear
                style={{ width: '100%', marginTop: 4 }}
                options={PROBLEM_CATEGORIES.map(c => ({ label: c, value: c }))}
              />
            </div>
            <div>
              <Text type="secondary">关联 PR{editProblemCategory === '开发代码' ? ' (必填)' : ''}：</Text>
              <Input
                value={editRelatedPr}
                onChange={(e) => setEditRelatedPr(e.target.value)}
                placeholder="如 1234"
                status={editProblemCategory === '开发代码' && !editRelatedPr ? 'error' : undefined}
                style={{ marginTop: 4 }}
              />
            </div>
            <div>
              <Text type="secondary">备注：</Text>
              <TextArea value={editNotes} onChange={(e) => setEditNotes(e.target.value)}
                placeholder="输入处理备注..." rows={4} style={{ marginTop: 4 }} />
            </div>
          </Space>
        )}
      </Modal>
    </div>
  )
}

export default DailyFailureTracking
