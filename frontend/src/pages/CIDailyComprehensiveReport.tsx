import { useMemo, useState } from 'react'
import dayjs from 'dayjs'
import {
  Alert,
  Card,
  Col,
  DatePicker,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  Button,
  Spin,
  Empty,
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
} from '@ant-design/icons'

import { FailureAnalysisDetailModal } from '../components/FailureAnalysisDetailModal'
import { useJobFailureAnalysis } from '../hooks/useFailureAnalysis'

export const CONCLUSION_ZH: Record<string, string> = {
  success: '成功',
  failure: '失败',
  cancelled: '已取消',
  skipped: '已跳过',
  timed_out: '超时',
  startup_failure: '启动失败',
}
import { useComprehensiveReport } from '../hooks/useCI'
import {
  type ComprehensiveReportFailure,
  type ComprehensiveReport,
  downloadComprehensiveReport,
  collectComprehensiveRawLogs,
  autoAnalyzeComprehensiveReport,
} from '../services/ci'

const AI_STATUS_ZH: Record<string, string> = {
  completed: '已完成',
  analyzing: '分析中',
  pending: '待分析',
  reused: '已复用',
  failed: '分析失败',
  cancelled: '已取消',
}

const { Text, Title } = Typography
const { RangePicker } = DatePicker

type FilterState = {
  start: string | null
  end: string | null
  hardware: string | null
  branch: string | null
}

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
  // 默认窗口：昨日 20:00 ~ 今日 09:00（北京时间/本地）
  const end = dayjs().hour(9).minute(0).second(0).millisecond(0)
  const start = end.subtract(1, 'day').hour(20).minute(0).second(0)
  return { start, end }
}

