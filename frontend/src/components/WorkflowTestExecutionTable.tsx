import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { EyeOutlined, GithubOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs, { Dayjs } from 'dayjs'
import { useRuns, useWorkflows } from '../hooks/useCI'
import type { CIResult, CIRunJobStats } from '../services/ci'
import {
  formatDuration,
  renderRunJobStats,
  renderHardwareTag,
  renderStatusTag,
} from '../utils/ciRenderers'
import { formatTimezone, fromTimezoneNow } from '../utils/timezone'
import {
  WorkflowDateFilter,
  getDateRangeForMode,
  getInitialCustomDateRange,
  getInitialDateFilterMode,
  type DateFilterMode,
} from './WorkflowDateFilter'

const { Text } = Typography

interface WorkflowTestExecutionTableProps {
  enabled: boolean
}

const STATUS_FILTERS = [
  { text: '已完成', value: 'completed' },
  { text: '进行中', value: 'in_progress' },
  { text: '等待中', value: 'queued' },
]

const RESULT_FILTERS = [
  { text: '成功', value: 'success' },
  { text: '失败', value: 'failure' },
  { text: '取消', value: 'cancelled' },
  { text: '跳过', value: 'skipped' },
]

const EVENT_FILTERS = [
  { text: '定时 schedule', value: 'schedule' },
  { text: '手动 workflow_dispatch', value: 'workflow_dispatch' },
  { text: '推送 push', value: 'push' },
  { text: 'Pull Request', value: 'pull_request' },
]

const EVENT_LABELS: Record<string, string> = {
  schedule: '定时 schedule',
  workflow_dispatch: '手动 workflow_dispatch',
  push: '推送 push',
  pull_request: 'Pull Request',
}

const WORKFLOW_EXECUTION_PREFERENCES_KEY = 'ci-workflow-execution-preferences'

// 偏好版本 v2：初始渲染曾把默认值 recent_day 写入存储，导致看板
// "只看今天"而夜间数据不可见。迁移时把无版本标记的 recent_day 重置为 all；
// 用户再次手动选择后按新值记住。
const PREFERENCES_VERSION = 2

type WorkflowExecutionPreferences = {
  v?: number
  workflowFilter?: string[]
  selectedWorkflow?: string | null
  hardwareFilter?: string[]
  statusFilter?: string[]
  resultFilter?: string[]
  eventFilter?: string[]
  logSearch?: string
  dateFilterMode?: DateFilterMode
  dateRange?: { start: string | null; end: string | null } | null
}

function readPreferences(): WorkflowExecutionPreferences {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(WORKFLOW_EXECUTION_PREFERENCES_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as WorkflowExecutionPreferences
    if (parsed.v !== PREFERENCES_VERSION) {
      if (parsed.dateFilterMode === 'recent_day' && !parsed.dateRange?.start && !parsed.dateRange?.end) {
        parsed.dateFilterMode = 'all'
      }
      parsed.v = PREFERENCES_VERSION
    }
    return parsed
  } catch {
    return {}
  }
}

const toRunStatus = (run: CIResult) => run.status || (run.completed_at ? 'completed' : 'in_progress')

// Workflow 的所属日期统一按开始时间计算。
const getRunBelongingTime = (run: CIResult) => run.started_at

function WorkflowTestExecutionTable({ enabled }: WorkflowTestExecutionTableProps) {
  const navigate = useNavigate()
  const [savedPreferences] = useState(readPreferences)
  const initialWorkflow = savedPreferences.selectedWorkflow ?? savedPreferences.workflowFilter?.[0]
  const [workflowFilter, setWorkflowFilter] = useState<string[]>(() => savedPreferences.workflowFilter ?? [])
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | undefined>(() => initialWorkflow || undefined)
  const [hardwareFilter, setHardwareFilter] = useState<string[]>(() => savedPreferences.hardwareFilter ?? [])
  const [statusFilter, setStatusFilter] = useState<string[]>(() => savedPreferences.statusFilter ?? [])
  const [resultFilter, setResultFilter] = useState<string[]>(() => savedPreferences.resultFilter ?? [])
  const [eventFilter, setEventFilter] = useState<string[]>(() => savedPreferences.eventFilter ?? [])
  const [logSearch, setLogSearch] = useState(() => savedPreferences.logSearch ?? '')
  const initialDateFilterMode = getInitialDateFilterMode(savedPreferences)
  const [dateFilterMode, setDateFilterMode] = useState<DateFilterMode>(initialDateFilterMode)
  const [customDateRange, setCustomDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(() => (
    getInitialCustomDateRange(savedPreferences, initialDateFilterMode)
  ))
  const [currentDay, setCurrentDay] = useState(() => dayjs().format('YYYY-MM-DD'))

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
    window.localStorage.setItem(WORKFLOW_EXECUTION_PREFERENCES_KEY, JSON.stringify({
      v: PREFERENCES_VERSION,
      workflowFilter,
      selectedWorkflow: selectedWorkflow ?? null,
      hardwareFilter,
      statusFilter,
      resultFilter,
      eventFilter,
      logSearch,
      dateFilterMode,
      dateRange: dateFilterMode === 'custom' && customDateRange ? {
        start: customDateRange[0]?.format('YYYY-MM-DD') ?? null,
        end: customDateRange[1]?.format('YYYY-MM-DD') ?? null,
      } : null,
    } satisfies WorkflowExecutionPreferences))
  }, [customDateRange, dateFilterMode, eventFilter, hardwareFilter, logSearch, resultFilter, selectedWorkflow, statusFilter, workflowFilter])

  const workflowsQuery = useWorkflows()
  const runsQuery = useRuns({ workflow_name: selectedWorkflow, limit: 500 }, enabled)
  const runs = runsQuery.data || []

  const workflowOptions = useMemo(() => (
    Array.from(new Set([...(workflowsQuery.data || []), ...runs.map((run) => run.workflow_name)].filter(Boolean)))
      .map((workflowName) => ({ text: workflowName, value: workflowName }))
  ), [runs, workflowsQuery.data])

  const hardwareOptions = useMemo(() => (
    Array.from(new Set(runs.map((run) => run.hardware).filter(Boolean)))
      .map((hardware) => ({ text: hardware as string, value: hardware as string }))
  ), [runs])

  const visibleRuns = useMemo(() => {
    const keyword = logSearch.trim().toLowerCase()
    return runs.filter((run) => {
      const status = toRunStatus(run)
      if (workflowFilter.length > 0 && !workflowFilter.includes(run.workflow_name)) return false
      if (hardwareFilter.length > 0 && !hardwareFilter.includes(run.hardware || '')) return false
      if (statusFilter.length > 0 && !statusFilter.includes(status)) return false
      if (resultFilter.length > 0 && !resultFilter.includes(run.conclusion || '')) return false
      if (eventFilter.length > 0 && !eventFilter.includes(run.event || '')) return false
      const belongingTime = getRunBelongingTime(run)
      if (dateRange && belongingTime) {
        const [start, end] = dateRange
        const belongingDate = dayjs(formatTimezone(belongingTime, 'YYYY-MM-DD'))
        if (start && belongingDate.isBefore(start.startOf('day'))) return false
        if (end && belongingDate.isAfter(end.endOf('day'))) return false
      } else if (dateRange && !belongingTime) return false
      if (!keyword) return true
      return [run.workflow_name, run.run_id.toString(), run.run_number?.toString(), run.event, run.branch, run.head_sha]
        .some((value) => value != null && String(value).toLowerCase().includes(keyword))
    })
  }, [dateRange, eventFilter, hardwareFilter, logSearch, resultFilter, runs, statusFilter, workflowFilter])

  const actionColumn = {
      title: '操作',
      key: 'action',
      width: 150,
      fixed: 'left' as const,
      render: (_: unknown, record: CIResult) => (
        <Space>
          <Button type="link" icon={<EyeOutlined />} onClick={() => navigate(`/ci/runs/${record.run_id}`)} style={{ padding: 0 }}>
            查看详情
          </Button>
          {record.github_html_url && <a href={record.github_html_url} target="_blank" rel="noopener noreferrer"><GithubOutlined /></a>}
        </Space>
      ),
    }

  const columns = [
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 190,
      filters: workflowOptions,
      filteredValue: workflowFilter,
      onFilter: (value: boolean | React.Key, record: CIResult) => record.workflow_name === value,
      render: (value: string) => <Tag color="blue">{value || '-'}</Tag>,
    },
    actionColumn,
    {
      title: '触发方式',
      dataIndex: 'event',
      key: 'event',
      width: 190,
      filters: EVENT_FILTERS,
      filteredValue: eventFilter,
      onFilter: (value: boolean | React.Key, record: CIResult) => record.event === value,
      render: (value: string | null) => <Tag color={value === 'schedule' ? 'purple' : 'default'}>{value ? (EVENT_LABELS[value] || value) : '-'}</Tag>,
    },
    {
      title: '硬件',
      dataIndex: 'hardware',
      key: 'hardware',
      width: 100,
      filters: hardwareOptions,
      filteredValue: hardwareFilter,
      onFilter: (value: boolean | React.Key, record: CIResult) => record.hardware === value,
      render: renderHardwareTag,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      filters: STATUS_FILTERS,
      filteredValue: statusFilter,
      onFilter: (value: boolean | React.Key, record: CIResult) => toRunStatus(record) === value,
      render: (_: string, record: CIResult) => renderStatusTag(toRunStatus(record)),
    },
    {
      title: '结果',
      dataIndex: 'job_stats',
      key: 'conclusion',
      width: 220,
      filters: RESULT_FILTERS,
      filteredValue: resultFilter,
      onFilter: (value: boolean | React.Key, record: CIResult) => record.conclusion === value,
      render: (_: CIRunJobStats | null | undefined, record: CIResult) => renderRunJobStats(record.job_stats),
    },
    { title: '时长', dataIndex: 'duration_seconds', key: 'duration_seconds', width: 100, render: formatDuration },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 180,
      sorter: (a: CIResult, b: CIResult) => new Date(b.started_at || 0).getTime() - new Date(a.started_at || 0).getTime(),
      render: (startedAt: string | null) => startedAt ? (
        <Space direction="vertical" size={0}>
          <Text>{formatTimezone(startedAt, 'YYYY-MM-DD HH:mm:ss')}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{fromTimezoneNow(startedAt)}</Text>
        </Space>
      ) : '-',
    },
    {
      title: '结束时间',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 180,
      sorter: (a: CIResult, b: CIResult) => new Date(b.completed_at || 0).getTime() - new Date(a.completed_at || 0).getTime(),
      render: (completedAt: string | null) => completedAt ? (
        <Space direction="vertical" size={0}>
          <Text>{formatTimezone(completedAt, 'YYYY-MM-DD HH:mm:ss')}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{fromTimezoneNow(completedAt)}</Text>
        </Space>
      ) : '-',
    },
  ]

  const resetFilters = () => {
    setWorkflowFilter([])
    setSelectedWorkflow(undefined)
    setHardwareFilter([])
    setStatusFilter([])
    setResultFilter([])
    setEventFilter([])
    setLogSearch('')
    setDateFilterMode('recent_day')
    setCustomDateRange(null)
  }

  const isDefaultDateRange = dateFilterMode === 'recent_day'
  const hasFilters = Boolean(workflowFilter.length || hardwareFilter.length || statusFilter.length || resultFilter.length || eventFilter.length || logSearch || !isDefaultDateRange)

  return (
    <Card
      title="运行记录"
      extra={(
        <Space>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            value={selectedWorkflow}
            options={workflowOptions.map(({ text, value }) => ({ label: text, value }))}
            onChange={(value) => { setSelectedWorkflow(value); setWorkflowFilter(value ? [value] : []) }}
            placeholder="按 Workflow 筛选"
            style={{ width: 190 }}
          />
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
          <Input.Search allowClear value={logSearch} onChange={(event) => setLogSearch(event.target.value)} placeholder="搜索 Workflow、Run 或触发方式" style={{ width: 250 }} />
          <Button icon={<ReloadOutlined />} onClick={() => runsQuery.refetch()} loading={runsQuery.isLoading}>刷新</Button>
          <Button onClick={resetFilters} disabled={!hasFilters}>重置筛选</Button>
        </Space>
      )}
    >
      <Tooltip title="列表按 Workflow Run 展示；“结果”列展示该次运行内 Jobs 的统计，点击“查看详情”可进入具体 Job 列表。">
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>当前为 Workflow 级别运行记录，“结果”列展示 Jobs 统计，具体条目请进入运行详情查看。</Text>
      </Tooltip>
      <Table
        columns={columns}
        dataSource={visibleRuns}
        loading={runsQuery.isLoading}
        rowKey={(record) => `${record.run_id}-${record.id}`}
        pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
        scroll={{ x: 1350 }}
        locale={{ emptyText: <Empty description="暂无 Workflow 运行记录" /> }}
        onChange={(_, filters) => {
          const nextWorkflowFilter = (filters.workflow_name as string[] | null) || []
          setWorkflowFilter(nextWorkflowFilter)
          setSelectedWorkflow(nextWorkflowFilter.length === 1 ? nextWorkflowFilter[0] : undefined)
          setHardwareFilter((filters.hardware as string[] | null) || [])
          setStatusFilter((filters.status as string[] | null) || [])
          setResultFilter((filters.conclusion as string[] | null) || [])
          setEventFilter((filters.event as string[] | null) || [])
        }}
      />
    </Card>
  )
}

export default WorkflowTestExecutionTable
