import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {useNavigate, useParams} from 'react-router-dom'
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Radio,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import {
  CodeOutlined,
  DownloadOutlined,
  FlagOutlined,
  FilterOutlined,
  GithubOutlined,
  ReloadOutlined,
  RocketOutlined,
  SearchOutlined,
  SettingOutlined,
  SortAscendingOutlined,
  StarOutlined,
  ToolOutlined,
  TrophyOutlined,
} from '@ant-design/icons'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as ChartsTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import dayjs from 'dayjs'
import {
  type BuildOptimizationStats,
  type BuildPlan,
  type ClassificationSummary,
  type ContributorDetail, ExtendedTrend,
  getBuildFilterOptions,
  getBuildOptimizationStats,
  getBuildPlans,
  getClassificationSummary,
  getContributorDetail,
  getContributorPolicy,
  getExtendedTrend,
  getModuleHotspot,
  getQuadrantData,
  getRanking,
  getReposList,
  getRoleStats,
  type ModuleHotspotResponse, PolicyResult,
  type QuadrantData,
  type RankingItem,
  type RoleStats,
  getContributorBoardSettings,
  type ContributorBoardSettings as SettingsType,
  updateQuadrantConfig,
  updateScoringWeights,
  updateRoleThresholds,
  syncContributors,
} from '../services/contributorBoard'

const { Title, Text } = Typography

/* ------------------------------------------------------------------ */
/*                             颜色映射                                 */
/* ------------------------------------------------------------------ */

const buildTypeColors: Record<string, string> = {
  full_build: 'red',
  minimal_build: 'orange',
  nightly_only: 'blue',
  skip: 'green',
  smart_check: 'purple',
  conservative: 'default',
}

const buildTypeLabels: Record<string, string> = {
  full_build: '完整构建',
  minimal_build: '精简构建',
  nightly_only: '仅夜间构建',
  skip: '跳过构建',
  smart_check: '智能检查',
  conservative: '保守策略',
}

const typeColors: Record<string, string> = {
  BugFix: 'red',
  Feature: 'green',
  Performance: 'blue',
  Revert: 'volcano',
  CI: 'orange',
  Doc: 'cyan',
  Test: 'purple',
  Refactor: 'magenta',
  Misc: 'default',
}

const pieColors = ['#ff4d4f', '#52c41a', '#1890ff', '#fa8c16', '#ff7a45', '#13c2c2', '#722ed1', '#eb2f96', '#d9d9d9']

const roleLabels: Record<string, string> = {
  developer: '开发者',
  reviewer: '检视者',
  maintainer: 'Maintainer',
}

const roleColors: Record<string, string> = {
  developer: 'blue',
  reviewer: 'purple',
  maintainer: 'orange',
}

const quadrantLabels: Record<string, string> = {
  INCENTIVE: '激励区',
  IMPROVE: '改进区',
  ENCOURAGE: '鼓励区',
  REPLACE: '置换区',
}

const quadrantColors: Record<string, string> = {
  INCENTIVE: '#52c41a',
  IMPROVE: '#faad14',
  ENCOURAGE: '#1890ff',
  REPLACE: '#ff4d4f',
}

const quadrantRoleLabels: Record<string, string> = {
  star: '明星',
  active: '活跃',
  observer: '观察',
  at_risk: '流失风险',
}

const quadrantRoleColors: Record<string, string> = {
  star: 'gold',
  active: 'blue',
  observer: 'default',
  at_risk: 'red',
}

/* ------------------------------------------------------------------ */
/*                            分类 Tab 内容                            */
/* ------------------------------------------------------------------ */

