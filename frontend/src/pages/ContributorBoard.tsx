import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Card,
  Table,
  Tag,
  Space,
  Statistic,
  Spin,
  message,
  Typography,
  Row,
  Col,
  Tabs,
  Button,
  Avatar,
  Descriptions,
  Empty,
  Alert,
  Divider,
  Select,
  DatePicker,
  Progress,
  Input,
  Modal,
  Form,
  Radio,
} from 'antd'
import {
  GithubOutlined,
  CodeOutlined,
  RocketOutlined,
  TrophyOutlined,
  ReloadOutlined,
  DownloadOutlined,
  SearchOutlined,
  SortAscendingOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import {
  PieChart,
  Pie,
  Cell,
  Tooltip as ChartsTooltip,
  ResponsiveContainer,
  Legend,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  ZAxis,
  ReferenceLine,
  LineChart,
  Line,
  BarChart,
  Bar,
} from 'recharts'
import dayjs from 'dayjs'
import {
  getClassificationSummary,
  getBuildOptimizationStats,
  getBuildPlans,
  getQuadrantData,
  getContributorDetail,
  getRanking,
  getRoleStats,
  getModuleHotspot,
  getContributorPolicy,
  getExtendedTrend,
  syncContributors,
  getReposList,
  type ClassificationSummary,
  type BuildOptimizationStats,
  type BuildPlan,
  type QuadrantData,
  type ContributorDetail,
  type RankingItem,
  type RoleStats,
  type ModuleHotspotResponse,
  type PolicyResult,
  type ExtendedTrend,
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

function BuildsTab({ stats, statsLoading, plans, plansTotal, plansLoading }: {
  stats: BuildOptimizationStats | null
  statsLoading: boolean
  plans: BuildPlan[]
  plansTotal: number
  plansLoading: boolean
}) {
  const buildPlanColumns = [
    {
      title: 'PR 号',
      dataIndex: 'pr_number',
      key: 'pr_number',
      width: 80,
      sorter: (a: BuildPlan, b: BuildPlan) => a.pr_number - b.pr_number,
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
            <Statistic title="总评估数" value={stats?.total_evaluations ?? 0} valueStyle={{ color: '#5b46f5' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已跳过构建" value={stats?.skipped_builds ?? 0} suffix="次" valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="减少构建" value={stats?.reduced_builds ?? 0} suffix="次" valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="平均节省" value={stats ? Math.round(stats.avg_saving_percent) : 0} suffix="%" valueStyle={{ color: '#fa8c16' }} />
          </Card>
        </Col>

        <Col span={24}>
          <Card title="构建计划记录">
            <Table
              columns={buildPlanColumns}
              dataSource={plans}
              loading={plansLoading}
              rowKey={(record) => `${record.pr_number}-${record.commit_sha}`}
              pagination={{ total: plansTotal, pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
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

function QuadrantTab({ data, loading }: { data: QuadrantData | null; loading: boolean }) {
  const navigate = useNavigate()
  const chartRef = useRef<HTMLDivElement>(null)
  const [searchLogin, setSearchLogin] = useState('')

  const scatterData: ScatterDataPoint[] = (() => {
    const deduped = new Map<string, ScatterDataPoint>()
    ;(data?.points ?? []).forEach(p => {
      const pt: ScatterDataPoint = {
        x: p.contribution_score,
        y: p.error_score,
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

  // 散点图缩放状态（鼠标滚轮缩放 + 重置）
  const [zoomDomain, setZoomDomain] = useState<{ x: [number, number] | null; y: [number, number] | null }>({ x: null, y: null })

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

  const handleClearSearch = () => {
    setSearchLogin('')
  }

  const handleWheelZoom = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    setZoomDomain({ x: null, y: null })
  }, [])

  const handleResetZoom = () => {
    setZoomDomain({ x: null, y: null })
  }

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
          <Text>出错分数: {p.y.toFixed(1)}</Text>
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
    <Spin spinning={loading}>
      <Row gutter={[16, 16]}>
        {['INCENTIVE', 'IMPROVE', 'ENCOURAGE', 'REPLACE'].map(q => (
          <Col span={6} key={q}>
            <Card size="small">
              <Statistic title={quadrantLabels[q]} value={data?.quadrant_counts[q] ?? 0} valueStyle={{ color: quadrantColors[q] }} />
            </Card>
          </Col>
        ))}

        <Col span={24}>
          <Card
            title="四象限散点图"
            extra={
              <Space size="small">
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
                {zoomDomain.x || zoomDomain.y ? (
                  <Button size="small" danger onClick={handleResetZoom}>重置缩放</Button>
                ) : null}
                <Button size="small" onClick={handleExportPNG} icon={<DownloadOutlined />}>导出PNG</Button>
              </Space>
            }
          >
            <div style={{ height: 530 }} ref={chartRef}>
              {scatterData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart>
                    <CartesianGrid />
                    <XAxis type="number" dataKey="x" name="贡献分数" unit="分" domain={zoomDomain.x || [0, 'dataMax']} />
                    <YAxis type="number" dataKey="y" name="出错分数" unit="分" domain={zoomDomain.y || [0, 'auto']} allowDataOverflow={false} />
                    <ZAxis type="number" dataKey="z" range={[50, 400]} name="PR数" />
                    <ChartsTooltip content={<CustomTooltip />} cursor={{ stroke: '#999' }} />

                    {data?.quad_x != null && (
                      <>
                        <ReferenceLine x={data.quad_x} stroke="#999" strokeDasharray="4 4" label={{ value: '中位数', position: 'insideTopRight', fill: '#999' }} />
                        <ReferenceLine y={data.quad_y} stroke="#999" strokeDasharray="4 4" label={{ value: '中位数', position: 'insideTopRight', fill: '#999' }} />
                      </>
                    )}

                    <Scatter
                      data={scatterData}
                      shape={(props: { cx?: number; cy?: number; payload?: ScatterDataPoint }) => {
                        const { cx, cy, payload } = props
                        if (cx == null || cy == null) return <circle cx={0} cy={0} r={0} />
                        const isHighlighted = payload ? isHighlightedPoint(payload.login) : false
                        return (
                          <g onClick={(e) => {
                            e.stopPropagation()
                            if (payload) handlePointClick(payload)
                          }}>
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
              ) : (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <Text type="secondary">暂无数据 — 先执行同步采集</Text>
                </div>
              )}
            </div>
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
  const [syncLoading, setSyncLoading] = useState(false)
  const [roleStats, setRoleStats] = useState<RoleStats | null>(null)
  const [moduleHotspot, setModuleHotspot] = useState<ModuleHotspotResponse | null>(null)
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null)
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
        period_start: dateRange?.[0]?.format('YYYY-MM-DD') || null,
        period_end: dateRange?.[1]?.format('YYYY-MM-DD') || null,
        sort_by: sortKey,
        sort_order: sortDir,
      })
      // 最小 loading 延迟 300ms，避免接口返回太快导致闪烁
      await new Promise(r => setTimeout(r, 300))
      setRankings(data.rankings)
    } finally {
      setRankingLoading(false)
    }
  }, [roleType, selectedRepo, dateRange, sortRules])

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
            period_start: dateRange?.[0]?.format('YYYY-MM-DD') || null,
            period_end: dateRange?.[1]?.format('YYYY-MM-DD') || null,
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
  }, [selectedRepo, dateRange])

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
    const headers = ['排名', '用户名', '工号', '分数', '贡献分数', '合并PR', '未合并PR', '代码增加', '代码删除', 'Review', 'Approval', '象限']
    const rows = displayData.map((r, idx) => [
      idx + 1, r.login, r.employee_id || '', r.score.toFixed(1),
      r.contribution_score.toFixed(1), r.merged_pr_count, r.pending_pr_count,
      r.code_additions, r.code_deletions, r.review_count, r.approval_count, r.quadrant || '',
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
    const unique = rankings.filter(r => {
      if (seen.has(r.login)) return false
      seen.add(r.login)
      return true
    })
    let data = unique
    if (searchText) {
      const lower = searchText.toLowerCase()
      data = data.filter(r =>
        r.login.toLowerCase().includes(lower) ||
        (r.employee_id && r.employee_id.toLowerCase().includes(lower)) ||
        (r.quadrant && r.quadrant.toLowerCase().includes(lower)),
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
      title: '象限',
      dataIndex: 'quadrant',
      key: 'quadrant',
      width: 100,
      render: (quadrant: string | null) => quadrant ? <Tag color={quadrantColors[quadrant]}>{quadrantLabels[quadrant] || quadrant}</Tag> : '-',
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
            <span style={{ borderLeft: '1px solid #d9d9d9', margin: '0 4px' }} />
            <DatePicker.RangePicker
              value={dateRange}
              onChange={(dates) => { setDateRange(dates as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null); setPagination(p => ({ ...p, current: 1 })) }}
              placeholder={['起始日期', '结束日期']}
            />
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
        title={`排行榜 - ${roleTypeLabel}${selectedRepo ? ` (${selectedRepo})` : ''}${dateRange ? ` (${dateRange[0]?.format('MM/DD')}-${dateRange[1]?.format('MM/DD')})` : ''}`}
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

  const [buildStats, setBuildStats] = useState<BuildOptimizationStats | null>(null)
  const [buildStatsLoading, setBuildStatsLoading] = useState(false)

  const [buildPlans, setBuildPlans] = useState<BuildPlan[]>([])
  const [buildPlansTotal, setBuildPlansTotal] = useState(0)
  const [buildPlansLoading, setBuildPlansLoading] = useState(false)

  const [quadrantData, setQuadrantData] = useState<QuadrantData | null>(null)
  const [quadrantLoading, setQuadrantLoading] = useState(false)

  const [repos, setRepos] = useState<string[]>([])
  const [selectedRepo, setSelectedRepo] = useState<string | undefined>(undefined)

  useEffect(() => {
    loadClassification()
    loadBuildStats()
    loadBuildPlans()
    loadQuadrantData()
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

  const loadBuildStats = async () => {
    setBuildStatsLoading(true)
    try {
      const data = await getBuildOptimizationStats()
      await new Promise(r => setTimeout(r, 400))
      setBuildStats(data)
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } }; message?: string }
      message.error('加载构建优化统计失败：' + (err.response?.data?.detail || err.message || '未知错误'))
    } finally {
      setBuildStatsLoading(false)
    }
  }

  const loadBuildPlans = async () => {
    setBuildPlansLoading(true)
    try {
      const data = await getBuildPlans(undefined, 100, 0)
      await new Promise(r => setTimeout(r, 400))
      setBuildPlans(data.plans)
      setBuildPlansTotal(data.total)
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } }; message?: string }
      message.error('加载构建计划失败：' + (err.response?.data?.detail || err.message || '未知错误'))
    } finally {
      setBuildPlansLoading(false)
    }
  }

  const loadQuadrantData = async () => {
    setQuadrantLoading(true)
    try {
      const data = await getQuadrantData()
      await new Promise(r => setTimeout(r, 400))
      setQuadrantData(data)
    } catch {
      // silently fail - quadrant data may require admin role
    } finally {
      setQuadrantLoading(false)
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
      children: <BuildsTab stats={buildStats} statsLoading={buildStatsLoading} plans={buildPlans} plansTotal={buildPlansTotal} plansLoading={buildPlansLoading} />,
    },
    {
      key: 'quadrant',
      label: <><GithubOutlined /> 四象限看板</>,
      children: <QuadrantTab data={quadrantData} loading={quadrantLoading} />,
    },
    {
      key: 'ranking',
      label: <><TrophyOutlined /> 排行榜</>,
      children: <RankingTab loading={quadrantLoading} selectedRepo={selectedRepo} />,
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