function CIDailyComprehensiveReport() {
  const [filters, setFilters] = useState<FilterState>(
    () => ({ start: null, end: null, hardware: null, branch: null }),
  )
  const [query, setQuery] = useState<{
    start: string | null
    end: string | null
    hardware: string | null
    branch: string | null
  }>(() => ({ start: null, end: null, hardware: null, branch: null }))
  const [range, setRange] = useState<{ start: dayjs.Dayjs; end: dayjs.Dayjs }>(() => defaultWindow())
  const [exporting, setExporting] = useState<'pdf' | 'excel' | null>(null)
  const [collectingLogs, setCollectingLogs] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysisModalJobId, setAnalysisModalJobId] = useState<number | null>(null)

  // 当前选中行的 AI 分析详情（按 jobId 拉取，分析中自动轮询）
  const { data: modalAnalysis } = useJobFailureAnalysis(analysisModalJobId)

  const {
    data: report,
    isLoading,
    isError,
    error,
    refetch,
  } = useComprehensiveReport(query)

  // 本地覆盖：抓取到的原始日志版本（覆盖 useQuery 的 data）
  const [rawLogsReport, setRawLogsReport] = useState<ComprehensiveReport | null>(null)
  const activeReport: ComprehensiveReport | undefined = rawLogsReport ?? report

  const applyFilters = () => {
    setRawLogsReport(null)
    setQuery({ ...filters })
  }

  // 一键自动 AI 分析：对「结论=失败 且尚无分析记录」的失败项自动入队，
  // 入队后自动刷新报告（analysis_status 会变为「分析中」），由后端 Collector worker 异步执行。
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
        start: query.start,
        end: query.end,
        hardware: query.hardware,
        branch: query.branch,
      })
      if (res.triggered > 0) {
        message.success(`已自动触发 ${res.triggered} 条 AI 失败分析（后台执行，稍后可刷新查看结果）`)
        // 触发后刷新，让表格 AI 分析列反映「分析中」状态
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
    // 仅对「缺少原始报错行 / 未定界」的失败项，自动访问 GitHub 原始日志链接并解析报错线索
    const targets = (activeReport?.failures ?? []).filter(
      (f) =>
        f.job_id &&
        (!(f.error_line || '').trim() || !f.problem_category || f.problem_category === '未定界'),
    )
    if (targets.length === 0) {
      message.info('当前失败项均已有原始报错信息，无需抓取')
      return
    }
    setCollectingLogs(true)
    try {
      const enhanced = await collectComprehensiveRawLogs({
        start: query.start,
        end: query.end,
        hardware: query.hardware,
        branch: query.branch,
      })
      setRawLogsReport(enhanced)
      message.success(`已抓取并解析 ${enhanced._raw_log_stats?.collected ?? 0}/${targets.length} 条失败项的原始日志`)
    } catch (e) {
      message.error(`抓取原始日志失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setCollectingLogs(false)
    }
  }

  const handleRangeChange = (_: unknown, dateStrings: [string, string]) => {
    setFilters((f) => ({
      ...f,
      start: dateStrings[0] ? dayjs(dateStrings[0]).format('YYYY-MM-DD HH:mm:ss') : null,
      end: dateStrings[1] ? dayjs(dateStrings[1]).format('YYYY-MM-DD HH:mm:ss') : null,
    }))
  }

  const handleExport = async (format: 'pdf' | 'excel') => {
    if (!activeReport) return
    setExporting(format)
    try {
      const hw = query.hardware && query.hardware !== 'all' ? query.hardware : null
      const br = query.branch && query.branch !== 'all' ? query.branch : null
      // 若当前页已抓取原始日志（rawLogsReport），则导出时同样抓取，保证图文一致
      await downloadComprehensiveReport(format, {
        start: query.start,
        end: query.end,
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

  const failureColumns: TableColumnsType<ComprehensiveReportFailure> = useMemo(
    () => [
      {
        title: '日期（北京时间）',
        dataIndex: 'report_datetime',
        width: 160,
        render: (v: string, r) => (
          <span style={{ whiteSpace: 'nowrap' }}>{v || r.report_date}</span>
        ),
      },
      { title: '分支', dataIndex: 'source_branch', width: 90, render: (v: string) => <Tag color="blue">{v}</Tag> },
      { title: 'Workflow', dataIndex: 'workflow_name', width: 150, ellipsis: true },
      { title: 'Job', dataIndex: 'job_name', width: 150, ellipsis: true },
      {
        title: '模型 / 用例',
        dataIndex: 'test_model',
        width: 160,
        ellipsis: true,
        render: (v: string | null, r: ComprehensiveReportFailure) =>
          v || r.display_name || <Text type="secondary">-</Text>,
      },
      { title: '硬件', dataIndex: 'hardware', width: 90, render: (v: string) => <Tag>{v}</Tag> },
      {
        title: '结论',
        dataIndex: 'conclusion',
        width: 100,
        render: (_: string | null, r: ComprehensiveReportFailure) => (
          <Tag color="red">{r.conclusion_zh || CONCLUSION_ZH[(r.conclusion || '').toLowerCase()] || r.conclusion || '-'}</Tag>
        ),
      },
      {
        title: '责任人',
        dataIndex: 'owner',
        width: 110,
        render: (v: string | null) => v || <Text type="secondary">-</Text>,
      },
      {
        title: '失败关键步骤',
        dataIndex: 'failed_step',
        width: 140,
        render: (v: string | null) => v || <Text type="secondary">-</Text>,
      },
      { title: '耗时', dataIndex: 'duration_text', width: 80 },
      {
        title: '问题范畴',
        dataIndex: 'problem_category',
        width: 110,
        render: (v: string | null) => (
          <Tag color={CATEGORY_COLORS[v || ''] || 'orange'}>{v || '未定界'}</Tag>
        ),
      },
      {
        title: 'AI 分析',
        dataIndex: 'analysis_status',
        width: 130,
        render: (_: string | null, r: ComprehensiveReportFailure) => {
          const st = r.analysis_status
          const color =
            st === 'completed' ? 'green'
            : st === 'analyzing' ? 'processing'
            : st === 'failed' ? 'red'
            : st ? 'orange'
            : 'default'
          return (
            <Space size={4}>
              <Tag color={color}>{AI_STATUS_ZH[st || ''] ?? st ?? '未分析'}</Tag>
              {st === 'completed' && r.analysis_id && (
                <Button
                  size="small"
                  type="link"
                  icon={<RobotOutlined />}
                  onClick={() => setAnalysisModalJobId(r.job_id ?? null)}
                >
                  详情
                </Button>
              )}
            </Space>
          )
        },
      },
      {
        title: '原始报错线索行',
        dataIndex: 'error_line',
        render: (_: string | null, r: ComprehensiveReportFailure) => {
          const lines: string[] = r.raw_log_lines?.length ? r.raw_log_lines : r.error_line ? [r.error_line] : []
          if (!lines.length) {
            return (
              <Tooltip title={r.problem_line} placement="topLeft">
                <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#999' }}>{r.problem_line || '-'}</span>
              </Tooltip>
            )
          }
          return (
            <div style={{ maxWidth: 420 }}>
              {lines.slice(0, 3).map((ln, i) => (
                <div
                  key={i}
                  style={{
                    fontFamily: 'monospace',
                    fontSize: 12,
                    color: '#991b1b',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                  title={ln}
                >
                  {ln}
                </div>
              ))}
              {lines.length > 3 && (
                <div style={{ color: '#8a8f98', fontSize: 11, textAlign: 'right' }}>+{lines.length - 3} 行</div>
              )}
            </div>
          )
        },
      },
      {
        title: '处理状态',
        dataIndex: 'processing_status',
        width: 100,
        render: (v: string) => <Tag color={v === '未处理' ? 'volcano' : 'green'}>{v}</Tag>,
      },
      {
        title: '链接',
        dataIndex: 'github_url',
        width: 70,
        render: (url: string | null) =>
          url ? (
            <a href={url} target="_blank" rel="noreferrer">GitHub</a>
          ) : (
            <Text type="secondary">-</Text>
          ),
      },
    ],
    [],
  )

  // 图表 base64（key 与后端 ci_report_charts 对齐），展示 activeReport（含抓取增强版）
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
        <Spin size="large" tip="正在聚合 CI 综合报错数据…" />
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
        action={
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            重试
          </Button>
        }
      />
    )
  }

  return (
    <div>
      {/* 顶部标题 + 导出 */}
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>CI 每日综合报错报告</Title>
          <Text type="secondary">
            以「失败详情」为核心：哪些用例 / 模型失败了、失败原因（问题范畴 + 原始报错线索行）。
            窗口内无失败即标记全绿。
          </Text>
        </div>
        <Space>
          <Button
            icon={<DownloadOutlined />}
            disabled={!report}
            onClick={() => refetch()}
          >
            刷新
          </Button>
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
            导出 PDF
          </Button>
          <Button
            type="primary"
            icon={<FileExcelOutlined />}
            loading={exporting === 'excel'}
            disabled={!report}
            onClick={() => handleExport('excel')}
          >
            导出 Excel
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
            ]}
            value={filters.branch ?? 'all'}
            style={{ width: 130 }}
            onChange={(v) => setFilters((f) => ({ ...f, branch: v === 'all' ? null : v }))}
          />
          <Button type="primary" onClick={applyFilters}>应用</Button>
        </Space>
      </Card>

      {/* 全绿横幅 */}
      {allGreen && (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleFilled />}
          message="时间窗口内无失败 —— 流水线全绿"
          description={`窗口：${activeReport?.window?.start_beijing} ~ ${activeReport?.window?.end_beijing}（北京时间）`}
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 核心摘要：失败统计（核心） */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
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
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                {summary.top_failure_category_count} 次
              </div>
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
        title={`失败明细（${activeReport?.failures?.length ?? 0}）`}
        style={{ marginBottom: 24 }}
      >
        {activeReport?.failures.length === 0 ? (
          <Empty description="窗口内无失败记录" />
        ) : (
          <Table<ComprehensiveReportFailure>
            rowKey={(r) => `${r.run_id}-${r.job_id ?? 'run'}`}
            size="small"
            columns={failureColumns}
            dataSource={activeReport?.failures ?? []}
            scroll={{ x: 1900 }}
            pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
            expandable={{
              expandedRowRender: (r) => (
                <div style={{ padding: '4px 8px' }}>
                  {r.root_cause_summary && (
                    <p style={{ margin: '2px 0' }}>
                      <Text strong>AI 根因摘要：</Text>
                      <span style={{ whiteSpace: 'pre-wrap' }}>{r.root_cause_summary}</span>
                    </p>
                  )}
                  {r.improvement_measures && (
                    <p style={{ margin: '2px 0' }}>
                      <Text strong>改进措施：</Text>
                      {r.improvement_measures}
                    </p>
                  )}
                  {r.notes && (
                    <p style={{ margin: '2px 0' }}>
                      <Text strong>备注：</Text>
                      {r.notes}
                    </p>
                  )}
                </div>
              ),
              rowExpandable: (r) => Boolean(r.root_cause_summary || r.improvement_measures || r.notes),
            }}
          />
        )}
      </Card>

      {/* 失败维度分布图表 */}
      {distributionCharts.length > 0 && (
        <Card size="small" title="失败维度分布" style={{ marginBottom: 24 }}>
          <Row gutter={[16, 16]}>
            {distributionCharts.map((key) => (
              <Col xs={24} md={12} key={key}>
                <div style={{ textAlign: 'center' }}>
                  <img
                    alt={key}
                    src={charts[key]}
                    style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }}
                  />
                </div>
              </Col>
            ))}
          </Row>
        </Card>
      )}

      {/* 次级：整体运行（弱化） + 趋势 + 成功率图 */}
      <Card size="small" title="整体运行（次级信息）" style={{ marginBottom: 24 }}>
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
            <Statistic
              title="成功率"
              value={summary?.success_rate ?? 0}
              suffix="%"
              valueStyle={{ color: (summary?.success_rate ?? 0) >= 90 ? '#3f8600' : '#cf1322' }}
            />
          </Col>
          <Col xs={24} sm={8} md={4} lg={4}>
            <Statistic
              title="平均耗时"
              value={summary?.avg_duration_seconds ?? '-'}
              suffix={summary?.avg_duration_seconds ? 's' : ''}
            />
          </Col>
          <Col xs={24} sm={8} md={4} lg={4}>
            <Statistic
              title="P90 耗时"
              value={summary?.p90_duration_seconds ?? '-'}
              suffix={summary?.p90_duration_seconds ? 's' : ''}
            />
          </Col>
        </Row>
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          {charts.result_distribution && (
            <Col xs={24} md={12}>
              <div style={{ textAlign: 'center' }}>
                <img alt="整体运行结果" src={charts.result_distribution}
                  style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }} />
              </div>
            </Col>
          )}
          {charts.success_rate_trend && (
            <Col xs={24} md={12}>
              <div style={{ textAlign: 'center' }}>
                <img alt="成功率趋势" src={charts.success_rate_trend}
                  style={{ maxWidth: '100%', height: 'auto', borderRadius: 8 }} />
              </div>
            </Col>
          )}
        </Row>
      </Card>

      {/* AI 失败分析详情弹窗（复用 FailureAnalysisDetailModal，分析中自动轮询） */}
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
