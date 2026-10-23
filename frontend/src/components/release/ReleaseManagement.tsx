import { useCallback, useEffect, useMemo, useState } from 'react'
import * as echarts from 'echarts'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  message,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  CloudUploadOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'

import {
  fmtDuration,
  getOverview,
  getRunDetail,
  getRuns,
  getStatusColor,
  listWorkflows,
  triggerSync,
  exportExcel,
  ConfigCheckResult,
  getConfigCheck,
  OverviewData,
  BuildRun,
  BuildGroup,
  type Workflow,
} from '../../services/releaseManagement'

const { Title, Text } = Typography
const { Option } = Select

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

function ChartBox({ id, height = 320 }: { id: string; height?: number }) {
  return (
    <div
      id={id}
      style={{ width: '100%', height }}
      className="echarts-container"
    />
  )
}

export default function ReleaseManagement() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [selectedIds, setSelectedIds] = useState<number[] | null>(null)
  const [daysBack, setDaysBack] = useState(30)
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [configCheck, setConfigCheck] = useState<ConfigCheckResult | null>(null)
  const [syncLoading, setSyncLoading] = useState(false)
  const [runsData, setRunsData] = useState<{ runs: BuildRun[]; total: number }>({ runs: [], total: 0 })
  const [detailDrawerOpen, setDetailDrawerOpen] = useState(false)
  const [detailRun, setDetailRun] = useState<BuildRun | null>(null)
  const [detailGroups, setDetailGroups] = useState<BuildGroup[]>([])
  const [detailLoading, setDetailLoading] = useState(false)

  // Chart refs
  const timelineChartRef = useCallback(
    (dom: HTMLDivElement | null) => {
      if (!dom || !overview?.timeline?.length) return
      const chart = echarts.init(dom)
      const data = overview.timeline
      chart.setOption({
        tooltip: {
          trigger: 'axis',
          formatter: (p: any[]) => {
            const item = p[0]
            return (
              `${item.name}<br/>排队 ${fmtDuration(data[parseInt(item.dataIndex)].wait)}<br/>` +
              `执行 ${fmtDuration(data[parseInt(item.dataIndex)].exec)}<br/>` +
              `合计 ${fmtDuration(data[parseInt(item.dataIndex)].wall)}`
            )
          },
        },
        legend: { data: ['排队等待', '实际执行'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 50, right: 16, top: 36, bottom: 40 },
        xAxis: {
          type: 'category',
          data: data.map((d) => `${d.date} #${String(d.run_id).slice(-4)}`),
          axisLabel: { fontSize: 9, interval: Math.max(0, Math.floor(data.length / 15) - 1), rotate: 30 },
        },
        yAxis: { type: 'value', name: '秒' },
        series: [
          {
            name: '排队等待', type: 'bar', stack: 't',
            data: data.map((d) => d.wait),
            itemStyle: { color: 'rgba(250,173,20,0.7)' },
          },
          {
            name: '实际执行', type: 'bar', stack: 't',
            data: data.map((d) => d.exec),
            itemStyle: { color: 'rgba(24,144,255,0.7)' },
          },
        ],
      })
      window.addEventListener('resize', () => chart.resize())
    },
    [overview?.timeline],
  )

  const phaseChartRef = useCallback(
    (dom: HTMLDivElement | null) => {
      if (!dom || !overview?.phase_data?.length) return
      const chart = echarts.init(dom)
      const colors = ['#5b8ff9', '#5ada7a', '#fd9a31', '#e06024', '#9f7aea', '#17a2b8']
      chart.setOption({
        tooltip: {
          formatter: (p: any) =>
            `${p.name}: 平均 ${fmtDuration(p.value)}, 共 ${overview.phase_data.find((d) => d.name === p.name)?.count} 次`,
        },
        series: [
          {
            type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'],
            data: overview.phase_data.map((d, i) => ({
              name: d.name, value: d.total, itemStyle: { color: colors[i % colors.length] },
            })),
            label: { fontSize: 11 },
          },
        ],
      })
      window.addEventListener('resize', () => chart.resize())
    },
    [overview?.phase_data],
  )

  const archChartRef = useCallback(
    (dom: HTMLDivElement | null) => {
      if (!dom || !overview?.arch_data?.length) return
      const chart = echarts.init(dom)
      chart.setOption({
        tooltip: { formatter: (p: any) => `${p.name}: 平均 ${fmtDuration(p.data.value[2])}` },
        grid: { left: 60, right: 20, top: 20, bottom: 30 },
        xAxis: { type: 'category', data: overview.arch_data.map((d) => d.name) },
        yAxis: { type: 'value', name: '秒' },
        series: [
          {
            type: 'bar',
            data: overview.arch_data.map((d) => ({
              value: d.total,
              itemStyle: { color: '#5b8ff9' },
            })),
            name: '总耗时',
          },
        ],
      })
      window.addEventListener('resize', () => chart.resize())
    },
    [overview?.arch_data],
  )

  const stepChartRef = useCallback(
    (dom: HTMLDivElement | null) => {
      if (!dom || !overview?.step_data?.length) return
      const chart = echarts.init(dom)
      const topSteps = overview.step_data.slice(0, 15)
      chart.setOption({
        tooltip: {
          formatter: (p: any) =>
            `${p.data.name}: 平均 ${fmtDuration(p.data.value)}, 共 ${p.data.count} 次`,
        },
        grid: { left: 160, right: 60, top: 10, bottom: 20 },
        xAxis: { type: 'value', name: '秒' },
        yAxis: { type: 'category', data: topSteps.map((d) => d.name), axisLabel: { fontSize: 10 } },
        series: [
          {
            type: 'bar',
            data: topSteps.map((d) => ({
              value: d.avg, name: d.name, count: d.count,
              itemStyle: { color: '#fd9a31' },
            })),
          },
        ],
      })
      window.addEventListener('resize', () => chart.resize())
    },
    [overview?.step_data],
  )

  // Load config check
  useEffect(() => {
    getConfigCheck().then((r) => setConfigCheck(r)).catch(() => {})
    listWorkflows().then((w) => setWorkflows(w)).catch(() => {})
  }, [])

  // Load overview when selection changes
  useEffect(() => {
    if (!configCheck?.github_configured) return
    if (!selectedIds || selectedIds.length === 0) return

    setLoading(true)
    Promise.all([
      getOverview(selectedIds, daysBack),
      getRuns(selectedIds, statusFilter, null, 1, 50),
    ])
      .then(([ov, rd]) => {
        setOverview(ov)
        setRunsData({ runs: rd.runs, total: rd.total })
      })
      .catch(() => message.error('加载数据失败'))
      .finally(() => setLoading(false))
  }, [selectedIds, daysBack, statusFilter, configCheck?.github_configured])

  // Auto-select all workflows on load
  useEffect(() => {
    if (workflows.length > 0 && !selectedIds) {
      setSelectedIds(workflows.map((w) => w.id))
    }
  }, [workflows, selectedIds])

  const handleSync = async () => {
    if (!selectedIds || selectedIds.length === 0) return
    setSyncLoading(true)
    try {
      for (const wfId of selectedIds) {
        await triggerSync(wfId, daysBack, 100)
      }
      message.success('同步成功')
      // Reload
      const ov = await getOverview(selectedIds, daysBack)
      setOverview(ov)
    } catch {
      message.error('同步失败')
    } finally {
      setSyncLoading(false)
    }
  }

  const handleExportExcel = async () => {
    try {
      const blob = await exportExcel(selectedIds, daysBack)
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

  const handleRunClick = async (run: BuildRun) => {
    setDetailDrawerOpen(true)
    setDetailRun(run)
    setDetailLoading(true)
    setDetailGroups([])
    try {
      const detail = await getRunDetail(run.id)
      setDetailGroups(detail.groups)
    } catch {
      message.error('加载运行详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const runsColumns = [
    { title: 'Run ID', dataIndex: 'github_run_id', width: 100, sorter: (a: BuildRun, b: BuildRun) => a.github_run_id - b.github_run_id },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => <Tag color={getStatusColor(s)}>{s}</Tag>,
    },
    { title: '分支', dataIndex: 'branch', width: 150, ellipsis: true },
    { title: '事件', dataIndex: 'event', width: 80 },
    {
      title: '创建时间', dataIndex: 'created_at', width: 180,
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '',
    },
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
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>
          发布件管理
        </Title>
        <Text type="secondary">
          镜像构建流程全链路分析 · 三方件版本监控 · AI 可信度评估
        </Text>
      </div>

      {/* Toolbar */}
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col>
            <Text strong>工作流:</Text>
          </Col>
          <Col span={6}>
            <Select
              mode="multiple"
              allowClear
              placeholder="选择工作流"
              value={selectedIds}
              onChange={setSelectedIds}
              style={{ width: '100%' }}
            >
              {workflows.map((w) => (
                <Option key={w.id} value={w.id}>
                  {w.workflow_name}
                </Option>
              ))}
            </Select>
          </Col>
          <Col>
            <Text strong>天数:</Text>
          </Col>
          <Col span={3}>
            <Select value={daysBack} onChange={setDaysBack} style={{ width: 100 }}>
              <Option value={7}>7 天</Option>
              <Option value={14}>14 天</Option>
              <Option value={30}>30 天</Option>
              <Option value={60}>60 天</Option>
              <Option value={90}>90 天</Option>
            </Select>
          </Col>
          <Col>
            <Text strong>状态:</Text>
          </Col>
          <Col span={3}>
            <Select
              allowClear
              placeholder="全部"
              value={statusFilter}
              onChange={(v) => setStatusFilter(v)}
              style={{ width: 120 }}
            >
              <Option value="success">成功</Option>
              <Option value="failure">失败</Option>
              <Option value="cancelled">取消</Option>
            </Select>
          </Col>
          <Col flex="auto" style={{ textAlign: 'right' }}>
            <Space>
              <Button
                icon={<Spin size="small" spinning={syncLoading} />}
                onClick={handleSync}
                disabled={!selectedIds?.length}
              >
                同步数据
              </Button>
              <Button
                icon={<DownloadOutlined />}
                onClick={handleExportExcel}
                disabled={!overview || !overview.stats.total}
              >
                导出 Excel
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {!selectedIds?.length && configCheck.github_configured && (
        <Alert
          message="没有可用的工作流"
          description="请先点击「同步数据」或在系统配置中添加 GitHub Token 后发现工作流。"
          type="info"
          showIcon
        />
      )}

      {/* KPI Cards */}
      {overview && (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={6}>
              <KPICard
                label="总运行次数"
                value={overview.stats.total}
                color="#1890ff"
                sub={`成功 ${overview.stats.successful} / 失败 ${overview.stats.failed}`}
              />
            </Col>
            <Col span={6}>
              <KPICard
                label="成功率"
                value={`${overview.stats.success_rate}%`}
                color={overview.stats.success_rate >= 80 ? '#52c41a' : '#ff4d4f'}
                sub={`异常慢构建 ${overview.stats.abnormal_count} 次`}
              />
            </Col>
            <Col span={6}>
              <KPICard
                label="平均执行耗时"
                value={fmtDuration(overview.stats.avg_exec)}
                color="#722ed1"
                sub={`最短 ${fmtDuration(overview.stats.min_exec)} / 最长 ${fmtDuration(overview.stats.max_exec)}`}
              />
            </Col>
            <Col span={6}>
              <KPICard
                label="耗时波动系数"
                value={`${overview.stats.cv_exec}%`}
                color={overview.stats.cv_exec > 50 ? '#ff4d4f' : overview.stats.cv_exec > 30 ? '#faad14' : '#52c41a'}
                sub={`平均排队 ${fmtDuration(overview.stats.avg_wait)}`}
              />
            </Col>
          </Row>

          {/* Charts Row 1 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={16}>
              <Card bordered={false} title="构建耗时趋势（排队 vs 执行）">
                <ChartBox id="chart-timeline" height={320} />
              </Card>
            </Col>
            <Col span={8}>
              <Card bordered={false} title="Phase 耗时分布">
                <ChartBox id="chart-phase" height={320} />
              </Card>
            </Col>
          </Row>

          {/* Charts Row 2 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card bordered={false} title="架构耗时对比">
                <ChartBox id="chart-arch" height={280} />
              </Card>
            </Col>
            <Col span={12}>
              <Card bordered={false} title="Top 15 最耗时 Step">
                <ChartBox id="chart-step" height={280} />
              </Card>
            </Col>
          </Row>

          {/* Target Table */}
          {overview.target_data.length > 0 && (
            <Card bordered={false} title="Target/OS 组合耗时" style={{ marginBottom: 16 }}>
              <Table
                dataSource={overview.target_data}
                rowKey="name"
                size="small"
                pagination={false}
                columns={[
                  { title: 'Target', dataIndex: 'name', key: 'name' },
                  { title: '次数', dataIndex: 'count', key: 'count', width: 60 },
                  { title: '平均', dataIndex: 'avg', key: 'avg', render: (v: number) => fmtDuration(v) },
                  { title: '最短', dataIndex: 'min', key: 'min', render: (v: number) => fmtDuration(v) },
                  { title: '最长', dataIndex: 'max', key: 'max', render: (v: number) => fmtDuration(v) },
                  { title: '总耗时', dataIndex: 'total', key: 'total', render: (v: number) => fmtDuration(v) },
                ]}
              />
            </Card>
          )}
        </>
      )}

      {/* Runs Table */}
      <Card bordered={false} title={`运行记录 (${runsData.total})`}>
        <Table
          dataSource={runsData.runs}
          rowKey="id"
          size="small"
          scroll={{ x: 900 }}
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
          columns={runsColumns}
          onRow={(record) => ({
            style: { cursor: 'pointer' },
            onClick: () => handleRunClick(record),
          })}
        />
      </Card>

      {/* Run Detail Drawer */}
      <Drawer
        title={`运行详情 #${detailRun?.github_run_id}`}
        placement="right"
        width={720}
        open={detailDrawerOpen}
        onClose={() => setDetailDrawerOpen(false)}
      >
        {detailRun && (
          <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="状态">
              <Tag color={getStatusColor(detailRun.status)}>{detailRun.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="分支">{detailRun.branch}</Descriptions.Item>
            <Descriptions.Item label="事件">{detailRun.event}</Descriptions.Item>
            <Descriptions.Item label="Job 数">{detailRun.num_jobs}</Descriptions.Item>
            <Descriptions.Item label="排队时间">{fmtDuration(detailRun.wait_duration)}</Descriptions.Item>
            <Descriptions.Item label="执行时间">{fmtDuration(detailRun.exec_duration)}</Descriptions.Item>
            <Descriptions.Item label="总耗时">{fmtDuration(detailRun.wall_duration)}</Descriptions.Item>
            <Descriptions.Item label="Commit">{detailRun.commit_sha?.slice(0, 8)}</Descriptions.Item>
          </Descriptions>
        )}
        <Spin spinning={detailLoading}>
          <Table
            dataSource={detailGroups}
            rowKey="id"
            size="small"
            scroll={{ x: 600 }}
            pagination={false}
            columns={[
              { title: 'Group', dataIndex: 'group_name', ellipsis: true },
              { title: 'Phase', dataIndex: 'phase', width: 80 },
              {
                title: '状态', dataIndex: 'status', width: 80,
                render: (s: string) => <Tag color={getStatusColor(s)}>{s}</Tag>,
              },
              { title: '耗时', dataIndex: 'total_duration', render: (v: number) => fmtDuration(v) },
              { title: '架构', dataIndex: 'architecture', width: 70 },
              { title: 'Sub-Job', dataIndex: 'sub_job_name', ellipsis: true },
              {
                title: 'Steps',
                render: (_: unknown, record: BuildGroup) =>
                  record.steps.length ? (
                    <Space direction="vertical" size={0}>
                      {record.steps.map((s, i) => (
                        <Text key={i} type="secondary" style={{ fontSize: 11 }}>
                          {s.name}: {fmtDuration(s.duration)}
                          <Tag color={getStatusColor(s.status)} style={{ marginLeft: 4 }}>
                            {s.status}
                          </Tag>
                        </Text>
                      ))}
                    </Space>
                  ) : '-',
              },
            ]}
          />
        </Spin>
      </Drawer>
    </Spin>
  )
}
