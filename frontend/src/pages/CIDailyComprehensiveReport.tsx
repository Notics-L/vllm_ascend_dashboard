import { useMemo, useState, useCallback } from 'react'
import dayjs from 'dayjs'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  type TableColumnsType,
} from 'antd'
import {
  FilePdfOutlined,
  FileExcelOutlined,
  DownloadOutlined,
  CheckCircleFilled,
  ReloadOutlined,
  CloudDownloadOutlined,
  RobotOutlined,
  FileSearchOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  ExpandOutlined,
  LinkOutlined,
} from '@ant-design/icons'

import { FailureAnalysisDetailModal } from '../components/FailureAnalysisDetailModal'
import { useJobFailureAnalysis } from '../hooks/useFailureAnalysis'
import { useComprehensiveReport } from '../hooks/useCI'
import {
  type ComprehensiveReportFailure,
  type ComprehensiveReport,
  downloadComprehensiveReport,
  collectComprehensiveRawLogs,
  autoAnalyzeComprehensiveReport,
} from '../services/ci'

export const CONCLUSION_ZH: Record<string, string> = {
  success: '成功',
  failure: '失败',
  cancelled: '已取消',
  skipped: '已跳过',
  timed_out: '超时',
  startup_failure: '启动失败',
}

const AI_STATUS_ZH: Record<string, string> = {
  completed: '已完成',
  analyzing: '分析中',
  pending: '待分析',
  reused: '已复用',
  failed: '分析失败',
  degraded: '已降级(GitHub)',
  cancelled: '已取消',
  raw_log_collected: '已收集日志',
}

const AI_STATUS_COLORS: Record<string, string> = {
  completed: 'green',
  analyzing: 'processing',
  pending: 'orange',
  reused: 'cyan',
  failed: 'red',
  degraded: 'blue',
  cancelled: 'default',
  raw_log_collected: 'geekblue',
}

const { Text, Title } = Typography
const { RangePicker } = DatePicker

type FilterState = {
  start: string | null
  end: string | null
  hardware: string | null
  branch: string | null
}
type SortOrder = 'desc' | 'asc'

const HARDWARE_OPTIONS = [
  { label: '全部硬件', value: 'all' },
  { label: 'Ascend 910B', value: '910B' },
  { label: 'Ascend 310P', value: '310P' },
  { label: 'Ascend A2', value: 'A2' },
  { label: 'Ascend A3', value: 'A3' },
  { label: 'All in One', value: 'All-in-One' },
  { label: 'PCIE', value: 'PCIE' },
]

const CATEGORY_COLORS: Record<string, string> = {
  基础设施: '#dc2626',
  测试用例: '#f59e0b',
  开发代码: '#2563eb',
  其他: '#6b7280',
}

function defaultWindow(): { start: dayjs.Dayjs; end: dayjs.Dayjs } {
  const end = dayjs().hour(9).minute(0).second(0).millisecond(0)
  const start = end.subtract(1, 'day').hour(20).minute(0).second(0)
  return { start, end }
}

