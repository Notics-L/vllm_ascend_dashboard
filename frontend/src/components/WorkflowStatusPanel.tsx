import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Col,
  DatePicker,
  InputNumber,
  Row,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { useRefreshWorkflowRun, useWorkflowStatusRuns } from '../hooks/useCI'
import type { WorkflowStatusCategory, WorkflowStatusRun } from '../services/ci'
import { formatTimezone } from '../utils/timezone'

const { Text } = Typography

const CATEGORY_OPTIONS: WorkflowStatusCategory[] = ['PR', 'Daily', 'scheduled', 'Weekly', 'Image', 'Other']
const DEFAULT_CATEGORIES: WorkflowStatusCategory[] = ['scheduled', 'Image']
const AUTO_REFRESH_STORAGE_KEY = 'ci-workflow-status-auto-refresh'
const REFRESH_INTERVAL_STORAGE_KEY = 'ci-workflow-status-refresh-interval'

const CATEGORY_COLORS: Record<WorkflowStatusCategory, string> = {
  PR: 'blue',
  Daily: 'cyan',
  scheduled: 'purple',
  Weekly: 'geekblue',
  Image: 'magenta',
  Other: 'default',
}

const readStoredNumber = (key: string, fallback: number) => {
  if (typeof window === 'undefined') return fallback
  const parsed = Number(window.localStorage.getItem(key))
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

const readStoredBoolean = (key: string, fallback: boolean) => {
  if (typeof window === 'undefined') return fallback
  const stored = window.localStorage.getItem(key)
  return stored === null ? fallback : stored === 'true'
}

function getRunState(run: WorkflowStatusRun): 'success' | 'failure' | 'running' | 'skipped' {
  if (run.conclusion === 'success') return 'success'
  if (run.conclusion === 'skipped' || run.conclusion === 'neutral') return 'skipped'
  if (run.status !== 'completed' && run.status !== 'success') return 'running'
  return 'failure'
}

function renderConclusionTag(run: WorkflowStatusRun) {
  const conclusion = run.conclusion
  const color = conclusion === 'success'
    ? 'success'
    : conclusion === 'failure'
      ? 'error'
      : conclusion === 'cancelled'
        ? 'default'
        : run.status !== 'completed'
          ? 'processing'
          : 'default'
  return <Tag color={color}>{conclusion || '--'}</Tag>
}

function renderStatusTag(run: WorkflowStatusRun) {
  const color = run.status === 'in_progress'
    ? 'processing'
    : run.status === 'queued' || run.status === 'waiting'
      ? 'warning'
      : run.conclusion === 'success'
        ? 'success'
        : run.conclusion === 'failure'
          ? 'error'
          : 'default'
  return <Tag color={color}>{run.status || '--'}</Tag>
}

function renderCases(run: WorkflowStatusRun) {
  const cases = run.cases
  if (!cases) return <Text type="secondary">--</Text>
  return (
    <Tooltip
      title={`总用例 ${cases.total}，成功 ${cases.success}，失败 ${cases.failure}，进行中 ${cases.in_progress}，跳过 ${cases.skipped}`}
    >
      <Space size={2} style={{ fontVariantNumeric: 'tabular-nums' }}>
        <span>{cases.total}</span>
        <span style={{ color: '#00a76f' }}>/✓{cases.success}</span>
        {cases.failure > 0 && <span style={{ color: '#ff4d4f' }}>/✗{cases.failure}</span>}
        {cases.in_progress > 0 && <span style={{ color: '#fa8c16' }}>/↻{cases.in_progress}</span>}
        {cases.skipped > 0 && <span style={{ color: '#8c8c8c' }}>/⊘{cases.skipped}</span>}
      </Space>
    </Tooltip>
  )
}

export default function WorkflowStatusPanel() {
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>(() => [
    dayjs().startOf('day'),
    dayjs().endOf('day'),
  ])
  const [categories, setCategories] = useState<WorkflowStatusCategory[]>(DEFAULT_CATEGORIES)
  const [autoRefresh, setAutoRefresh] = useState(() => readStoredBoolean(AUTO_REFRESH_STORAGE_KEY, false))
  const [refreshInterval, setRefreshInterval] = useState(() => (
    Math.min(6000, Math.max(5, readStoredNumber(REFRESH_INTERVAL_STORAGE_KEY, 30)))
  ))
  const [refreshingRunId, setRefreshingRunId] = useState<number | null>(null)

  const queryParams = useMemo(() => ({
    start_time: dateRange[0].startOf('day').toISOString(),
    end_time: dateRange[1].endOf('day').toISOString(),
    limit: 5000,
  }), [dateRange])

  const runsQuery = useWorkflowStatusRuns(queryParams)
  const refreshMutation = useRefreshWorkflowRun()

  useEffect(() => {
    window.localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(autoRefresh))
  }, [autoRefresh])

  useEffect(() => {
    window.localStorage.setItem(REFRESH_INTERVAL_STORAGE_KEY, String(refreshInterval))
  }, [refreshInterval])

  useEffect(() => {
    if (!autoRefresh) return undefined
    const timer = window.setInterval(() => {
      void runsQuery.refetch()
    }, refreshInterval * 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, refreshInterval, runsQuery.refetch])

  const visibleRuns = useMemo(() => {
    return (runsQuery.data || []).filter((run) => categories.includes(run.category))
  }, [categories, runsQuery.data])

  const stats = useMemo(() => {
    let success = 0
    let failure = 0
    let running = 0
    visibleRuns.forEach((run) => {
      const state = getRunState(run)
      if (state === 'success') success += 1
      else if (state === 'running') running += 1
      else if (state === 'failure') failure += 1
    })
    return { total: visibleRuns.length, success, failure, running }
  }, [visibleRuns])

  const handleRefreshRun = async (run: WorkflowStatusRun) => {
    if (run.category === 'Image') {
      await runsQuery.refetch()
      return
    }
    setRefreshingRunId(run.run_id)
    try {
      await refreshMutation.mutateAsync(run.run_id)
      message.success(`Run ${run.run_id} 已刷新`)
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
      message.error(detail || `Run ${run.run_id} 刷新失败`)
    } finally {
      setRefreshingRunId(null)
    }
  }

  const handleToday = () => {
    setDateRange([dayjs().startOf('day'), dayjs().endOf('day')])
  }

  const columns = [
    {
      title: 'Run #',
      dataIndex: 'run_id',
      key: 'run_id',
      width: 130,
      render: (runId: number, run: WorkflowStatusRun) => (
        run.html_url
          ? <a href={run.html_url} target="_blank" rel="noopener noreferrer">{runId}</a>
          : runId
      ),
    },
    {
      title: 'Workflow',
      dataIndex: 'display_name',
      key: 'display_name',
      width: 300,
      ellipsis: true,
      render: (name: string, run: WorkflowStatusRun) => (
        <Tooltip title={name}>
          <span>{name || run.workflow_name}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Category',
      dataIndex: 'category',
      key: 'category',
      width: 110,
      render: (category: WorkflowStatusCategory) => (
        <Tag color={CATEGORY_COLORS[category] || 'default'}>{category}</Tag>
      ),
    },
    {
      title: 'PR #',
      dataIndex: 'pr_number',
      key: 'pr_number',
      width: 90,
      render: (prNumber: number | null, run: WorkflowStatusRun) => (
        prNumber
          ? <a href={run.pr_url || '#'} target="_blank" rel="noopener noreferrer">#{prNumber}</a>
          : '--'
      ),
    },
    {
      title: 'Cases (T/S/F/R)',
      dataIndex: 'cases',
      key: 'cases',
      width: 190,
      render: (_: unknown, run: WorkflowStatusRun) => renderCases(run),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (_: string, run: WorkflowStatusRun) => renderStatusTag(run),
    },
    {
      title: 'Conclusion',
      dataIndex: 'conclusion',
      key: 'conclusion',
      width: 120,
      render: (_: string | null, run: WorkflowStatusRun) => renderConclusionTag(run),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (createdAt: string | null) => createdAt ? formatTimezone(createdAt, 'YYYY-MM-DD HH:mm:ss') : '--',
    },
    {
      title: 'Link',
      key: 'link',
      width: 80,
      render: (_: unknown, run: WorkflowStatusRun) => (
        run.html_url
          ? <a href={run.html_url} target="_blank" rel="noopener noreferrer">View</a>
          : '--'
      ),
    },
    {
      title: 'Action',
      key: 'action',
      width: 100,
      render: (_: unknown, run: WorkflowStatusRun) => (
        <Button
          size="small"
          loading={refreshingRunId === run.run_id}
          onClick={() => void handleRefreshRun(run)}
        >
          Update
        </Button>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space wrap>
          <Text strong>From:</Text>
          <DatePicker
            value={dateRange[0]}
            onChange={(value) => {
              if (value) setDateRange([value.startOf('day'), dateRange[1]])
            }}
            allowClear={false}
          />
          <Text strong>To:</Text>
          <DatePicker
            value={dateRange[1]}
            onChange={(value) => {
              if (value) setDateRange([dateRange[0], value.endOf('day')])
            }}
            allowClear={false}
          />
          <Button onClick={handleToday}>Today</Button>
          <Button
            icon={<ReloadOutlined />}
            loading={runsQuery.isFetching}
            onClick={() => void runsQuery.refetch()}
          >
            Refresh
          </Button>
          <Space size={6}>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} />
            <Text type="secondary">Auto-refresh</Text>
            <InputNumber
              min={5}
              max={6000}
              value={refreshInterval}
              onChange={(value) => {
                if (typeof value === 'number') {
                  setRefreshInterval(Math.min(6000, Math.max(5, value)))
                }
              }}
              style={{ width: 80 }}
            />
            <Text type="secondary">s</Text>
          </Space>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="TOTAL" value={stats.total} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="SUCCESS" value={stats.success} valueStyle={{ color: '#00a76f' }} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="FAILURE" value={stats.failure} valueStyle={{ color: '#ff4d4f' }} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card><Statistic title="IN PROGRESS" value={stats.running} valueStyle={{ color: '#fa8c16' }} /></Card>
        </Col>
      </Row>

      <Card size="small">
        <Space wrap align="center">
          <Text type="secondary">Category:</Text>
          {CATEGORY_OPTIONS.map((category) => (
            <Checkbox
              key={category}
              checked={categories.includes(category)}
              onChange={(event) => {
                setCategories((previous) => (
                  event.target.checked
                    ? [...previous, category]
                    : previous.filter((item) => item !== category)
                ))
              }}
            >
              {category}
            </Checkbox>
          ))}
          <Button size="small" onClick={() => setCategories([])}>Clear</Button>
        </Space>
      </Card>

      <Table
        columns={columns}
        dataSource={visibleRuns}
        rowKey={(run) => `${run.category}-${run.run_id}`}
        loading={runsQuery.isLoading}
        pagination={{ pageSize: 100, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
        scroll={{ x: 1350 }}
      />
    </Space>
  )
}