function ModuleHotspotGrid({ modules, maxCount }: { modules: Array<{ name: string; count: number }>; maxCount: number }) {
  if (modules.length === 0) return <Empty description="暂无模块热点数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />

  const top3Gradients = [
    'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
    'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
  ]
  const normalGradient = 'linear-gradient(135deg, #e0e7ff 0%, #f0f4ff 100%)'

  return (
    <Row gutter={[12, 12]}>
      {modules.map((item, index) => {
        const pct = Math.round((item.count / maxCount) * 100)
        const isTop3 = index < 3
        const gradient = isTop3 ? top3Gradients[index] : normalGradient
        return (
          <Col xs={12} sm={8} md={8} lg={6} key={item.name}>
            <div style={{
              background: gradient,
              borderRadius: 10,
              padding: '12px 14px',
              position: 'relative',
              overflow: 'hidden',
              minHeight: 80,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
              boxShadow: isTop3 ? '0 4px 12px rgba(0,0,0,0.15)' : '0 2px 6px rgba(0,0,0,0.06)',
              transition: 'transform 0.2s, box-shadow 0.2s',
              cursor: 'default',
            }}>
              {/* Rank badge */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                {isTop3 ? (
                  <div style={{
                    background: 'rgba(255,255,255,0.3)',
                    borderRadius: '50%',
                    width: 26,
                    height: 26,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                    fontWeight: 700,
                    color: '#fff',
                  }}>
                    #{index + 1}
                  </div>
                ) : (
                  <Text style={{ fontSize: 11, color: 'rgba(0,0,0,0.25)' }}>#{index + 1}</Text>
                )}
                <Text strong style={{
                  fontSize: 13,
                  color: isTop3 ? '#fff' : '#333',
                  maxWidth: 120,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>{item.name}</Text>
              </div>

              {/* Progress mini bar */}
              <div style={{ marginTop: 8 }}>
                <div style={{
                  background: isTop3 ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.06)',
                  borderRadius: 3,
                  height: 5,
                  overflow: 'hidden',
                }}>
                  <div style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: isTop3 ? 'rgba(255,255,255,0.8)' : '#5b46f5',
                    borderRadius: 3,
                    transition: 'width 0.6s ease',
                  }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                  <Text style={{ fontSize: 11, color: isTop3 ? 'rgba(255,255,255,0.8)' : '#888' }}>{pct}%</Text>
                  <Text strong style={{ fontSize: 18, color: isTop3 ? '#fff' : '#333' }}>{item.count}</Text>
                </div>
              </div>
            </div>
          </Col>
        )
      })}
    </Row>
  )
}

function ClassificationTab({ data, loading }: { data: ClassificationSummary | null; loading: boolean }) {
  const pieData = data?.type_distribution
    ? Object.entries(data.type_distribution).map(([name, value]) => ({ name, value }))
    : []

  const hotspotModules = data
    ? Object.entries(data.module_hotspot)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => ({ name, count }))
    : []
  const hotspotMax = hotspotModules.length > 0 ? hotspotModules[0].count : 1

  return (
    <Spin spinning={loading}>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic title="总提交数" value={data?.total_commits ?? 0} valueStyle={{ color: '#5b46f5' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="BugFix 占比" value={data ? Math.round(data.type_percentage.BugFix ?? 0) : 0} suffix="%" valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Feature 占比" value={data ? Math.round(data.type_percentage.Feature ?? 0) : 0} suffix="%" valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="芯片特定提交" value={data?.chip_specific_count ?? 0} valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>

        <Col span={12}>
          <Card title="提交类型分布">
            <div style={{ height: 400 }}>
              {pieData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={150} label={({ name, value }) => `${name}: ${value}`}>
                      {pieData.map((_entry, index) => (
                        <Cell key={`cell-${index}`} fill={pieColors[index % pieColors.length]} />
                      ))}
                    </Pie>
                    <ChartsTooltip formatter={(value: number) => `${value} 次`} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <Text type="secondary">暂无数据</Text>
                </div>
              )}
            </div>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="模块热点">
            <ModuleHotspotGrid modules={hotspotModules} maxCount={hotspotMax} />
          </Card>
        </Col>
      </Row>
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                            构建 Tab 内容                            */
/* ------------------------------------------------------------------ */

function BuildsTab() {
  const [buildStats, setBuildStats] = useState<BuildOptimizationStats | null>(null)
  const [statsLoading, setStatsLoading] = useState(false)

  const [plans, setPlans] = useState<BuildPlan[]>([])
  const [plansTotal, setPlansTotal] = useState(0)
  const [plansLoading, setPlansLoading] = useState(false)

  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [filterCommitType, setFilterCommitType] = useState<string | undefined>(undefined)
  const [filterBuildType, setFilterBuildType] = useState<string | undefined>(undefined)
  const [filterDateRange, setFilterDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null)
  const [filterOptions, setFilterOptions] = useState<{ commitTypes: string[]; buildTypes: string[] }>({ commitTypes: [], buildTypes: [] })
  // 加载统计数据
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setStatsLoading(true)
      try {
        const [statsData, optionsData] = await Promise.all([
          getBuildOptimizationStats(),
          getBuildFilterOptions(),
        ])
        if (!cancelled) {
          await new Promise(r => setTimeout(r, 400))
          setBuildStats(statsData)
          setFilterOptions({
            commitTypes: optionsData.commit_types || [],
            buildTypes: optionsData.build_types || [],
          })
        }
      } catch { /* ignore */ } finally {
        if (!cancelled) setStatsLoading(false)
      }
    }
    load().then()
    return () => { cancelled = true }
  }, [])

  // 加载构建计划列表
  const fetchPlans = useCallback(async (page: number, size: number) => {
    setPlansLoading(true)
    try {
      const data = await getBuildPlans({
        commit_type: filterCommitType || null,
        build_type: filterBuildType || null,
        created_after: filterDateRange?.[0]?.format('YYYY-MM-DD') || null,
        created_before: filterDateRange?.[1]?.format('YYYY-MM-DD') || null,
        limit: size,
        offset: (page - 1) * size,
      })
      await new Promise(r => setTimeout(r, 300))
      setPlans(data.plans)
      setPlansTotal(data.total)
    } catch { /* ignore */ } finally {
      setPlansLoading(false)
    }
  }, [filterCommitType, filterBuildType, filterDateRange])

  useEffect(() => {
    fetchPlans(pagination.current, pagination.pageSize).then()
  }, [fetchPlans, pagination.current, pagination.pageSize])

  const handleExportCSV = () => {
    const headers = ['PR 号', 'Commit SHA', 'Commit Type', 'Build Type', '节省比例', '推理说明', '创建时间']
    const rows = plans.map(p => [
      `#${p.pr_number}`, p.commit_sha.slice(0, 8), p.commit_type,
      buildTypeLabels[p.build_type] || p.build_type, `${Math.round(p.estimated_saving_percent)}%`,
      p.reasoning || '-', p.created_at ? dayjs(p.created_at).format('YYYY-MM-DD HH:mm') : '-',
    ])
    const csvContent = ['\uFEFF', headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `构建计划_${dayjs().format('YYYYMMDD')}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleClearFilters = () => {
    setFilterCommitType(undefined)
    setFilterBuildType(undefined)
    setFilterDateRange(null)
    setPagination(p => ({ ...p, current: 1 }))
  }

  const hasActiveFilter = filterCommitType || filterBuildType || filterDateRange

  const buildPlanColumns = [
    {
      title: 'PR 号',
      dataIndex: 'pr_number',
      key: 'pr_number',
      width: 80,
      render: (prNumber: number) => (
        <a href={`https://github.com/vllm-project/vllm-ascend/pull/${prNumber}`} target="_blank" rel="noopener noreferrer">
          #{prNumber}
        </a>
      ),
    },
    {
      title: 'Commit SHA',
      dataIndex: 'commit_sha',
      key: 'commit_sha',
      width: 120,
      render: (sha: string) => <Text code>{sha.slice(0, 8)}</Text>,
    },
    {
      title: 'Commit Type',
      dataIndex: 'commit_type',
      key: 'commit_type',
      width: 120,
      render: (type: string) => <Tag color={typeColors[type] || 'default'}>{type}</Tag>,
    },
    {
      title: 'Build Type',
      dataIndex: 'build_type',
      key: 'build_type',
      width: 120,
      render: (type: string) => (
        <Tag color={buildTypeColors[type] || 'default'}>
          {buildTypeLabels[type] || type}
        </Tag>
      ),
    },
    {
      title: '节省比例',
      dataIndex: 'estimated_saving_percent',
      key: 'estimated_saving_percent',
      width: 100,
      render: (pct: number) => (
        <Tag color={pct > 50 ? 'green' : pct > 20 ? 'blue' : 'default'}>
          {Math.round(pct)}%
        </Tag>
      ),
    },
    {
      title: '推理说明',
      dataIndex: 'reasoning',
      key: 'reasoning',
      ellipsis: true,
      width: 300,
      render: (text: string | null) => text || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (date: string | null) => date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-',
    },
  ]

  return (
    <Spin spinning={statsLoading}>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic title="总评估数" value={buildStats?.total_evaluations ?? 0} valueStyle={{ color: '#5b46f5' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已跳过构建" value={buildStats?.skipped_builds ?? 0} suffix="次" valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="减少构建" value={buildStats?.reduced_builds ?? 0} suffix="次" valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="平均节省" value={buildStats ? Math.round(buildStats.avg_saving_percent) : 0} suffix="%" valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>

        <Col span={24}>
          <Card
            title="构建计划记录"
            extra={
              <Space size="small">
                <Select
                  allowClear
                  placeholder="Commit Type"
                  value={filterCommitType}
                  onChange={(v) => { setFilterCommitType(v); setPagination(p => ({ ...p, current: 1 })) }}
                  style={{ width: 150 }}
                  options={filterOptions.commitTypes.map(t => ({ label: t, value: t }))}
                />
                <Select
                  allowClear
                  placeholder="Build Type"
                  value={filterBuildType}
                  onChange={(v) => { setFilterBuildType(v); setPagination(p => ({ ...p, current: 1 })) }}
                  style={{ width: 160 }}
                  options={filterOptions.buildTypes.map(t => ({ label: buildTypeLabels[t] || t, value: t }))}
                />
                <DatePicker.RangePicker
                  value={filterDateRange}
                  onChange={(dates) => { setFilterDateRange(dates as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null); setPagination(p => ({ ...p, current: 1 })) }}
                  placeholder={['开始日期', '结束日期']}
                />
                {hasActiveFilter && (
                  <Button size="small" icon={<FilterOutlined />} onClick={handleClearFilters}>清除筛选</Button>
                )}
                <Button size="small" icon={<DownloadOutlined />} onClick={handleExportCSV}>导出CSV</Button>
              </Space>
            }
          >
            <Table
              columns={buildPlanColumns}
              dataSource={plans}
              loading={plansLoading}
              rowKey={(record: BuildPlan) => `${record.pr_number}-${record.commit_sha}`}
              pagination={{
                current: pagination.current,
                pageSize: pagination.pageSize,
                total: plansTotal,
                pageSizeOptions: [10, 20, 50],
                showSizeChanger: true,
                showTotal: (total) => `共 ${total} 条`,
                showQuickJumper: true,
                onChange: (page, size) => {
                  setPagination({ current: page, pageSize: size })
                },
              }}
              scroll={{ x: 1200 }}
            />
          </Card>
        </Col>
      </Row>
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                          四象限散点图 Tab                            */
/* ------------------------------------------------------------------ */

type ScatterDataPoint = {
  x: number
  y: number
  z: number
  login: string
  quadrant: string
}

const axisOptions = [
  { label: '出错评分', value: 'error_score' },
  { label: '检视次数', value: 'review_count' },
  { label: 'PR合并数', value: 'merged_pr_count' },
  { label: '代码行数', value: 'code_additions' },
]

const axisLabels: Record<string, string> = {
  error_score: '出错评分',
  review_count: '检视次数',
  merged_pr_count: 'PR合并数',
  code_additions: '代码行数',
}

function QuadrantTab({ refreshKey, selectedRepo }: { refreshKey?: number; selectedRepo?: string }) {
  const navigate = useNavigate()
  const chartRef = useRef<HTMLDivElement>(null)
  const [searchLogin, setSearchLogin] = useState('')
  const [quadrantData, setQuadrantData] = useState<QuadrantData | null>(null)
  const [quadrantLoading, setQuadrantLoading] = useState(true)
  const [yAxisType, setYAxisType] = useState('review_count')

  // 从设置加载初始 Y 轴配置
  useEffect(() => {
    let cancelled = false
    const loadSettings = async () => {
      try {
        const data = await getContributorBoardSettings()
        if (!cancelled) {
          setYAxisType(data.quadrant_config.y_axis_type)
        }
      } catch { /* ignore */ }
    }
    loadSettings()
    return () => { cancelled = true }
  }, [])

  // 当 refreshKey 变化时，重新从 settings 读取 Y 轴
  useEffect(() => {
    let cancelled = false
    const reloadAxis = async () => {
      try {
        const data = await getContributorBoardSettings()
        if (!cancelled) {
          setYAxisType(data.quadrant_config.y_axis_type)
        }
      } catch { /* ignore */ }
    }
    if (refreshKey && refreshKey > 0) {
      reloadAxis()
    }
  }, [refreshKey])

  const loadQuadrant = useCallback(async (axis: string, repo: string | undefined) => {
    setQuadrantLoading(true)
    try {
      const data = await getQuadrantData({ y_axis_type: axis, repo_name: repo || null })
      await new Promise(r => setTimeout(r, 400))
      setQuadrantData(data)
    } catch { /* silently fail */ } finally {
      setQuadrantLoading(false)
    }
  }, [])

  useEffect(() => {
    loadQuadrant(yAxisType, selectedRepo)
  }, [yAxisType, selectedRepo, loadQuadrant])

  const scatterData: ScatterDataPoint[] = (() => {
    const deduped = new Map<string, ScatterDataPoint>()
    ;(quadrantData?.points ?? []).forEach(p => {
      const pt: ScatterDataPoint = {
        x: p.contribution_score,
        y: p.y_value,
        z: p.merged_pr_count,
        login: p.login,
        quadrant: p.quadrant,
      }
      const existing = deduped.get(p.login)
      if (!existing || pt.z > existing.z || (pt.z === existing.z && pt.x > existing.x)) {
        deduped.set(p.login, pt)
      }
    })
    return Array.from(deduped.values())
  })()

  // 散点图缩放 — 鼠标滚轮缩放 + 拖拽平移
  const [zoomDomain, setZoomDomain] = useState<{ x: [number, number] | null; y: [number, number] | null }>({ x: null, y: null })

  const [isPanning, setIsPanning] = useState(false)
  const panStartRef = useRef<{ x: number; y: number } | null>(null)
  const panDomainRef = useRef<{ x: [number, number]; y: [number, number] }>({ x: [0, 1], y: [0, 1] })

  // 计算数据范围
  const xMax = Math.max(...scatterData.map(p => p.x), 1)
  const yMaxRaw = Math.max(...scatterData.map(p => p.y), 1)
  const yMax = Math.max(12, Math.ceil((yMaxRaw + 1) * 1.2))
  const xDomain: [number, number] = zoomDomain.x || [0, xMax]
  const yDomain: [number, number] = zoomDomain.y || [0, yMax]

  const hasZoom = zoomDomain.x !== null || zoomDomain.y !== null

  const isHighlightedPoint = useCallback((login: string) => {
    if (!searchLogin.trim()) return false
    return login.toLowerCase().includes(searchLogin.trim().toLowerCase())
  }, [searchLogin])

  const handlePointClick = (point: ScatterDataPoint | undefined) => {
    if (point?.login) {
      navigate(`/contributor-board/detail/${point.login}`)
    }
  }

  const handleLocate = () => {
    if (!searchLogin.trim()) return
    const found = scatterData.filter(p => p.login.toLowerCase().includes(searchLogin.trim().toLowerCase()))
    if (found.length > 0) {
      message.success(`找到 ${found.length} 位贡献者: ${found.map(p => p.login).join(', ')} (点击散点查看详情)`)
    } else {
      message.warning(`未找到匹配的贡献者: ${searchLogin}`)
    }
  }

  const handleResetZoom = useCallback(() => {
    setZoomDomain({ x: null, y: null })
  }, [])

  // 鼠标滚轮缩放 — 以光标为中心缩放
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const chartEl = chartRef.current
    if (!chartEl) return

    const rect = chartEl.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const mouseY = e.clientY - rect.top

    // 图表边距（与 recharts 默认大致一致）
    const margin = { left: 60, right: 20, top: 10, bottom: 40 }
    const plotLeft = margin.left
    const plotTop = margin.top
    const plotW = rect.width - margin.left - margin.right
    const plotH = rect.height - margin.top - margin.bottom

    if (mouseX < plotLeft || mouseX > plotLeft + plotW || mouseY < plotTop || mouseY > plotTop + plotH) return

    const curXDomain = zoomDomain.x || [0, xMax]
    const curYDomain = zoomDomain.y || [0, yMax]

    // 光标在 plot 区域中的相对位置 (0-1)
    const ratioX = (mouseX - plotLeft) / plotW
    const ratioY = 1 - (mouseY - plotTop) / plotH // Y 轴反转

    // 缩放因子：滚轮向上放大 (deltaY < 0)，向下缩小
    const zoomFactor = e.deltaY > 0 ? 1.15 : 1 / 1.15

    // 计算缩放后的 domain
    const xRange = curXDomain[1] - curXDomain[0]
    const yRange = curYDomain[1] - curYDomain[0]
    const mouseXVal = curXDomain[0] + xRange * ratioX
    const mouseYVal = curYDomain[0] + yRange * ratioY

    const newXRange = Math.max(xRange * zoomFactor, 1)
    const newYRange = Math.max(yRange * zoomFactor, 1)

    const newMinX = mouseXVal - newXRange * ratioX
    const newMaxX = mouseXVal + newXRange * (1 - ratioX)
    const newMinY = mouseYVal - newYRange * ratioY
    const newMaxY = mouseYVal + newYRange * (1 - ratioY)

    setZoomDomain({
      x: [newMinX, newMaxX],
      y: [newMinY, newMaxY],
    })
  }, [zoomDomain, scatterData, xMax, yMax])

  // 拖拽平移
  const handlePanStart = useCallback((e: React.MouseEvent) => {
    if (!hasZoom) return
    // 只在 Shift+左键或中键时触发拖拽平移
    if (!e.shiftKey && e.button !== 1) return
    e.preventDefault()
    setIsPanning(true)
    panStartRef.current = { x: e.clientX, y: e.clientY }
    panDomainRef.current = { x: [...xDomain] as [number, number], y: [...yDomain] as [number, number] }
  }, [hasZoom, xDomain, yDomain])

  const handlePanMove = useCallback((e: MouseEvent) => {
    if (!isPanning || !panStartRef.current) return
    const chartEl = chartRef.current
    if (!chartEl) return

    const rect = chartEl.getBoundingClientRect()
    const dx = e.clientX - panStartRef.current.x
    const dy = e.clientY - panStartRef.current.y

    const margin = { left: 60, right: 20 }
    const plotW = rect.width - margin.left - margin.right
    const plotH = rect.height - 50

    const panX = -(dx / plotW) * (panDomainRef.current.x[1] - panDomainRef.current.x[0])
    const panY = (dy / plotH) * (panDomainRef.current.y[1] - panDomainRef.current.y[0])

    setZoomDomain({
      x: [panDomainRef.current.x[0] + panX, panDomainRef.current.x[1] + panX],
      y: [panDomainRef.current.y[0] + panY, panDomainRef.current.y[1] + panY],
    })
  }, [isPanning])

  const handlePanEnd = useCallback(() => {
    setIsPanning(false)
    panStartRef.current = null
    document.removeEventListener('mousemove', handlePanMove)
    document.removeEventListener('mouseup', handlePanEnd)
  }, [handlePanMove])

  useEffect(() => {
    if (!isPanning) return
    document.addEventListener('mousemove', handlePanMove)
    document.addEventListener('mouseup', handlePanEnd)
    return () => {
      document.removeEventListener('mousemove', handlePanMove)
      document.removeEventListener('mouseup', handlePanEnd)
    }
  }, [isPanning, handlePanMove, handlePanEnd])

  const handleExportPNG = useCallback(() => {
    if (chartRef.current) {
      const svgEl = chartRef.current.querySelector('svg')
      if (svgEl) {
        const svgData = new XMLSerializer().serializeToString(svgEl)
        const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' })
        const url = URL.createObjectURL(svgBlob)
        const img = new Image()
        img.onload = () => {
          const canvas = document.createElement('canvas')
          canvas.width = svgEl.clientWidth * 2
          canvas.height = svgEl.clientHeight * 2
          const ctx = canvas.getContext('2d')
          if (ctx) {
            ctx.fillStyle = '#fff'
            ctx.fillRect(0, 0, canvas.width, canvas.height)
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
            const pngUrl = canvas.toDataURL('image/png')
            const a = document.createElement('a')
            a.href = pngUrl
            a.download = 'quadrant-chart.png'
            a.click()
            URL.revokeObjectURL(url)
          }
        }
        img.src = url
      }
    }
  }, [])

  const CustomTooltip = (props: { active?: boolean; payload?: { payload: ScatterDataPoint }[] }) => {
    if (props.active && props.payload?.[0]?.payload) {
      const p = props.payload[0].payload
      const isHighlighted = isHighlightedPoint(p.login)
      return (
        <Card size="small" style={{ minWidth: 180, border: isHighlighted ? '2px solid #ff4d4f' : undefined }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Avatar src={`https://avatars.githubusercontent.com/${p.login}`} size="small" icon={<GithubOutlined />} />
            <Text strong>{p.login}</Text>
            {isHighlighted && <Tag color="red">匹配</Tag>}
          </div>
          <Divider style={{ margin: '8px 0' }} />
          <Text>贡献分数: {p.x.toFixed(1)}</Text>
          <br />
          <Text>{axisLabels[yAxisType] || 'Y轴'}: {p.y.toFixed(1)}</Text>
          <br />
          <Text>合并 PR: {p.z}</Text>
          <br />
          <Tag color={quadrantColors[p.quadrant]} style={{ marginTop: 4 }}>
            {quadrantLabels[p.quadrant] || p.quadrant}
          </Tag>
        </Card>
      )
    }
    return null
  }

  return (
    <Spin spinning={quadrantLoading}>
      <Row gutter={[16, 16]}>
        {['INCENTIVE', 'IMPROVE', 'ENCOURAGE', 'REPLACE'].map(q => (
          <Col span={6} key={q}>
            <Card size="small">
              <Statistic title={quadrantLabels[q]} value={quadrantData?.quadrant_counts[q] ?? 0} valueStyle={{ color: quadrantColors[q] }} />
            </Card>
          </Col>
        ))}

        <Col span={24}>
          <Card
            title="四象限散点图"
            extra={
              <Space size="small" wrap>
                <Select
                  size="small"
                  value={yAxisType}
                  onChange={(v) => setYAxisType(v)}
                  options={axisOptions}
                  style={{ width: 110 }}
                />
                <Input
                  placeholder="输入用户名模糊高亮"
                  value={searchLogin}
                  onChange={e => setSearchLogin(e.target.value)}
                  allowClear
                  size="small"
                  style={{ width: 200 }}
                  prefix={<SearchOutlined />}
                  onPressEnter={handleLocate}
                />
                {hasZoom ? (
                  <Button size="small" danger onClick={handleResetZoom}>重置缩放</Button>
                ) : null}
                <Button size="small" onClick={handleExportPNG} icon={<DownloadOutlined />}>导出PNG</Button>
              </Space>
            }
          >
            <div
              ref={chartRef}
              onWheel={handleWheel}
              onMouseDown={handlePanStart}
              style={{
                height: 490,
                position: 'relative',
                cursor: isPanning ? 'grabbing' : hasZoom ? 'grab' : 'default',
              }}
              onMouseLeave={() => {
                if (isPanning) {
                  setIsPanning(false)
                  panStartRef.current = null
                }
              }}
            >
              {scatterData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart>
                      <CartesianGrid />
                      <XAxis type="number" dataKey="x" name="贡献分数" unit="分" domain={xDomain} />
                      <YAxis type="number" dataKey="y" name={axisLabels[yAxisType] || 'Y轴评分'} unit="次" domain={yDomain} allowDataOverflow={false} />
                      <ZAxis type="number" dataKey="z" range={[50, 400]} name="PR数" />
                      <ChartsTooltip content={<CustomTooltip />} cursor={{ stroke: '#999' }} />

                      {quadrantData?.quad_x != null && (
                        <>
                           <ReferenceLine x={quadrantData.quad_x} stroke="#999" strokeDasharray="4 4" label={{ value: `X=${quadrantData.quad_x.toFixed(0)}`, position: 'insideTopRight', fill: '#999' }} />
                           <ReferenceLine y={quadrantData.quad_y} stroke="#999" strokeDasharray="4 4" label={{ value: `Y=${quadrantData.quad_y.toFixed(0)}`, position: 'insideTopRight', fill: '#999' }} />
                        </>
                      )}

                      <Scatter
                        data={scatterData}
                        shape={(props: { cx?: number; cy?: number; payload?: ScatterDataPoint }) => {
                          const { cx, cy, payload } = props
                          if (cx == null || cy == null) return <circle cx={0} cy={0} r={0} />
                          const isHighlighted = payload ? isHighlightedPoint(payload.login) : false
                          return (
                            <g
                              onClick={(e) => {
                                e.stopPropagation()
                                if (payload) handlePointClick(payload)
                              }}
                              onMouseDown={(e) => {
                                // 阻止滚轮在SVG上触发浏览器的默认缩放行为
                                e.stopPropagation()
                              }}
                            >
                              <circle cx={cx} cy={cy} r={isHighlighted ? 16 : 10} fill="transparent" />
                              <circle
                                cx={cx}
                                cy={cy}
                                r={isHighlighted ? 8 : 5}
                                fill={quadrantColors[payload?.quadrant ?? ''] || '#d9d9d9'}
                                stroke={isHighlighted ? '#ff4d4f' : '#fff'}
                                strokeWidth={isHighlighted ? 3 : 2}
                                style={{ cursor: 'pointer' }}>
                                  <title>{payload?.login}</title>
                                </circle>
                                {isHighlighted && (
                                  <text x={cx} y={cy - 12} textAnchor="middle" fill="#ff4d4f" fontSize={11} fontWeight="bold">
                                    {payload?.login}
                                  </text>
                                )}
                            </g>
                          )
                        }}
                       />

                    </ScatterChart>
                  </ResponsiveContainer>


                </>
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <Text type="secondary">暂无数据 — 先执行同步采集</Text>
                </div>
              )}
            </div>
            {hasZoom && (
              <div style={{ marginTop: 4, padding: '6px 12px', background: '#f6f8fa', borderRadius: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  缩放范围: X {xDomain[0].toFixed(1)}~{xDomain[1].toFixed(1)}, Y {yDomain[0].toFixed(1)}~{yDomain[1].toFixed(1)}
                </Text>
                <Button type="link" size="small" danger onClick={handleResetZoom}>重置缩放</Button>
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </Spin>
    )
}

type SortRule = { field: string; order: 'asc' | 'desc' }

const sortableColumns: { key: string; label: string }[] = [
  { key: 'score', label: '分数' },
  { key: 'merged_pr_count', label: '合并 PR' },
  { key: 'pending_pr_count', label: '未合并 PR' },
  { key: 'commit_count', label: 'Commits' },
  { key: 'code_additions', label: '代码增加' },
  { key: 'code_deletions', label: '代码删除' },
  { key: 'review_count', label: 'Review 数' },
  { key: 'approval_count', label: 'Approval 数' },
  { key: 'login', label: '用户名' },
]

function RankingTab({ loading: _, selectedRepo }: { loading: boolean; selectedRepo: string | undefined }) {
  const navigate = useNavigate()
  const [roleType, setRoleType] = useState('developer')
  const [rankings, setRankings] = useState<RankingItem[]>([])
  const [rankingLoading, setRankingLoading] = useState(false)
  const [rankingTotal, setRankingTotal] = useState(0)
  const [syncLoading, setSyncLoading] = useState(false)
  const [roleStats, setRoleStats] = useState<RoleStats | null>(null)
  const [moduleHotspot, setModuleHotspot] = useState<ModuleHotspotResponse | null>(null)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })

  const [searchText, setSearchText] = useState('')
  const [sortModalOpen, setSortModalOpen] = useState(false)
  const [sortRules, setSortRules] = useState<SortRule[]>([{ field: 'score', order: 'desc' }])

  const [form] = Form.useForm()

  const fetchRanking = useCallback(async (page: number, size: number) => {
    setRankingLoading(true)
    try {
      const sortKey = sortRules.length > 0 ? sortRules[0].field : undefined
      const sortDir = sortRules.length > 0 ? sortRules[0].order : 'desc'
      const data = await getRanking(roleType, size, {
        repo_name: selectedRepo || null,
        sort_by: sortKey,
        sort_order: sortDir,
        offset: (page - 1) * size,
      })
      // 最小 loading 延迟 300ms，避免接口返回太快导致闪烁
      await new Promise(r => setTimeout(r, 300))
      setRankings(data.rankings)
      setRankingTotal(data.total)
    } finally {
      setRankingLoading(false)
    }
  }, [roleType, selectedRepo, sortRules])

  useEffect(() => {
    fetchRanking(pagination.current, pagination.pageSize)
  }, [fetchRanking, pagination.current, pagination.pageSize])

  useEffect(() => {
    let cancelled = false
    const loadStats = async () => {
      try {
        const [_roleStats, _moduleHotspot] = await Promise.all([
          getRoleStats({ repo_name: selectedRepo || undefined }),
          getModuleHotspot({
            repo_name: selectedRepo || null,
          }),
        ])
        if (!cancelled) {
          setRoleStats(_roleStats)
          setModuleHotspot(_moduleHotspot)
        }
      } catch { /* ignore */ }
    }
    loadStats()
    return () => { cancelled = true }
  }, [selectedRepo])

  const handleSync = async () => {
    setSyncLoading(true)
    try {
      await syncContributors(undefined, false)
      message.success('同步已触发，请在后台日志中查看进度')
      await fetchRanking(pagination.current, pagination.pageSize)
    } catch {
      message.error('同步触发失败')
    } finally {
      setSyncLoading(false)
    }
  }

  const handleExportCSV = () => {
    const headers = ['排名', '用户名', '工号', '分数', '贡献分数', '合并PR', '未合并PR', 'Commits', '代码增加', '代码删除', 'Review', 'Approval', '四象限', '角色']
    const rows = displayData.map((r, idx) => [
      idx + 1, r.login, r.employee_id || '', r.score.toFixed(1),
      r.contribution_score.toFixed(1), r.merged_pr_count, r.pending_pr_count,
      r.commit_count || 0, r.code_additions, r.code_deletions, r.review_count, r.approval_count,
      r.dynamic_quadrant || '', r.role_label || '',
    ])
    const csvContent = ['\uFEFF', headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `排行榜_${roleType}_${dayjs().format('YYYYMMDD')}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const roleTypeLabel = roleType === 'developer' ? '开发者' : roleType === 'reviewer' ? '检视者' : 'Maintainer'

  const applySort = useCallback((data: RankingItem[], rules: SortRule[]) => {
    if (rules.length === 0) return data
    return [...data].sort((a, b) => {
      for (const rule of rules) {
        const aVal = a[rule.field as keyof RankingItem]
        const bVal = b[rule.field as keyof RankingItem]
        let cmp = 0
        if (typeof aVal === 'string' && typeof bVal === 'string') {
          cmp = (aVal || '').localeCompare(bVal || '')
        } else {
          cmp = Number(aVal ?? 0) - Number(bVal ?? 0)
        }
        if (cmp !== 0) return rule.order === 'asc' ? cmp : -cmp
      }
      return 0
    })
  }, [])

  const displayData = useMemo(() => {
    const seen = new Set<string>()
    let data = rankings.filter(r => {
      if (seen.has(r.login)) return false
      seen.add(r.login)
      return true
    })
    if (searchText) {
      const lower = searchText.toLowerCase()
      data = data.filter(r =>
        r.login.toLowerCase().includes(lower) ||
        (r.employee_id && r.employee_id.toLowerCase().includes(lower)) ||
        (r.dynamic_quadrant && r.dynamic_quadrant.toLowerCase().includes(lower)) ||
        (r.role_label && r.role_label.toLowerCase().includes(lower)),
      )
    }
    return applySort(data, sortRules)
  }, [rankings, searchText, sortRules, applySort])

  const handleAddSortRule = () => {
    form.resetFields()
    setSortModalOpen(true)
  }

  const handleApplySort = () => {
    const values = form.getFieldsValue()
    setSortRules(prev => [...prev, { field: values.field, order: values.order || 'desc' }])
    setPagination(p => ({ ...p, current: 1 }))
    form.resetFields()
    setSortModalOpen(false)
  }

  const handleRemoveSortRule = (index: number) => {
    setSortRules(prev => prev.filter((_, i) => i !== index))
    setPagination(p => ({ ...p, current: 1 }))
  }

  const rankingColumns = [
    {
      title: '排名',
      dataIndex: 'rank',
      key: 'rank',
      width: 70,
      render: (rank: number) => (
        <Tag color={rank <= 3 ? 'gold' : rank <= 10 ? 'blue' : 'default'}>
          <TrophyOutlined /> #{rank}
        </Tag>
      ),
    },
    {
      title: '用户名',
      dataIndex: 'login',
      key: 'login',
      width: 160,
      render: (login: string, record: RankingItem) => (
        <Space>
          <Avatar src={record.avatar_url} size="small" icon={<GithubOutlined />} />
          <a onClick={() => navigate(`/contributor-board/detail/${login}`)} style={{ cursor: 'pointer' }}>
            {login}
          </a>
        </Space>
      ),
    },
    {
      title: '工号',
      dataIndex: 'employee_id',
      key: 'employee_id',
      width: 100,
      render: (id: string | null) => id ? <Tag color="cyan">{id}</Tag> : '-',
    },
    {
      title: '分数',
      dataIndex: 'score',
      key: 'score',
      width: 100,
      render: (score: number) => <Text strong>{score.toFixed(1)}</Text>,
    },
    {
      title: '合并 PR',
      dataIndex: 'merged_pr_count',
      key: 'merged_pr_count',
      width: 100,
      render: (val: number) => <Tag color="green">{val}</Tag>,
    },
    {
      title: '未合并 PR',
      dataIndex: 'pending_pr_count',
      key: 'pending_pr_count',
      width: 100,
      render: (val: number) => val > 0 ? <Tag color="orange">{val}</Tag> : '-',
    },
    {
      title: 'Commits',
      dataIndex: 'commit_count',
      key: 'commit_count',
      width: 90,
      render: (val: number) => val > 0 ? <Tag color="geekblue">{val}</Tag> : '-',
    },
    {
      title: '代码增加',
      dataIndex: 'code_additions',
      key: 'code_additions',
      width: 110,
      render: (val: number) => <Text style={{ color: '#52c41a' }}>+{val}</Text>,
    },
    {
      title: '代码删除',
      dataIndex: 'code_deletions',
      key: 'code_deletions',
      width: 110,
      render: (val: number) => <Text style={{ color: '#ff4d4f' }}>-{val}</Text>,
    },
    {
      title: 'Review 数',
      dataIndex: 'review_count',
      key: 'review_count',
      width: 100,
    },
    {
      title: 'Approval 数',
      dataIndex: 'approval_count',
      key: 'approval_count',
      width: 110,
    },
    {
      title: '四象限',
      dataIndex: 'dynamic_quadrant',
      key: 'dynamic_quadrant',
      width: 90,
      render: (q: string | null) => q ? <Tag color={quadrantColors[q]}>{quadrantLabels[q] || q}</Tag> : '-',
    },
    {
      title: '角色',
      dataIndex: 'role_label',
      key: 'role_label',
      width: 80,
      render: (r: string | null) => r ? <Tag color={quadrantRoleColors[r]}>{quadrantRoleLabels[r] || r}</Tag> : '-',
    },
  ]

  const hotspotModules = moduleHotspot?.modules
    ? moduleHotspot.modules.map(m => ({ name: m.module, count: m.count }))
    : []
  const hotspotMax = hotspotModules.length > 0 ? hotspotModules[0].count : 1

  const sortRuleLabels = sortRules.map(r =>
    `${sortableColumns.find(c => c.key === r.field)?.label ?? r.field} ${r.order === 'asc' ? '↑' : '↓'}`
  ).join(' → ')

  return (
    <Spin spinning={rankingLoading}>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={24}>
          <Space wrap>
            <Button type={roleType === 'developer' ? 'primary' : 'default'} onClick={() => { setRoleType('developer'); setPagination(p => ({ ...p, current: 1 })) }}>开发者排行</Button>
            <Button type={roleType === 'reviewer' ? 'primary' : 'default'} onClick={() => { setRoleType('reviewer'); setPagination(p => ({ ...p, current: 1 })) }}>检视者排行</Button>
            <Button type={roleType === 'maintainer' ? 'primary' : 'default'} onClick={() => { setRoleType('maintainer'); setPagination(p => ({ ...p, current: 1 })) }}>Maintainer 排行</Button>
            <Button icon={<ReloadOutlined />} loading={syncLoading} onClick={handleSync}>同步数据</Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportCSV}>导出CSV</Button>
          </Space>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="开发者人数" value={roleStats?.developer_count ?? 0} valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="检视者人数" value={roleStats?.reviewer_count ?? 0} valueStyle={{ color: '#722ed1' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="Maintainer 人数" value={roleStats?.maintainer_count ?? 0} valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="总 PR 数" value={roleStats?.total_pr ?? 0} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>

        <Col span={12}>
          <Card title="模块热度 Top 10" size="small" bodyStyle={{ padding: '12px 16px' }}>
            <ModuleHotspotGrid modules={hotspotModules.slice(0, 10)} maxCount={hotspotMax} />
          </Card>
        </Col>

        <Col span={12}>
          <Card title="角色统计详情" size="small">
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="总代码行数">{roleStats?.total_code_lines ?? 0} 行</Descriptions.Item>
              <Descriptions.Item label="总 Review 数">{roleStats?.total_reviews ?? 0} 次</Descriptions.Item>
              <Descriptions.Item label="总 Approval 数">{roleStats?.total_approvals ?? 0} 次</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      <Card
        title={`排行榜 - ${roleTypeLabel}${selectedRepo ? ` (${selectedRepo})` : ''}`}
        extra={
          <Space>
            <Input.Search
              placeholder="搜索用户名/工号"
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              allowClear
              style={{ width: 200 }}
              prefix={<SearchOutlined />}
            />
            <Button
              icon={<SortAscendingOutlined />}
              onClick={handleAddSortRule}
              style={sortRules.length > 0 ? { borderColor: '#1890ff', color: '#1890ff' } : undefined}
            >
              排序{sortRuleLabels ? ` (${sortRuleLabels})` : ''}
            </Button>
            {sortRules.length > 0 && (
              <Button danger size="small" onClick={() => setSortRules([])}>清除排序</Button>
            )}
          </Space>
        }
      >
        <Table
          columns={rankingColumns}
          dataSource={displayData}
          rowKey="login"
          loading={rankingLoading}
          pagination={{
            current: pagination.current,
            pageSize: pagination.pageSize,
            total: rankingTotal,
            pageSizeOptions: [10, 20, 50],
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
            showQuickJumper: true,
            onChange: (page, size) => {
              setPagination({ current: page, pageSize: size })
            },
          }}
        />
      </Card>

      <Modal
        title="配置排序规则"
        open={sortModalOpen}
        onOk={handleApplySort}
        onCancel={() => { form.resetFields(); setSortModalOpen(false) }}
        okText="添加排序"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="field" label="排序字段" rules={[{ required: true }]}>
            <Select options={sortableColumns.map(c => ({ label: c.label, value: c.key }))} />
          </Form.Item>
          <Form.Item name="order" label="排序方式" initialValue="desc">
            <Radio.Group>
              <Radio value="desc">降序 ↓</Radio>
              <Radio value="asc">升序 ↑</Radio>
            </Radio.Group>
          </Form.Item>
        </Form>

        {sortRules.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Text strong>当前排序规则：</Text>
            <div style={{ marginTop: 8 }}>
              {sortRules.map((rule, idx) => (
                <Tag
                  key={idx}
                  closable
                  onClose={() => handleRemoveSortRule(idx)}
                  style={{ marginBottom: 4, fontSize: 13 }}
                >
                  {idx + 1}. {sortableColumns.find(c => c.key === rule.field)?.label ?? rule.field} {rule.order === 'asc' ? '↑ 升序' : '↓ 降序'}
                </Tag>
              ))}
            </div>
          </div>
        )}
      </Modal>
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                            设置 Tab                                */
/* ------------------------------------------------------------------ */

function SettingsTab() {
  const [form] = Form.useForm()
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const data = await getContributorBoardSettings()
        if (!cancelled) {
          setSettings(data)
          form.setFieldValue('y_axis_type', data.quadrant_config.y_axis_type)
          form.setFieldValue('merged_pr_weight', data.scoring_weights.merged_pr_weight)
          form.setFieldValue('code_line_weight', data.scoring_weights.code_line_weight)
          form.setFieldValue('review_weight', data.scoring_weights.review_weight)
          form.setFieldValue('approval_weight', data.scoring_weights.approval_weight)
          form.setFieldValue('core_module_weight', data.scoring_weights.core_module_weight)
          form.setFieldValue('star_threshold', data.role_thresholds.star_threshold)
          form.setFieldValue('active_threshold', data.role_thresholds.active_threshold)
          form.setFieldValue('max_error_score', data.role_thresholds.max_error_score)
        }
      } catch { /* ignore */ } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [form])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      await Promise.all([
        updateQuadrantConfig(values.y_axis_type),
        updateScoringWeights({
          merged_pr_weight: values.merged_pr_weight,
          code_line_weight: values.code_line_weight,
          review_weight: values.review_weight,
          approval_weight: values.approval_weight,
          core_module_weight: values.core_module_weight,
        }),
        updateRoleThresholds({
          star_threshold: values.star_threshold,
          active_threshold: values.active_threshold,
          max_error_score: values.max_error_score,
        }),
      ])
      message.success('设置已保存，四象限和排行榜将使用新配置重新计算')
      // 通知四象限 Tab 刷新 Y 轴配置
      window.dispatchEvent(new CustomEvent('settings-updated'))
      // 重新加载
      const data = await getContributorBoardSettings()
      setSettings(data)
    } catch (err) {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const cardStyle = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
    border: '1px solid #f0f0f0',
  }

  const sliderFormItemStyle = {
    marginBottom: '14px',
  }

  return (
    <Spin spinning={loading}>
      {/* 顶部操作栏 */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 20,
      }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>
            <SettingOutlined style={{ marginRight: 8, color: '#5b46f5' }} />
            贡献者分析设置
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            以下配置将同时影响「四象限看板」和「排行榜中的象限标识」。修改后保存，系统将使用新配置重新计算。
          </Text>
        </div>
        <Button type="primary" loading={saving} onClick={handleSave}>
          保存设置
        </Button>
      </div>

      {/* 三卡片水平排列 */}
      <Row gutter={[20, 0]}>
        {/* 卡片 1: Y 轴配置 */}
        <Col xs={24} md={8}>
          <Card title={<><FlagOutlined style={{ marginRight: 6, color: '#1890ff' }} />Y 轴指标</>}
            size="small" style={cardStyle} bodyStyle={{ padding: '20px 24px 24px' }}>
            <Form form={form}>
              <Form.Item name="y_axis_type">
                <Radio.Group buttonStyle="solid" size="middle">
                  <Radio.Button value="review_count">检视次数</Radio.Button>
                  <Radio.Button value="error_score">出错评分</Radio.Button>
                  <Radio.Button value="merged_pr_count">PR合并数</Radio.Button>
                  <Radio.Button value="code_additions">代码行数</Radio.Button>
                </Radio.Group>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 卡片 2: 权重配置 */}
        <Col xs={24} md={8}>
          <Card title={<><TrophyOutlined style={{ marginRight: 6, color: '#fa8c16' }} />贡献分数权重</>}
            size="small" style={cardStyle} bodyStyle={{ padding: '20px 24px 24px' }}>
            <Form form={form}>
              <Form.Item label="PR 合并权重" name="merged_pr_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={20} step={1}
                  marks={{ 0: '0', 10: '10', 20: '20' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
              <Form.Item label="代码行权重" name="code_line_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={5} step={0.1}
                  marks={{ 0: '0', 2.5: '2.5', 5: '5' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(1) }} />
              </Form.Item>
              <Form.Item label="Review 权重" name="review_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={10} step={0.5}
                  marks={{ 0: '0', 5: '5', 10: '10' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(1) }} />
              </Form.Item>
              <Form.Item label="Approval 权重" name="approval_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={10} step={0.5}
                  marks={{ 0: '0', 5: '5', 10: '10' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(1) }} />
              </Form.Item>
              <Form.Item label="核心模块权重" name="core_module_weight" style={{ marginBottom: 0 }}>
                <Slider min={0} max={15} step={1}
                  marks={{ 0: '0', 8: '8', 15: '15' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 卡片 3: 阈值配置 */}
        <Col xs={24} md={8}>
          <Card title={<><StarOutlined style={{ marginRight: 6, color: '#faad14' }} />角色标签阈值</>}
            size="small" style={cardStyle} bodyStyle={{ padding: '20px 24px 24px' }}>
            <Form form={form}>
              <Form.Item label="明星分数阈值" name="star_threshold" style={sliderFormItemStyle}>
                <Slider min={0} max={100} step={5}
                  marks={{ 0: '0', 50: '50', 100: '100' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
              <Form.Item label="活跃分数阈值" name="active_threshold" style={sliderFormItemStyle}>
                <Slider min={0} max={50} step={5}
                  marks={{ 0: '0', 25: '25', 50: '50' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
              <Form.Item label="最大容错分数" name="max_error_score" style={{ marginBottom: 0 }}>
                <Slider min={0} max={10} step={0.5}
                  marks={{ 0: '0', 5: '5', 10: '10' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(1) }} />
              </Form.Item>
            </Form>
          </Card>
        </Col>
      </Row>
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                          贡献者详情页                              */
/* ------------------------------------------------------------------ */

function ContributorDetailPage() {
  const navigate = useNavigate()
  const { login } = useParams<{ login: string }>()
  const [detail, setDetail] = useState<ContributorDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [policy, setPolicy] = useState<PolicyResult | null>(null)
  const [trend, setTrend] = useState<ExtendedTrend | null>(null)

  useEffect(() => {
    loadAll()
  }, [login])

  const loadAll = async () => {
    if (!login) return
    setLoading(true)
    try {
      const [detailData, policyData, trendData] = await Promise.all([
        getContributorDetail(login).catch(() => null),
        getContributorPolicy(login).catch(() => null),
        getExtendedTrend(login).catch(() => null),
      ])
      setDetail(detailData)
      setPolicy(policyData)
      setTrend(trendData)
    } catch {
      message.error('加载贡献者详情失败')
    } finally {
      setLoading(false)
    }
  }

  if (loading) return <Spin size="large" />

  if (!detail) {
    return <Empty description={`未找到贡献者: ${login}`} />
  }

  return (
    <div>
      <Row gutter={[16, 0]} style={{ marginBottom: 16 }}>
        <Col span={22}>
          <Button onClick={() => navigate('/contributor-board')} style={{ marginBottom: 8 }}>← 返回看板</Button>
        </Col>
      </Row>

      <Card title={<Space><Avatar src={detail.avatar_url} size="large" icon={<GithubOutlined />} /> {detail.login}</Space>}>
        <Descriptions bordered column={2}>
          <Descriptions.Item label="姓名">{detail.name || '-'}</Descriptions.Item>
          <Descriptions.Item label="公司">{detail.company || '-'}</Descriptions.Item>
          <Descriptions.Item label="首次贡献">{detail.first_contribution_at ? dayjs(detail.first_contribution_at).format('YYYY-MM-DD') : '-'}</Descriptions.Item>
          <Descriptions.Item label="最近贡献">{detail.last_contribution_at ? dayjs(detail.last_contribution_at).format('YYYY-MM-DD') : '-'}</Descriptions.Item>
          <Descriptions.Item label="工号">{detail.employee_id ? <Tag color="cyan">{detail.employee_id}</Tag> : '-'}</Descriptions.Item>
          <Descriptions.Item label="类型">{detail.is_community ? <Tag color="blue">社区贡献者</Tag> : <Tag color="green">内部开发者</Tag>}</Descriptions.Item>
          <Descriptions.Item label="角色标签">
            {detail.role_types && detail.role_types.length > 0 ? (
              <Space>
                {detail.role_types.map((role) => (
                  <Tag key={role} color={roleColors[role] || 'default'}>
                    {roleLabels[role] || role}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Tag color="default">未分配角色</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="象限">{detail.quadrant ? <Tag color={quadrantColors[detail.quadrant]}>{quadrantLabels[detail.quadrant] || detail.quadrant}</Tag> : '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic title="合并 PR" value={detail.merged_pr_count} valueStyle={{ color: '#5b46f5' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="未合并 PR" value={detail.pending_pr_count} valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="代码添加" value={detail.code_additions} suffix="行" valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="代码删除" value={detail.code_deletions} suffix="行" valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Review 数" value={detail.review_count} valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="贡献分数" value={detail.contribution_score.toFixed(1)} valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>
      </Row>

      {policy && policy.policies.length > 0 && (
        <Card title="自动化策略建议" style={{ marginTop: 16 }}>
          {policy.has_critical && (
            <Alert message="存在需要立即处理的关键策略" type="error" showIcon style={{ marginBottom: 16 }} />
          )}
          {policy.has_warning && !policy.has_critical && (
            <Alert message="存在需要关注的改进策略" type="warning" showIcon style={{ marginBottom: 16 }} />
          )}
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {policy.policies.map((p, i) => (
              <Alert
                key={i}
                message={p.description}
                description={p.reason}
                type={p.severity === 'critical' ? 'error' : p.severity === 'warning' ? 'warning' : 'info'}
                showIcon
              />
            ))}
          </Space>
        </Card>
      )}

      <Divider />

      <Row gutter={[16, 16]}>
        <Col span={12}>
          <Card title="提交量趋势">
            <div style={{ height: 300 }}>
              {trend?.commit_trend && trend.commit_trend.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={trend.commit_trend}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                    <YAxis />
                    <ChartsTooltip />
                    <Legend />
                    <Bar dataKey="merged_pr_count" name="合并 PR" fill="#5b46f5" />
                    <Bar dataKey="code_additions" name="代码添加(行)" fill="#52c41a" />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="Review 趋势">
            <div style={{ height: 300 }}>
              {trend?.review_trend && trend.review_trend.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trend.review_trend}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                    <YAxis />
                    <ChartsTooltip />
                    <Legend />
                    <Line type="monotone" dataKey="review_count" name="Review 数" stroke="#1890ff" strokeWidth={2} />
                    <Line type="monotone" dataKey="approval_count" name="Approval 数" stroke="#722ed1" strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>

        <Col span={24}>
          <Card title="分数趋势 (贡献分数 vs 出错分数)">
            <div style={{ height: 300 }}>
              {trend?.score_trend && trend.score_trend.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trend.score_trend}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                    <YAxis />
                    <ChartsTooltip />
                    <Legend />
                    <Line type="monotone" dataKey="contribution_score" name="贡献分数" stroke="#52c41a" strokeWidth={2} />
                    <Line type="monotone" dataKey="error_score" name="出错分数" stroke="#ff4d4f" strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*                           主页面组件                                */
/* ------------------------------------------------------------------ */

function ContributorBoard() {
  const [classificationData, setClassificationData] = useState<ClassificationSummary | null>(null)
  const [classificationLoading, setClassificationLoading] = useState(false)

  const [repos, setRepos] = useState<string[]>([])
  const [selectedRepo, setSelectedRepo] = useState<string | undefined>(undefined)

  // 监听 SettingsTab 发出的配置更新事件
  const [settingsRefreshTrigger, setSettingsRefreshTrigger] = useState(0)
  useEffect(() => {
    const handler = () => setSettingsRefreshTrigger(t => t + 1)
    window.addEventListener('settings-updated', handler)
    return () => window.removeEventListener('settings-updated', handler)
  }, [])

  useEffect(() => {
    loadClassification()
    loadRepos()
  }, [])

  const loadRepos = async () => {
    try {
      const data = await getReposList()
      setRepos(data.repos ?? [])
    } catch { /* ignore */ }
  }

  const loadClassification = async () => {
    setClassificationLoading(true)
    try {
      const data = await getClassificationSummary()
      await new Promise(r => setTimeout(r, 400))
      setClassificationData(data)
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } }; message?: string }
      message.error('加载分类统计失败：' + (err.response?.data?.detail || err.message || '未知错误'))
    } finally {
      setClassificationLoading(false)
    }
  }


  const tabItems = [
    {
      key: 'classification',
      label: <><CodeOutlined /> 提交分类统计</>,
      children: <ClassificationTab data={classificationData} loading={classificationLoading} />,
    },
    {
      key: 'builds',
      label: <><RocketOutlined /> 构建优化统计</>,
      children: <BuildsTab />,
    },
    {
      key: 'quadrant',
      label: <><GithubOutlined /> 四象限看板</>,
      children: <QuadrantTab refreshKey={settingsRefreshTrigger} />,
    },
    {
      key: 'ranking',
      label: <><TrophyOutlined /> 排行榜</>,
      children: <RankingTab loading={false} selectedRepo={selectedRepo} />,
    },
    {
      key: 'settings',
      label: <><ToolOutlined /> 分析设置</>,
      children: <SettingsTab />,
    },
  ]

  return (
    <div className="stripe-project-page">
      <div className="stripe-page-header">
        <Title level={3} className="stripe-page-title">
          <GithubOutlined className="stripe-page-icon" />
          贡献者与提交分析
        </Title>
        <Text className="stripe-page-description">
          vLLM Ascend 提交分类、构建优化与贡献者分析
        </Text>
      </div>

      {repos.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <Select
            allowClear
            placeholder="选择仓库（影响排行榜 Tab）"
            value={selectedRepo}
            onChange={setSelectedRepo}
            style={{ width: 260 }}
            options={repos.map(r => ({ label: r, value: r }))}
            suffixIcon={<SearchOutlined />}
          />
        </div>
      )}

      <Tabs defaultActiveKey="classification" items={tabItems} />
    </div>
  )
}

export { ContributorDetailPage }
export default ContributorBoard