function CIDailyComprehensiveReport() {
  const [filters, setFilters] = useState<FilterState>({
    start: null,
    end: null,
    hardware: null,
    branch: null,
  })
  const [applied, setApplied] = useState<FilterState>({
    start: null,
    end: null,
    hardware: null,
    branch: null,
  })
  const [range, setRange] = useState<{ start: dayjs.Dayjs; end: dayjs.Dayjs }>(() => defaultWindow())
  const [exporting, setExporting] = useState<'pdf' | 'excel' | null>(null)
  const [collectingLogs, setCollectingLogs] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisModalJobId, setAnalysisModalJobId] = useState<number | null>(null)
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  const [logModalRow, setLogModalRow] = useState<ComprehensiveReportFailure | null>(null)
  const [detailDrawerRow, setDetailDrawerRow] = useState<ComprehensiveReportFailure | null>(null)

  const { data: modalAnalysis } = useJobFailureAnalysis(analysisModalJobId)

  const { data: report, isLoading, isError, error, refetch } = useComprehensiveReport(applied)

  const [rawLogsReport, setRawLogsReport] = useState<ComprehensiveReport | null>(null)
  const activeReport: ComprehensiveReport | undefined = rawLogsReport ?? report

  const applyFilters = () => {
    setRawLogsReport(null)
    setApplied({ ...filters })
  }

  const handleRangeChange = (values: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
    if (values && values[0] && values[1]) {
      setRange({ start: values[0], end: values[1] })
      setFilters((f) => ({
        ...f,
        start: values[0]!.format('YYYY-MM-DD HH:mm:ss'),
        end: values[1]!.format('YYYY-MM-DD HH:mm:ss'),
      }))
    } else {
      const def = defaultWindow()
      setRange(def)
      setFilters((f) => ({
        ...f,
        start: null,
        end: null,
      }))
    }
  }

  const handleAutoAnalyze = async () => {
    const failures = activeReport?.failures ?? []
    const targets = failures.filter(
      (f) => (f.conclusion || '').toLowerCase() === 'failure' && f.job_id && !f.analysis_id,
    )
    if (targets.length === 0) {
      message.info('当前窗口内失败项均已有 AI 分析，无需自动触发')
      return
    }
    setAnalyzing(true)
    try {
      const res = await autoAnalyzeComprehensiveReport({
        start: applied.start,
        end: applied.end,
        hardware: applied.hardware,
        branch: applied.branch,
      })
      if (res.triggered > 0) {
        message.success(`已自动触发 ${res.triggered} 条 AI 失败分析（后台执行）`)
        setTimeout(() => {
          refetch()
          setRawLogsReport(null)
        }, 1500)
      } else {
        message.info('无可自动触发的失败项')
      }
      if (res.errors?.length) {
        message.warning(`${res.errors.length} 条入队失败：${res.errors[0]}`)
      }
    } catch (e) {
      message.error(`自动 AI 分析失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setAnalyzing(false)
    }
  }

  const handleCollectRawLogs = async () => {
    const targets = (activeReport?.failures ?? []).filter(
      (f) =>
        f.job_id &&
        (!(f.error_line || '').trim() || !f.problem_category || f.problem_category === '未定界' || f.analysis_status === 'failed'),
    )
    if (targets.length === 0) {
      message.info('当前失败项均已有原始报错信息，无需抓取')
      return
    }
    setCollectingLogs(true)
    try {
      const enhanced = await collectComprehensiveRawLogs({
        start: applied.start,
        end: applied.end,
        hardware: applied.hardware,
        branch: applied.branch,
      })
      setRawLogsReport(enhanced)
      message.success(`已抓取并解析 ${enhanced._raw_log_stats?.collected ?? 0}/${targets.length} 条失败项的原始日志`)
    } catch (e) {
      message.error(`抓取原始日志失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setCollectingLogs(false)
    }
  }

  const handleExport = async (format: 'pdf' | 'excel') => {
    if (!activeReport) return
    setExporting(format)
    try {
      const hw = applied.hardware && applied.hardware !== 'all' ? applied.hardware : null
      const br = applied.branch && applied.branch !== 'all' ? applied.branch : null
      await downloadComprehensiveReport(format, {
        start: applied.start,
        end: applied.end,
        hardware: hw,
        branch: br,
        collectRawLogs: !!rawLogsReport,
      })
      message.success(`${format === 'pdf' ? 'PDF' : 'Excel'} 报告导出成功`)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      message.error(`导出失败：${msg}`)
    } finally {
      setExporting(null)
    }
  }

  const summary = activeReport?.summary
  const allGreen = activeReport?.all_green

  const sortedFailures = useMemo(() => {
    const items = activeReport?.failures ?? []
    const sorted = [...items].sort((a, b) => {
      const da = a.started_at || a.report_datetime || ''
      const db = b.started_at || b.report_datetime || ''
      return sortOrder === 'desc' ? db.localeCompare(da) : da.localeCompare(db)
    })
    // 按 (workflow_name, job_name, run_id) 分组 → 树形 parent/child
    const groupMap = new Map<string, ComprehensiveReportFailure[]>()
    for (const item of sorted) {
      const key = [item.workflow_name, item.job_name, item.run_id].join('|')
      const arr = groupMap.get(key)
      if (arr) arr.push(item)
      else groupMap.set(key, [item])
    }
    const tree: ComprehensiveReportFailure[] = []
    for (const [, group] of groupMap) {
      if (group.length === 1) {
        tree.push(group[0])
      } else {
        // parent = 第一条，children = 全部子 step
        const parent: ComprehensiveReportFailure = {
          ...group[0],
          failed_steps: group.map((g) => g.failed_step || g.job_name || 'unknown').filter(Boolean),
          children: [...group],
        }
        tree.push(parent)
      }
    }
    // 按 parent 时间排序
    tree.sort((a, b) => {
      const da = a.started_at || a.report_datetime || ''
      const db = b.started_at || b.report_datetime || ''
      return sortOrder === 'desc' ? db.localeCompare(da) : da.localeCompare(db)
    })
    return tree
  }, [activeReport, sortOrder])

  const failureColumns: TableColumnsType<ComprehensiveReportFailure> = useMemo(
    () => [
      {
        title: '时间',
        dataIndex: 'report_datetime',
        width: 155,
        fixed: 'left' as const,
        sorter: (a: ComprehensiveReportFailure, b: ComprehensiveReportFailure) =>
          (a.started_at || '').localeCompare(b.started_at || ''),
        render: (v: string, r) => (
          <span style={{ whiteSpace: 'nowrap', fontSize: 12 }}>{v || r.report_date}</span>
        ),
      },
      {
        title: '分支',
        dataIndex: 'source_branch',
        width: 140,
        ellipsis: { showTitle: true },
        render: (v: string) => <Tag color="blue">{v}</Tag>,
      },
      {
        title: 'Workflow',
        dataIndex: 'workflow_name',
        width: 130,
        ellipsis: { showTitle: true },
      },
      {
        title: 'Job',
        dataIndex: 'job_name',
        width: 180,
        ellipsis: { showTitle: true },
      },
      {
        title: '模型',
        dataIndex: 'model_name',
        width: 160,
        ellipsis: { showTitle: true },
        render: (v: string | null, r) =>
          v || <Text type="secondary">{r.test_model ? r.test_model.replace(/\.ya?ml$/i, '') : '-'}</Text>,
      },
      {
        title: '测试内容',
        dataIndex: 'test_yaml',
        width: 160,
        ellipsis: { showTitle: true },
        render: (v: string | null) => v || <Text type="secondary">-</Text>,
      },
      {
        title: '硬件',
        dataIndex: 'hardware',
        width: 80,
        render: (v: string) => <Tag>{v}</Tag>,
      },
      {
        title: '结论',
        dataIndex: 'conclusion',
        width: 85,
        render: (_: string | null, r) => {
          const zh = r.conclusion_zh || CONCLUSION_ZH[(r.conclusion || '').toLowerCase()]
          const color = (r.conclusion || '').toLowerCase() === 'failure' ? 'red' : 'default'
          return <Tag color={color}>{zh || r.conclusion || '-'}</Tag>
        },
      },
      {
        title: '责任人',
        dataIndex: 'owner',
        width: 90,
        ellipsis: { showTitle: true },
        render: (v: string | null) => v || <Text type="secondary">-</Text>,
      },
      {
        title: '失败 Steps',
        dataIndex: 'failed_steps',
        width: 130,
        render: (_: string[] | null | undefined, r) => {
          // parent 行（有 children）：显示聚合数
          if (r.children?.length && r.children.length > 0) {
            return (
              <Tag color="red" icon={<span>•</span>}>
                {r.children.length} steps
              </Tag>
            )
          }
          const steps = r.failed_steps
          if (steps?.length) {
            if (steps.length === 1) return <span style={{ fontSize: 12 }}>{steps[0]}</span>
            return (
              <Tooltip title={steps.join('\n')}>
                <Tag color="orange">{steps.length} steps</Tag>
              </Tooltip>
            )
          }
          if (r.failed_step) return <span style={{ fontSize: 12 }}>{r.failed_step}</span>
          return <Text type="secondary">-</Text>
        },
      },
      {
        title: '耗时',
        dataIndex: 'duration_text',
        width: 75,
        render: (v: string) => <span style={{ fontSize: 12 }}>{v}</span>,
      },
      {
        title: '范畴',
        dataIndex: 'problem_category',
        width: 95,
        render: (v: string | null) => (
          <Tag color={CATEGORY_COLORS[v || ''] || 'orange'}>{v || '未定界'}</Tag>
        ),
      },
      {
        title: 'AI分析',
        dataIndex: 'analysis_status',
        width: 110,
        render: (_: string | null, r) => {
          const st = r.analysis_status
          const color = AI_STATUS_COLORS[st || ''] || 'default'
          return (
            <Space size={2}>
              <Tag color={color} style={{ fontSize: 11 }}>{AI_STATUS_ZH[st || ''] ?? st ?? '未分析'}</Tag>
              {st === 'completed' && r.analysis_id && (
                <Button size="small" type="link" icon={<RobotOutlined />}
                  style={{ padding: 0, height: 'auto' }}
                  onClick={() => setAnalysisModalJobId(r.job_id ?? null)}
                />
              )}
            </Space>
          )
        },
      },
      {
        title: '报错线索',
        dataIndex: 'error_line',
        width: 200,
        render: (_: string | null, r) => {
          const hasLines = (r.raw_log_lines?.length || 0) > 0 || !!(r.error_line || '').trim()
          if (!hasLines) return <Text type="secondary">-</Text>
          return (
            <Button
              size="small"
              icon={<FileSearchOutlined />}
              onClick={() => setLogModalRow(r)}
            >
              查看（{(r.raw_log_lines?.length || 1)}行）
            </Button>
          )
        },
      },
      {
        title: '处理',
        dataIndex: 'processing_status',
        width: 80,
        render: (v: string) => (
          <Tag color={v === '未处理' ? 'volcano' : 'green'}>{v}</Tag>
        ),
      },
      {
        title: '操作',
        key: 'actions',
        width: 130,
        fixed: 'right' as const,
        render: (_: unknown, r) => (
          <Space size={4}>
            <Button size="small" type="link" icon={<ExpandOutlined />}
              onClick={() => setDetailDrawerRow(r)}
            >
              详情
            </Button>
            {r.github_url && (
              <Button size="small" type="link" icon={<LinkOutlined />}
                href={r.github_url} target="_blank"
              >
                GH
              </Button>
            )}
          </Space>
        ),
      },
    ],
    [],
  )

  const charts = (activeReport?.charts ?? {}) as Record<string, string>
  const chartKeys = [
    'failure_by_category',
    'failure_by_branch',
    'failure_by_hardware',
    'failure_by_model',
    'processing_status',
  ]
  const distributionCharts = chartKeys.filter((k) => charts[k])

  if (isLoading) {
    return (
      <div style={{ padding: 60, textAlign: 'center' }}>
        <Text type="secondary" style={{ fontSize: 16 }}>正在聚合 CI 综合报错数据…</Text>
      </div>
    )
  }

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="综合报告加载失败"
        description={(error as Error)?.message || '请稍后重试'}
        action={<Button icon={<ReloadOutlined />} onClick={() => refetch()}>重试</Button>}
      />
    )
  }

  return (
    <div>
      {/* 顶部标题 + 导出 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>CI 每日综合报错报告</Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            以「失败详情」为核心 · 窗口内无失败即标记全绿
          </Text>
        </div>
        <Space>
          <Button icon={<DownloadOutlined />} disabled={!report} onClick={() => refetch()}>刷新</Button>
          <Button
            icon={<CloudDownloadOutlined />}
            loading={collectingLogs}
            disabled={!activeReport}
            onClick={handleCollectRawLogs}
          >
            抓取原始日志
          </Button>
          <Button
            icon={<RobotOutlined />}
            loading={analyzing}
            disabled={!activeReport}
            onClick={handleAutoAnalyze}
          >
            自动 AI 分析
          </Button>
          <Button
            type="primary"
            danger
            icon={<FilePdfOutlined />}
            loading={exporting === 'pdf'}
            disabled={!report}
            onClick={() => handleExport('pdf')}
          >
            PDF
          </Button>
          <Button
            type="primary"
            icon={<FileExcelOutlined />}
            loading={exporting === 'excel'}
            disabled={!report}
            onClick={() => handleExport('excel')}
          >
            Excel
          </Button>
        </Space>
      </div>

      {/* 筛选区 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size="middle">
          <span>时间窗口</span>
          <RangePicker
            showTime
            allowClear
            value={[range.start, range.end]}
            onChange={handleRangeChange}
            format={['YYYY-MM-DD HH:mm', 'YYYY-MM-DD HH:mm']}
            style={{ width: 420 }}
          />
          <span>硬件</span>
          <Select
            options={HARDWARE_OPTIONS}
            value={filters.hardware ?? 'all'}
            style={{ width: 150 }}
            onChange={(v) => setFilters((f) => ({ ...f, hardware: v === 'all' ? null : v }))}
          />
          <span>分支</span>
          <Select
            options={[
              { label: '全部分支', value: 'all' },
              { label: 'main', value: 'main' },
              { label: 'release', value: 'release' },
              { label: 'release-all', value: 'release-all' },
            ]}
            value={filters.branch ?? 'all'}
            style={{ width: 130 }}
            onChange={(v) => setFilters((f) => ({ ...f, branch: v === 'all' ? null : v }))}
          />
          <Button type="primary" onClick={applyFilters}>应用</Button>
        </Space>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            已应用：{applied.start || '默认昨日20:00'} ~ {applied.end || '默认今日09:00'}
            {applied.hardware ? ` · ${applied.hardware}` : ''}
            {applied.branch ? ` · ${applied.branch}` : ''}
          </Text>
        </div>
      </Card>

      {/* 全绿横幅 */}
      {allGreen && (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleFilled />}
          message="时间窗口内无失败 — 流水线全绿"
          description={`窗口：${activeReport?.window?.start_beijing} ~ ${activeReport?.window?.end_beijing}（北京时间）`}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 核心摘要 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="失败记录" value={summary?.total_failed ?? 0}
              valueStyle={{ color: (summary?.total_failed ?? 0) > 0 ? '#cf1322' : '#3f8600', fontWeight: 700 }} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={5}>
          <Card size="small">
            <Statistic
              title="主要问题范畴"
              value={summary?.top_failure_category || '—'}
              valueStyle={{ color: CATEGORY_COLORS[summary?.top_failure_category || ''] || '#d97706', fontWeight: 700 }}
            />
            {summary?.top_failure_category_count ? (
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>{summary.top_failure_category_count} 次</div>
            ) : null}
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="涉及分支" value={Object.keys(summary?.failure_by_branch ?? {}).length} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="涉及硬件" value={Object.keys(summary?.failure_by_hardware ?? {}).length} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={4}>
          <Card size="small">
            <Statistic title="涉及模型/用例" value={Object.keys(summary?.failure_by_model ?? {}).length} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={3}>
          <Card size="small">
            <Statistic title="未处理" value={summary?.unprocessed_failures ?? 0}
              valueStyle={{ color: (summary?.unprocessed_failures ?? 0) > 0 ? '#cf1322' : '#3f8600' }} />
          </Card>
        </Col>
      </Row>

      {/* 失败明细表（核心） */}
      <Card
        size="small"
        title={
          <Space>
            <span>失败明细（{sortedFailures.length}）</span>
            <Button
              size="small"
              icon={sortOrder === 'desc' ? <ArrowDownOutlined /> : <ArrowUpOutlined />}
              onClick={() => setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')}
            >
              {sortOrder === 'desc' ? '时间降序' : '时间升序'}
            </Button>
          </Space>
        }
        style={{ marginBottom: 20 }}
      >
        {sortedFailures.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Text type="secondary">窗口内无失败记录</Text>
          </div>
        ) : (
          <Table<ComprehensiveReportFailure>
            rowKey={(r, idx) => `${r.run_id}-${r.job_id ?? 'run'}-${r.id ?? ''}-${idx}`}
            size="small"
            columns={failureColumns}
            dataSource={sortedFailures as ComprehensiveReportFailure[]}
            scroll={{ x: 2000 }}
            pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
            defaultExpandAllRows={false}
          />
        )}
      </Card>

      {/* 失败维度分布图表 */}
      {distributionCharts.length > 0 && (
        <Card size="small" title="失败维度分布" style={{ marginBottom: 20 }}>
          <Row gutter={[16, 16]}>
            {distributionCharts.map((key) => (
              <Col xs={24} md={12} key={key}>
                <div style={{ textAlign: 'center' }}>
                  <img alt={key} src={charts[key]} style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }} />
                </div>
              </Col>
            ))}
          </Row>
        </Card>
      )}

      {/* 次级：整体运行 */}
      <Card size="small" title="整体运行（次级信息）" style={{ marginBottom: 20 }}>
        <Row gutter={[16, 16]}>
          <Col xs={24} sm={8} md={4} lg={3}>
            <Statistic title="总运行数" value={summary?.total_runs ?? 0} />
          </Col>
          <Col xs={24} sm={8} md={4} lg={2}>
            <Statistic title="通过" value={summary?.passed_runs ?? 0} valueStyle={{ color: '#3f8600' }} />
          </Col>
          <Col xs={24} sm={8} md={4} lg={2}>
            <Statistic title="失败" value={summary?.failed_runs ?? 0} valueStyle={{ color: '#cf1322' }} />
          </Col>
          <Col xs={24} sm={8} md={4} lg={3}>
            <Statistic title="成功率" value={summary?.success_rate ?? 0} suffix="%"
              valueStyle={{ color: (summary?.success_rate ?? 0) >= 90 ? '#3f8600' : '#cf1322' }} />
          </Col>
          <Col xs={24} sm={8} md={4} lg={4}>
            <Statistic title="平均耗时" value={summary?.avg_duration_seconds ?? '-'} suffix={summary?.avg_duration_seconds ? 's' : ''} />
          </Col>
          <Col xs={24} sm={8} md={4} lg={4}>
            <Statistic title="P90 耗时" value={summary?.p90_duration_seconds ?? '-'} suffix={summary?.p90_duration_seconds ? 's' : ''} />
          </Col>
        </Row>
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          {charts.result_distribution && (
            <Col xs={24} md={12}>
              <div style={{ textAlign: 'center' }}>
                <img alt="整体运行结果" src={charts.result_distribution} style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }} />
              </div>
            </Col>
          )}
          {charts.success_rate_trend && (
            <Col xs={24} md={12}>
              <div style={{ textAlign: 'center' }}>
                <img alt="成功率趋势" src={charts.success_rate_trend} style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }} />
              </div>
            </Col>
          )}
        </Row>
      </Card>

      {/* 报错线索弹窗 */}
      <Modal
        title={`原始报错线索 — ${logModalRow?.job_name || ''}`}
        open={!!logModalRow}
        onCancel={() => setLogModalRow(null)}
        footer={<Button onClick={() => setLogModalRow(null)}>关闭</Button>}
        width={800}
      >
        {logModalRow && (
          <div>
            {logModalRow.error_brief && (
              <Alert type="warning" showIcon message={logModalRow.error_brief} style={{ marginBottom: 12 }} />
            )}
            <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 16, fontFamily: 'Consolas, monospace', fontSize: 13, lineHeight: 1.8, maxHeight: 500, overflowY: 'auto' }}>
              {(logModalRow.raw_log_lines?.length
                ? logModalRow.raw_log_lines
                : logModalRow.error_line
                  ? [logModalRow.error_line]
                  : ['-']
              ).map((ln, i) => (
                <div key={i} style={{ color: '#991b1b', wordBreak: 'break-all' }}>{ln}</div>
              ))}
            </div>
            {logModalRow.root_cause_summary && (
              <div style={{ marginTop: 12 }}>
                <Text strong>AI 根因摘要：</Text>
                <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{logModalRow.root_cause_summary}</div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 详情抽屉 */}
      <Drawer
        title={`失败详情 — ${detailDrawerRow?.job_name || ''}`}
        open={!!detailDrawerRow}
        onClose={() => setDetailDrawerRow(null)}
        width={720}
        placement="right"
      >
        {detailDrawerRow && (
          <div>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="时间">{detailDrawerRow.report_datetime}</Descriptions.Item>
              <Descriptions.Item label="分支"><Tag color="blue">{detailDrawerRow.source_branch}</Tag></Descriptions.Item>
              <Descriptions.Item label="Workflow">{detailDrawerRow.workflow_name}</Descriptions.Item>
              <Descriptions.Item label="Job">{detailDrawerRow.job_name}</Descriptions.Item>
              <Descriptions.Item label="模型/用例">{detailDrawerRow.test_model || detailDrawerRow.display_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="硬件"><Tag>{detailDrawerRow.hardware}</Tag></Descriptions.Item>
              <Descriptions.Item label="结论">
                <Tag color="red">{detailDrawerRow.conclusion_zh || CONCLUSION_ZH[(detailDrawerRow.conclusion || '').toLowerCase()] || detailDrawerRow.conclusion}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="耗时">{detailDrawerRow.duration_text}</Descriptions.Item>
              <Descriptions.Item label="责任人">{detailDrawerRow.owner || '-'}</Descriptions.Item>
              <Descriptions.Item label="失败关键步骤">{detailDrawerRow.failed_step || '-'}</Descriptions.Item>
              <Descriptions.Item label="问题范畴">
                <Tag color={CATEGORY_COLORS[detailDrawerRow.problem_category || ''] || 'orange'}>{detailDrawerRow.problem_category || '未定界'}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="AI 分析状态">
                <Tag color={AI_STATUS_COLORS[detailDrawerRow.analysis_status || ''] || 'default'}>
                  {AI_STATUS_ZH[detailDrawerRow.analysis_status || ''] ?? detailDrawerRow.analysis_status ?? '未分析'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="处理状态">
                <Tag color={detailDrawerRow.processing_status === '未处理' ? 'volcano' : 'green'}>{detailDrawerRow.processing_status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="责任范围(Fo)">
                {detailDrawerRow.model_fo || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="部署类型">{detailDrawerRow.deployment_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="关联 PR">{detailDrawerRow.related_pr || '-'}</Descriptions.Item>
            </Descriptions>

            {detailDrawerRow.error_brief && (
              <Alert type="warning" showIcon message={detailDrawerRow.error_brief} style={{ marginBottom: 12 }} />
            )}

            {/* 完整报错线索 */}
            <div style={{ marginBottom: 12 }}>
              <Text strong style={{ marginBottom: 4, display: 'block' }}>原始报错线索</Text>
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: 12, fontFamily: 'Consolas, monospace', fontSize: 12, lineHeight: 1.8, maxHeight: 300, overflowY: 'auto' }}>
                {(detailDrawerRow.raw_log_lines?.length
                  ? detailDrawerRow.raw_log_lines
                  : detailDrawerRow.error_line
                    ? [detailDrawerRow.error_line]
                    : ['-']
                ).map((ln, i) => (
                  <div key={i} style={{ color: '#991b1b', wordBreak: 'break-all' }}>{ln}</div>
                ))}
              </div>
            </div>

            {/* AI 分析 */}
            {detailDrawerRow.root_cause_summary && (
              <div style={{ marginBottom: 12 }}>
                <Text strong style={{ marginBottom: 4, display: 'block' }}>AI 根因摘要</Text>
                <div style={{ background: '#f0f9ff', borderRadius: 8, padding: 12, whiteSpace: 'pre-wrap', fontSize: 13 }}>{detailDrawerRow.root_cause_summary}</div>
              </div>
            )}
            {detailDrawerRow.improvement_measures && (
              <div style={{ marginBottom: 12 }}>
                <Text strong style={{ marginBottom: 4, display: 'block' }}>改进措施</Text>
                <div style={{ background: '#f0fdf4', borderRadius: 8, padding: 12, whiteSpace: 'pre-wrap', fontSize: 13 }}>{detailDrawerRow.improvement_measures}</div>
              </div>
            )}
            {detailDrawerRow.notes && (
              <div style={{ marginBottom: 12 }}>
                <Text strong style={{ marginBottom: 4, display: 'block' }}>备注</Text>
                <div style={{ background: '#fffbeb', borderRadius: 8, padding: 12, whiteSpace: 'pre-wrap', fontSize: 13 }}>{detailDrawerRow.notes}</div>
              </div>
            )}

            {/* AI 分析详情按钮 */}
            {detailDrawerRow.analysis_status === 'completed' && detailDrawerRow.analysis_id && (
              <Button
                type="primary"
                icon={<RobotOutlined />}
                onClick={() => {
                  setDetailDrawerRow(null)
                  setAnalysisModalJobId(detailDrawerRow.job_id ?? null)
                }}
                style={{ marginBottom: 12 }}
              >
                查看完整 AI 分析报告
              </Button>
            )}

            {/* GitHub 链接 */}
            {detailDrawerRow.github_url && (
              <a href={detailDrawerRow.github_url} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <LinkOutlined /> 查看 GitHub 原始日志（{detailDrawerRow.github_url}）
              </a>
            )}
          </div>
        )}
      </Drawer>

      {/* AI 失败分析详情弹窗 */}
      {analysisModalJobId !== null && (
        <FailureAnalysisDetailModal
          open={analysisModalJobId !== null}
          onClose={() => setAnalysisModalJobId(null)}
          jobId={analysisModalJobId}
          existingAnalysis={modalAnalysis ?? null}
        />
      )}
    </div>
  )
}

export default CIDailyComprehensiveReport
