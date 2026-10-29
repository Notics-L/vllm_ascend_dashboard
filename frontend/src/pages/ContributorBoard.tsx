import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import * as echarts from 'echarts'
import {useNavigate, useParams} from 'react-router-dom'

/** 保存当前 tab/repo 到 sessionStorage 以便详情页返回时恢复，然后导航到详情页 */
function navigateToDetail(navigate: ReturnType<typeof useNavigate>, login: string, tab: string, repo: string | undefined) {
  try {
    if (tab !== 'classification') sessionStorage.setItem('cb_last_tab', tab)
    if (repo) sessionStorage.setItem('cb_last_repo', repo)
  } catch { /* ignore */ }
  navigate(`/contributor-board/detail/${login}`)
}
import {
  Alert,
  Avatar,
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Progress,
  Radio,
  Result,
  Row,
  Select,
  Slider,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd'
import {
  CalendarOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  CloudUploadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ColumnHeightOutlined,
  DeleteOutlined,
  EyeOutlined,
  ExperimentOutlined,
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
  StopOutlined,
  ToolOutlined,
  TrophyOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { formatTimezone, fromTimezoneNow } from '@/utils/timezone'
import {
  type BuildOptimizationStats,
  type BuildPlan,
  type ClassificationSummary,
  type ContributorDetail,
  ExtendedTrend,
  getExtendedTrend,
  getBuildFilterOptions,
  getBuildOptimizationStats,
  getBuildPlans,
  getClassificationSummary,
  getContributorDetail,
  getContributorDistribution,
  getContributorHeatmap,
  getContributorPolicy,
  getContributorTimeline,
  type ContributorHeatmap as HeatmapType,
  type ContributorDistribution as DistributionType,
  type ContributorTimeline as TimelineType,
  type HeatmapDataPoint,
  type ModuleDistributionItem,
  type TypeDistributionItem,
  type TimelineCommitItem,
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
  recalculateScores,
  getActiveScoringVersion,
  listScoringVersions,
  listScoreSnapshots,
  listScoreAuditLogs,
  createScoringVersion,
  applyScoringVersion,
  rollbackScoringVersion,
  compareScoringVersions,
  calculateAutoWeights,
  aiAnalyzeWeights,
  exportPdfByVersion,
  generateAIReport,
  listAIReports,
  getAIReportDetail,
  generateScoreSnapshot,
  type AIReportListItem,
  type AIReportDetail as AIReportDetailType,
  type ScoringVersion,
  type VersionCompareResult,
  type ScoreSnapshot,
  type ScoreUpdateAudit,
  WEIGHT_DIMENSIONS,
  WEIGHT_LABELS,
  METHOD_CONFIG,
  normalizeWeights,
   type AutoWeightsResult,
  getSyncSchedule,
  updateSyncSchedule,
  type ContributorSyncScheduleConfig,
    syncContributors,
    type SyncRecord,
    type SyncSummary,
    getSyncStatus,
    getSyncHistory,
    getSyncSummary,
    cancelSync,
} from '../services/contributorBoard'
import { getLLMProviders, type LLMProvider } from '../services/dailySummary'

const { Title, Text, Paragraph } = Typography

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

// 提交类型映射（用于 Timeline 和类型分布图表）
const classificationLabels: Record<string, string> = {
  BugFix: 'Bug 修复',
  Feature: '新功能',
  Performance: '性能优化',
  Revert: '回滚',
  CI: 'CI/CD',
  Doc: '文档',
  Test: '测试',
  Refactor: '重构',
  Misc: '其他',
}

const classificationColors: Record<string, string> = {
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

const chartColors = ['#5b46f5', '#52c41a', '#ff4d4f', '#1890ff', '#fa8c16', '#722ed1', '#13c2c2', '#eb2f96', '#d9d9d9']

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

function EChartsPieChart({ data }: { data: Array<{ name: string; value: number }> }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const inst = echarts.init(el)
    inst.setOption({
      tooltip: {
        trigger: 'item',
        formatter: '{b}: {c} 次 ({d}%)',
      },
      legend: {
        type: 'scroll',
        orient: 'vertical',
        right: 10,
        top: 'center',
        textStyle: { fontSize: 12 },
      },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['35%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}: {c}' },
        emphasis: {
          label: { show: true, fontSize: 14, fontWeight: 'bold' },
        },
        data: data.map((item, i) => ({
          ...item,
          itemStyle: { color: pieColors[i % pieColors.length] },
        })),
      }],
    }, true)
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}

function EChartsBarChart({ data }: { data: Array<{ date: string; merged_pr_count: number; code_additions: number }> }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const inst = echarts.init(el)
    inst.setOption({
      tooltip: { trigger: 'axis' },
      legend: { bottom: 4, textStyle: { fontSize: 11 } },
      grid: { left: 50, right: 16, top: 10, bottom: 40 },
      xAxis: {
        type: 'category',
        data: data.map(d => d.date),
        axisTick: { alignWithLabel: true },
        axisLabel: { fontSize: 11 },
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } } },
      series: [
        { name: '合并 PR', type: 'bar', data: data.map(d => d.merged_pr_count), itemStyle: { color: '#5b46f5' } },
        { name: '代码添加(行)', type: 'bar', data: data.map(d => d.code_additions), itemStyle: { color: '#52c41a' } },
      ],
    }, true)
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}

function EChartsLineChart<T extends object>({
  data,
  series,
}: {
  data: T[];
  series: Array<{ name: string; dataKey: string; color: string }>;
}) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const inst = echarts.init(el)

    // 当 series 超过 2 条时，使用双 Y 轴展示
    const useDualAxis = series.length > 3
    if (useDualAxis) {
      // 前几条放左轴，后面放右轴
      const leftAxisSeries = series.slice(0, Math.ceil(series.length / 2))
      const rightAxisSeries = series.slice(Math.ceil(series.length / 2))

      inst.setOption({
        tooltip: { trigger: 'axis' },
        legend: { bottom: 4, textStyle: { fontSize: 11 } },
        grid: { left: 55, right: 55, top: 10, bottom: 40 },
        xAxis: {
          type: 'category',
          data: (data as any).map((d: any) => d.date),
          axisTick: { alignWithLabel: true },
          axisLabel: { fontSize: 11 },
        },
        yAxis: [
          { type: 'value', position: 'left', splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } }, name: '主要指标' },
          { type: 'value', position: 'right', splitLine: { show: false }, name: '辅助指标' },
        ],
        series: [
          ...leftAxisSeries.map(s => ({
            name: s.name,
            type: 'line',
            smooth: true,
            lineStyle: { width: 2 },
            yAxisIndex: 0,
            data: (data as any).map((d: any) => d[s.dataKey]),
            itemStyle: { color: s.color },
          })),
          ...rightAxisSeries.map(s => ({
            name: s.name,
            type: 'line',
            smooth: true,
            lineStyle: { width: 2 },
            yAxisIndex: 1,
            data: (data as any).map((d: any) => d[s.dataKey]),
            itemStyle: { color: s.color },
          })),
        ],
      }, true)
    } else {
      inst.setOption({
        tooltip: { trigger: 'axis' },
        legend: { bottom: 4, textStyle: { fontSize: 11 } },
        grid: { left: 50, right: 16, top: 10, bottom: 40 },
        xAxis: {
          type: 'category',
          data: (data as any).map((d: any) => d.date),
          axisTick: { alignWithLabel: true },
          axisLabel: { fontSize: 11 },
        },
        yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } } },
        series: series.map(s => ({
          name: s.name,
          type: 'line',
          smooth: true,
          lineStyle: { width: 2 },
          data: (data as any).map((d: any) => d[s.dataKey]),
          itemStyle: { color: s.color },
        })),
      }, true)
    }
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [data, series])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}

/** 贡献活动热力图（类似 GitHub 贡献格子） */
function ContributorHeatmapChart({ data }: { data: HeatmapDataPoint[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const inst = echarts.init(el)

    // 构建周×日的二维数据
    const weekDays = ['一', '二', '三', '四', '五', '六', '日']
    // 按 ISO 周分组
    const weekMap: Record<string, Record<number, number>> = {}
    for (const d of data) {
      const dt = new Date(d.date)
      // ISO week: Monday as first day
      const isoWeek = getISOWeek(dt)
      const key = `${dt.getFullYear()}-W${String(isoWeek).padStart(2, '0')}`
      if (!weekMap[key]) weekMap[key] = {}
      weekMap[key][d.weekDay] = d.commit_count
    }
    const weeks = Object.keys(weekMap).sort()

    // 构建 heatmap 数据：[weekIndex, weekDay, count]
    const heatmapData: [number, number, number][] = []
    weeks.forEach((wk, wi) => {
      for (let wd = 0; wd < 7; wd++) {
        heatmapData.push([wi, wd, weekMap[wk][wd] || 0])
      }
    })

    inst.setOption({
      tooltip: {
        formatter: (params: any) => {
          const wi = params.data[0]
          const wd = params.data[1]
          const cnt = params.data[2]
          return `${weeks[wi]}<br/>星期${weekDays[wd]}: ${cnt} 次提交`
        },
      },
      grid: { left: 10, right: 30, top: 10, bottom: 30 },
      xAxis: {
        type: 'category',
        data: weeks,
        splitArea: { show: false },
        axisLabel: {
          fontSize: 10,
          formatter: (val: string) => val.split('-W')[1],
          rotate: 0,
        },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      yAxis: {
        type: 'category',
        data: weekDays,
        splitArea: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      visualMap: {
        min: 0,
        max: Math.max(...heatmapData.map(d => d[2]), 1),
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        show: true,
        inRange: {
          color: ['#f0f0f0', '#d9f7be', '#a0d911', '#62b209', '#216500'],
        },
        textStyle: { fontSize: 10 },
      },
      series: [{
        type: 'heatmap',
        data: heatmapData,
        label: { show: false },
        emphasis: {
          itemStyle: { borderColor: '#5b46f5', borderWidth: 2 },
        },
      }],
    }, true)

    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}

/** 提交类型柱状图 */
function CommitTypeBarChart({ data }: { data: TypeDistributionItem[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const inst = echarts.init(el)
    inst.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 50, right: 16, top: 10, bottom: 40 },
      xAxis: {
        type: 'category',
        data: data.map(d => classificationLabels[d.commit_type] || d.commit_type),
        axisTick: { alignWithLabel: true },
        axisLabel: { fontSize: 11, rotate: 30 },
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } } },
      series: [{
        type: 'bar',
        data: data.map((d, i) => ({
          value: d.count,
          itemStyle: { color: chartColors[i % chartColors.length] },
        })),
        barMaxWidth: 50,
        label: {
          show: true,
          position: 'top',
          fontSize: 11,
          formatter: (params: any) => params.value,
        },
      }],
    }, true)
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [data])

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
}

// ISO 周工具函数
function getISOWeek(date: Date): number {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  const dayNum = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum)
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  return Math.ceil((((d.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
}

function ClassificationTab({ selectedRepo }: { selectedRepo?: string }) {
  const [data, setData] = useState<ClassificationSummary | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const result = await getClassificationSummary({ repo_name: selectedRepo || null })
        if (!cancelled) {
          await new Promise(r => setTimeout(r, 400))
          setData(result)
        }
      } catch (error: unknown) {
        if (!cancelled) {
          const err = error as { response?: { data?: { detail?: string } }; message?: string }
          message.error('加载分类统计失败：' + (err.response?.data?.detail || err.message || '未知错误'))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load().then()
    return () => { cancelled = true }
  }, [selectedRepo])

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
                <EChartsPieChart data={pieData} />
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

function BuildsTab({ selectedRepo }: { selectedRepo?: string }) {
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
  const [fileModal, setFileModal] = useState<{ open: boolean; files: string[] | null; prNumber: number }>({ open: false, files: null, prNumber: 0 })
  const [refreshTrigger, setRefreshTrigger] = useState(0)

  // 加载统计数据
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setStatsLoading(true)
      try {
        const [statsData, optionsData] = await Promise.all([
          getBuildOptimizationStats({ repo_name: selectedRepo || null }),
          getBuildFilterOptions({ repo_name: selectedRepo || null }),
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
  }, [selectedRepo])

  // 加载构建计划列表
  const fetchPlans = useCallback(async (page: number, size: number) => {
    setPlansLoading(true)
    try {
      const data = await getBuildPlans({
        repo_name: selectedRepo || null,
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
  }, [selectedRepo, filterCommitType, filterBuildType, filterDateRange, refreshTrigger])

  useEffect(() => {
    fetchPlans(pagination.current, pagination.pageSize).then()
  }, [fetchPlans, pagination.current, pagination.pageSize])

  const handleExportCSV = () => {
    const headers = ['PR 号', 'Commit SHA', 'Commit Type', 'Build Type', '节省比例', '推理说明', '创建时间']
    const rows = plans.map(p => [
      `#${p.pr_number}`, p.commit_sha.slice(0, 8), p.commit_type,
      buildTypeLabels[p.build_type] || p.build_type, `${Math.round(p.estimated_saving_percent)}%`,
      p.reasoning || '-', p.created_at ? formatTimezone(p.created_at, 'YYYY-MM-DD HH:mm') : '-',
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
      width: 400,
      render: (text: string | null, record: BuildPlan) => {
        const files = record.files_changed || []
        const fileCount = files.length

        const fileChangeColors: Record<string, string> = {
          'vllm_ascend/': '#1a7f37',
          'csrc/': '#1a7f37',
          'test': '#6e42a0',
          'docs/': '#57606a',
          '.github/': '#57606a',
        }

        const getFileColor = (f: string) => {
          for (const [pattern, color] of Object.entries(fileChangeColors)) {
            if (f.includes(pattern)) return color
          }
          return '#1a7f37'
        }

        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ flex: 1 }}>
              {text ? (text.length > 50 ? text.slice(0, 50) + '...' : text) : '-'}
            </span>
            {fileCount > 0 && (
              <Tooltip title="点击查看变更文件列表">
                <Tag
                  color="success"
                  style={{ cursor: 'pointer', fontWeight: 600, margin: 0 }}
                  onClick={() => setFileModal({ open: true, files: files, prNumber: record.pr_number })}
                >
                  {fileCount} 文件
                </Tag>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (date: string | null) => formatTimezone(date, 'YYYY-MM-DD HH:mm'),
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
            title={
              <Space>
                <span>构建计划记录</span>
                <Button size="middle" icon={<ReloadOutlined />} loading={plansLoading} onClick={() => setRefreshTrigger(t => t + 1)}>刷新数据</Button>
              </Space>
            }
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

      <Modal
        title={
          <span>
            <GithubOutlined style={{ marginRight: 6, color: '#6e42a0' }} />
            PR #{fileModal.prNumber} 变更文件
          </span>
        }
        open={fileModal.open}
        onCancel={() => setFileModal({ open: false, files: null, prNumber: 0 })}
        footer={null}
        width={720}
      >
        {fileModal.files && fileModal.files.length > 0 && (
          <div style={{ maxHeight: 480, overflow: 'auto' }}>
            <Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
              共 {fileModal.files.length} 个文件
            </Text>
            {fileModal.files.map((f: string) => {
              const parts = f.split('/')
              const fileName = parts[parts.length - 1]
              const dir = parts.length > 1 ? parts.slice(0, -1).join('/') + '/' : ''
              return (
                <div key={f} style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '5px 8px',
                  gap: 8,
                  borderRadius: 4,
                  borderBottom: '1px solid #f0f0f0',
                }}>
                  <span style={{
                    display: 'inline-block',
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: ((): string => {
                      const fileChangeColors: Record<string, string> = { 'vllm_ascend/': '#1a7f37', 'csrc/': '#1a7f37', 'test': '#6e42a0', 'docs/': '#57606a', '.github/': '#57606a' }
                      for (const [pattern, color] of Object.entries(fileChangeColors)) {
                        if (f.includes(pattern)) return color
                      }
                      return '#1a7f37'
                    })(),
                    flexShrink: 0,
                  }} />
                  <span style={{ color: '#656d76', fontSize: 12, flexShrink: 0 }}>{dir}</span>
                  <Text strong style={{ fontSize: 13 }}>{fileName}</Text>
                </div>
              )
            })}
          </div>
        )}
        {(!fileModal.files || fileModal.files.length === 0) && (
          <Empty description="暂无变更文件数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Modal>
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

type ViewMode = 'single' | 'quad'

/* ------------------------------------------------------------------ */
/*  独立 ECharts 散点图组件 — 各自管理实例生命周期                      */
/* ------------------------------------------------------------------ */

function SingleChart({
  data,
  yLabel,
  quadX,
  quadY,
  highlightedLogins,
  height = 750,
  sourceTab = 'quadrant',
}: {
  data: ScatterDataPoint[];
  yLabel: string;
  quadX: number;
  quadY: number;
  highlightedLogins: Set<string>;
  height?: number;
  sourceTab?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const disabledQuadsRef = useRef(new Set<string>())

  const buildOption = useCallback((disabled: Set<string>) => {
    let filtered = data
    if (highlightedLogins.size > 0) {
      filtered = filtered.filter(p => highlightedLogins.has(p.login))
    }
    if (disabled.size > 0 && highlightedLogins.size === 0) {
      filtered = filtered.filter(p => !disabled.has(p.quadrant))
    }
    if (filtered.length === 0) return null

    const fXMax = Math.max(...filtered.map(p => p.x), 1)
    const fYMax = Math.max(...filtered.map(p => p.y), 1)
    const xMax = Math.ceil((fXMax + 1) * 1.15)
    const yMax = Math.max(15, Math.ceil((fYMax + 1) * 1.15))

    const series: any[] = []
    const allQuads = ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE']
    for (const q of allQuads) {
      const qData = filtered.filter(p => p.quadrant === q)
      series.push({
        type: 'scatter',
        name: quadrantLabels[q],
        symbolSize: () => highlightedLogins.size > 0 ? 10 : 8,
        data: qData.length === 0 ? [] : qData.map((p, idx) => {
          const isHighlighted = highlightedLogins.has(p.login)
          return {
            value: [p.x, p.y, p.z, idx],
            login: p.login,
            quadrant: p.quadrant,
            itemStyle: {
              color: quadrantColors[q],
              opacity: highlightedLogins.size === 0 || isHighlighted ? 0.9 : 0.15,
            },
          }
        }),
      })
    }

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const raw = params.data
          if (!raw || !raw.value) return ''
          const [xVal, yVal, zVal] = raw.value
          const login = raw.login
          const quadrant = raw.quadrant
          if (!login) return ''
          const isMatch = highlightedLogins.has(login)
          return `
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
              <img src="https://avatars.githubusercontent.com/${login}" style="width:20px;height:20px;border-radius:50%;" />
              <strong>${login}</strong>
              ${isMatch ? '<span style="background:#faad14;border:1px solid #fa8c16;color:#fff;padding:1px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-left:6px;">匹配</span>' : ''}
            </div>
            <div style="border-top:1px solid #eee;padding-top:6px;">
              贡献分数: <b>${xVal.toFixed(1)}</b><br/>
              ${yLabel}: <b>${yVal.toFixed(1)}</b><br/>
              合并 PR: <b>${zVal}</b><br/>
              象限: <span style="color:${quadrantColors[quadrant]};font-weight:bold">${quadrantLabels[quadrant] || quadrant}</span>
            </div>
          `
        },
      },
      legend: {
        orient: 'horizontal',
        bottom: 0,
        data: allQuads.map(q => ({
          name: quadrantLabels[q],
          itemStyle: { color: quadrantColors[q] },
        })),
        textStyle: { fontSize: 12, color: '#666' },
        itemGap: 24,
        itemWidth: 12,
        itemHeight: 12,
        selected: Object.fromEntries(allQuads.map(q => [quadrantLabels[q], !disabled.has(q)])),
      },
      grid: {
        left: 60,
        right: 16,
        top: 10,
        bottom: 40,
      },
      xAxis: {
        type: 'value',
        nameLocation: 'middle',
        nameGap: 36,
        min: 0,
        max: xMax,
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } },
        scale: true,
      },
      yAxis: {
        type: 'value',
        name: yLabel,
        nameLocation: 'middle',
        nameGap: 44,
        min: 0,
        max: yMax,
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } },
        scale: true,
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseWheel: true },
        { type: 'inside', yAxisIndex: 0, filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseWheel: true },
      ],
      series,
      graphic: [],
    }
  }, [data, yLabel, highlightedLogins])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const inst = echarts.init(el)
    const opt = buildOption(disabledQuadsRef.current)
    if (opt) inst.setOption(opt, true)

    inst.on('click', (params: any) => {
      if (params.data?.login) navigateToDetail(navigate, params.data.login, sourceTab, undefined)
    })

    inst.on('legendselectchanged', (params: any) => {
      const selected = params.selected
      const newDisabled = new Set<string>()
      for (const q of ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE']) {
        if (!selected[quadrantLabels[q]]) {
          newDisabled.add(q)
        }
      }
      disabledQuadsRef.current = newDisabled
      const newOpt = buildOption(newDisabled)
      if (newOpt) inst.setOption(newOpt, true)
    })

    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [buildOption, navigate])

  return <div ref={containerRef} style={{ height, width: '100%' }} />
}

function QuadSubChart({
  option,
  title,
  color,
  count,
  height = 350,
}: {
  option: any;
  title: string;
  color: string;
  count: number;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const inst = echarts.init(el)
    if (option) inst.setOption(option, true)

    inst.on('click', (params: any) => {
      if (params.data?.login) navigateToDetail(navigate, params.data.login, title.toLowerCase(), undefined)
    })

    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      inst.dispose()
    }
  }, [option, navigate])

  return (
    <Card
      size="small"
      title={
        <span>
          <span
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: '50%',
              backgroundColor: color,
              marginRight: 6,
            }}
          />
          {title} ({count})
        </span>
      }
      style={{ width: '100%' }}
      bodyStyle={{ padding: '4px 8px 8px' }}
    >
      <div ref={containerRef} style={{ height, width: '100%' }} />
    </Card>
  )
}

function buildScatterOption(
  data: ScatterDataPoint[],
  yLabel: string,
  quadX: number,
  quadY: number,
  highlightedLogins: Set<string>,
  quadFilter?: string,
  isSearchMode?: boolean,
  showXAxisName?: boolean,
): any {
  let filtered = quadFilter ? data.filter(p => p.quadrant === quadFilter) : data

  if (isSearchMode && filtered.length > 0) {
    filtered = filtered.filter(p => highlightedLogins.has(p.login))
  }

  if (filtered.length === 0) {
    return null
  }

  const fXMax = Math.max(...filtered.map(p => p.x), 1)
  const fYMax = Math.max(...filtered.map(p => p.y), 1)

  const xMax = Math.ceil((fXMax + 1) * 1.15)
  const yMax = Math.max(15, Math.ceil((fYMax + 1) * 1.15))

  const series: any[] = []
  const quadsToShow = quadFilter ? [quadFilter] : ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE']

  for (const q of quadsToShow) {
    const qData = filtered.filter(p => p.quadrant === q)
    if (qData.length === 0 && !quadFilter) continue

    series.push({
      type: 'scatter',
      name: quadrantLabels[q],
      symbolSize: () => isSearchMode && qData.length <= 20 ? 12 : (highlightedLogins.size > 0 ? 10 : 8),
      data: qData.map((p, idx) => {
        const isHighlighted = highlightedLogins.has(p.login)
        return {
          value: [p.x, p.y, p.z, idx],
          login: p.login,
          quadrant: p.quadrant,
          itemStyle: {
            color: quadrantColors[q],
            opacity: highlightedLogins.size === 0 || isHighlighted ? 0.9 : 0.15,
          },
        }
      }),
    })
  }

  return {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const raw = params.data
        if (!raw || !raw.value) return ''
        const [xVal, yVal, zVal] = raw.value
        const login = raw.login
        const quadrant = raw.quadrant
        if (!login) return ''
        const isMatch = highlightedLogins.has(login)
        return `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
            <img src="https://avatars.githubusercontent.com/${login}" style="width:20px;height:20px;border-radius:50%;" />
            <strong>${login}</strong>
            ${isMatch ? '<span style="background:#faad14;border:1px solid #fa8c16;color:#fff;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:bold;">匹配</span>' : ''}
          </div>
          <div style="border-top:1px solid #eee;padding-top:6px;">
            贡献分数: <b>${xVal.toFixed(1)}</b><br/>
            ${yLabel}: <b>${yVal.toFixed(1)}</b><br/>
            合并 PR: <b>${zVal}</b><br/>
            象限: <span style="color:${quadrantColors[quadrant]};font-weight:bold">${quadrantLabels[quadrant] || quadrant}</span>
          </div>
        `
      },
    },
    legend: quadFilter ? undefined : {
      orient: 'horizontal',
      bottom: 0,
      data: ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE'].map(q => ({
        name: quadrantLabels[q],
        itemStyle: { color: quadrantColors[q] },
      })),
      textStyle: { fontSize: 12, color: '#666' },
      itemGap: 24,
      itemWidth: 12,
      itemHeight: 12,
    },
    grid: {
      left: 60,
      right: quadFilter ? 12 : 16,
      top: quadFilter ? 8 : 10,
      bottom: quadFilter ? 16 : 40,
    },
    xAxis: {
      type: 'value',
      name: showXAxisName ? '贡献分数' : undefined,
      nameLocation: 'middle',
      nameGap: 36,
      min: 0,
      max: xMax,
      splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } },
      scale: true,
    },
    yAxis: {
      type: 'value',
      name: yLabel,
      nameLocation: 'middle',
      nameGap: 44,
      min: 0,
      max: yMax,
      splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } },
      scale: true,
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0, filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseWheel: true },
      { type: 'inside', yAxisIndex: 0, filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseWheel: true },
    ],
    series,
    graphic: [],
  }
}

function QuadrantTab({ refreshKey, selectedRepo }: { refreshKey?: number; selectedRepo?: string }) {
  const [searchLogin, setSearchLogin] = useState('')
  const [quadrantData, setQuadrantData] = useState<QuadrantData | null>(null)
  const [quadrantLoading, setQuadrantLoading] = useState(true)
  const [yAxisType, setYAxisType] = useState('review_count')
  const [viewMode, setViewMode] = useState<ViewMode>('single')

  useEffect(() => {
    let cancelled = false
    const loadSettings = async () => {
      try {
        const data = await getContributorBoardSettings()
        if (!cancelled) setYAxisType(data.quadrant_config.y_axis_type)
      } catch { /* ignore */ }
    }
    loadSettings()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    const reloadAxis = async () => {
      try {
        const data = await getContributorBoardSettings()
        if (!cancelled) setYAxisType(data.quadrant_config.y_axis_type)
      } catch { /* ignore */ }
    }
    if (refreshKey && refreshKey > 0) reloadAxis()
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

  const scatterData: ScatterDataPoint[] = useMemo(() => {
    const deduped = new Map<string, ScatterDataPoint>()
    ;(quadrantData?.points ?? []).forEach(p => {
      const pt: ScatterDataPoint = {
        x: p.contribution_score, y: p.y_value, z: p.merged_pr_count, login: p.login, quadrant: p.quadrant,
      }
      const existing = deduped.get(p.login)
      if (!existing || pt.z > existing.z || (pt.z === existing.z && pt.x > existing.x)) {
        deduped.set(p.login, pt)
      }
    })
    return Array.from(deduped.values())
  }, [quadrantData])

  const isHighlightedPoint = useCallback((login: string) => {
    if (!searchLogin.trim()) return false
    return login.toLowerCase().includes(searchLogin.trim().toLowerCase())
  }, [searchLogin])

  const highlightedLogins = useMemo(() => {
    const s = new Set<string>()
    scatterData.forEach(p => { if (isHighlightedPoint(p.login)) s.add(p.login) })
    return s
  }, [scatterData, isHighlightedPoint])

  const yLabel = axisLabels[yAxisType] || 'Y轴评分'
  const quadX = quadrantData?.quad_x ?? 0
  const quadY = quadrantData?.quad_y ?? 0

  const quadOrder = ['INCENTIVE', 'ENCOURAGE', 'IMPROVE', 'REPLACE']

  const quadOptions = useMemo(() => {
    return quadOrder.map(q =>
      buildScatterOption(scatterData, yLabel, quadX, quadY, highlightedLogins, q),
    )
  }, [scatterData, yLabel, quadX, quadY, highlightedLogins])

  const handleLocate = () => {
    if (!searchLogin.trim()) return
    const found = scatterData.filter(p => p.login.toLowerCase().includes(searchLogin.trim().toLowerCase()))
    if (found.length > 0) {
      message.success(`找到 ${found.length} 位贡献者: ${found.map(p => p.login).join(', ')} (点击散点查看详情)`)
    } else {
      message.warning(`未找到匹配的贡献者: ${searchLogin}`)
    }
  }

  const quadCounts = quadrantData?.quadrant_counts ?? {}

  return (
    <Spin spinning={quadrantLoading}>
      <Row gutter={[16, 16]}>
        {quadOrder.map(q => (
          <Col span={6} key={q}>
            <Card size="small">
              <Statistic title={quadrantLabels[q]} value={quadCounts[q] ?? 0} valueStyle={{ color: quadrantColors[q] }} />
            </Card>
          </Col>
        ))}

        <Col span={24}>
          <Card
            title="四象限散点图"
            extra={
              <Space size="small" wrap>
                <Radio.Group size="small" value={viewMode} onChange={e => setViewMode(e.target.value)}>
                  <Radio.Button value="single">单图</Radio.Button>
                  <Radio.Button value="quad">四分图</Radio.Button>
                </Radio.Group>
                <Select
                  size="small"
                  value={yAxisType}
                  onChange={v => setYAxisType(v)}
                  options={axisOptions}
                  style={{ width: 110 }}
                />
                <Input
                  placeholder="输入用户名模糊搜索"
                  value={searchLogin}
                  onChange={e => setSearchLogin(e.target.value)}
                  allowClear
                  size="small"
                  style={{ width: 200 }}
                  prefix={<SearchOutlined />}
                  onPressEnter={handleLocate}
                />
              </Space>
            }
            bodyStyle={{ padding: '4px 8px 8px' }}
          >
            {scatterData.length > 0 ? (
              viewMode === 'single' ? (
                <SingleChart key="single" data={scatterData} yLabel={yLabel} quadX={quadX} quadY={quadY} highlightedLogins={highlightedLogins} height={700} />
              ) : (
                <Row gutter={[12, 12]}>
                  {quadOrder.map((q, i) => (
                    <Col span={12} key={q}>
                      <QuadSubChart
                        key={`${q}-${i}`}
                        option={quadOptions[i]}
                        title={quadrantLabels[q]}
                        color={quadrantColors[q]}
                        count={quadCounts[q] ?? 0}
                      />
                    </Col>
                  ))}
                </Row>
              )
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300 }}>
                <Text type="secondary">暂无数据 — 先执行同步采集</Text>
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
   const [quadrantFilter, setQuadrantFilter] = useState<string>('')
  const [roleStats, setRoleStats] = useState<RoleStats | null>(null)
  const [moduleHotspot, setModuleHotspot] = useState<ModuleHotspotResponse | null>(null)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [rankingYAxis, setRankingYAxis] = useState('error_score')
  const [settingsLoaded, setSettingsLoaded] = useState(false)

  const [searchText, setSearchText] = useState('')
  const [sortModalOpen, setSortModalOpen] = useState(false)
  const [sortRules, setSortRules] = useState<SortRule[]>([{ field: 'score', order: 'desc' }])

  const [form] = Form.useForm()

  const fetchRanking = useCallback(async (page: number, size: number) => {
    setRankingLoading(true)
    try {
      // 当没有自定义排序时，按当前 Y 轴类型默认排序
      const axisSortMap: Record<string, string> = {
        error_score: 'error_score',
        review_count: 'review_count',
        merged_pr_count: 'merged_pr_count',
        code_additions: 'code_additions',
      }
      const defaultSortField = axisSortMap[rankingYAxis] || rankingYAxis
      const defaultSortOrder = rankingYAxis === 'error_score' ? 'asc' : 'desc'
      const sortKey = sortRules.length > 0 ? sortRules[0].field : defaultSortField
      const sortDir = sortRules.length > 0 ? sortRules[0].order : defaultSortOrder
      const data = await getRanking(roleType, size, {
        repo_name: selectedRepo || null,
        sort_by: sortKey,
        sort_order: sortDir,
        offset: (page - 1) * size,
        quadrant: quadrantFilter || undefined,
        y_axis_type: rankingYAxis,
      })
      // 最小 loading 延迟 300ms，避免接口返回太快导致闪烁
      await new Promise(r => setTimeout(r, 300))
      setRankings(data.rankings)
      setRankingTotal(data.total)
    } finally {
      setRankingLoading(false)
    }
    }, [roleType, selectedRepo, sortRules, quadrantFilter, rankingYAxis])

  // 首次加载：settings 完成后只发起一次 fetchRanking
  useEffect(() => {
    if (!settingsLoaded) return
    fetchRanking(pagination.current, pagination.pageSize)
  }, [settingsLoaded, fetchRanking, pagination.current, pagination.pageSize])

  useEffect(() => {
    let cancelled = false
    const loadStats = async () => {
      try {
        const [_roleStats, _moduleHotspot, _settings] = await Promise.all([
          getRoleStats({ repo_name: selectedRepo || undefined }),
          getModuleHotspot({
            repo_name: selectedRepo || null,
          }),
          getContributorBoardSettings().catch(() => null),
        ])
        if (!cancelled) {
          setRoleStats(_roleStats)
          setModuleHotspot(_moduleHotspot)
          if (_settings?.quadrant_config) {
            setRankingYAxis(_settings.quadrant_config.y_axis_type || 'error_score')
          }
          // settings 加载完成后再触发 fetchRanking（统一触发，避免双 loading）
          setSettingsLoaded(true)
        }
      } catch { /* ignore */ }
    }
    loadStats()
    return () => { cancelled = true }
  }, [selectedRepo])

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
          <a onClick={() => navigateToDetail(navigate, login, 'ranking', selectedRepo)} style={{ cursor: 'pointer' }}>
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
            <Divider type="vertical" />
            <span style={{ fontSize: 13, color: '#8c8c8c' }}>象限筛选:</span>
            <Button type={!quadrantFilter ? 'primary' : 'default'} onClick={() => { setQuadrantFilter(''); setPagination(p => ({ ...p, current: 1 })) }}>全部</Button>
            {['INCENTIVE', 'IMPROVE', 'ENCOURAGE', 'REPLACE'].map(q => (
              <Button key={q} type={quadrantFilter === q ? 'primary' : 'default'} onClick={() => { setQuadrantFilter(q); setPagination(p => ({ ...p, current: 1 })) }} style={{ borderColor: quadrantColors[q], color: quadrantFilter === q ? '#fff' : quadrantColors[q] }}>
                {quadrantLabels[q]}
              </Button>
            ))}
            <Divider type="vertical" />
            <span style={{ fontSize: 13, color: '#8c8c8c' }}>四象限 Y 轴:</span>
            <Select
              value={rankingYAxis}
              onChange={(v) => { setRankingYAxis(v); setPagination(p => ({ ...p, current: 1 })) }}
              style={{ width: 140 }}
              options={Object.entries(axisLabels).map(([key, label]) => ({ value: key, label }))}
            />
            <Divider type="vertical" />
            <Button icon={<ReloadOutlined />} onClick={() => { fetchRanking(pagination.current, pagination.pageSize) }}>刷新数据</Button>
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

function CronModal({ open, cron, onChange, onClose }: {
  open: boolean
  cron: string
  onChange: (cron: string) => void
  onClose: () => void
}) {
  const [mode, setMode] = useState<'preset' | 'custom' | 'weekly'>('preset')
  const [customHour, setCustomHour] = useState(2)
  const [customMinute, setCustomMinute] = useState(0)
  const [weekDays, setWeekDays] = useState<number[]>([0, 1, 2, 3, 4])
  const [selectedPreset, setSelectedPreset] = useState('0 2 * * *')

  useEffect(() => {
    const parts = cron.split(' ')
    if (parts.length === 5) {
      setCustomMinute(parseInt(parts[0]) || 0)
      setCustomHour(parseInt(parts[1]) || 0)
      setSelectedPreset(cron)
    }
    if (cron.startsWith('0 2 * * *')) {
      setMode('preset')
    } else if (parts.length === 5 && parts[4] !== '*') {
      setMode('weekly')
      const days = parts[4].split(',').map(Number).filter(n => !isNaN(n))
      if (days.length) setWeekDays(days)
    } else {
      setMode('custom')
    }
  }, [cron])

  const cronToText = (expr: string) => {
    const parts = expr.split(' ')
    if (parts.length !== 5) return expr
    const [min, hour, day, _month, dow] = parts
    if (dow !== '*') {
      const dayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      const days = dow.split(',').map(n => parseInt(n)).map(n => dayNames[n] || n).join('、')
      return `每周${days} ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
    }
    if (day !== '*') return `每月${day}日 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
    return `每天 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
  }

  const presetGroups = [
    {
      title: '每天',
      items: [
        { label: '02:00', value: '0 2 * * *' },
        { label: '03:00', value: '0 3 * * *' },
        { label: '06:00', value: '0 6 * * *' },
        { label: '12:00', value: '0 12 * * *' },
      ],
    },
    {
      title: '每周',
      items: [
        { label: '周日', value: '0 2 * * 0' },
        { label: '周一', value: '0 2 * * 1' },
        { label: '周五', value: '0 2 * * 5' },
        { label: '周六', value: '0 2 * * 6' },
      ],
    },
    {
      title: '每月',
      items: [
        { label: '1号', value: '0 2 1 * *' },
        { label: '15号', value: '0 2 15 * *' },
      ],
    },
  ]

  const weekDayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

  const toggleWeekDay = (day: number) => {
    setWeekDays(prev =>
      prev.includes(day) ? prev.filter(d => d !== day).sort() : [...prev, day].sort()
    )
  }

  const handleSave = () => {
    if (mode === 'weekly' && weekDays.length === 0) {
      message.warning('请至少选择一个星期几')
      return
    }
    let newCron = cron
    if (mode === 'preset') {
      newCron = selectedPreset
    } else if (mode === 'weekly') {
      newCron = `${customMinute} ${customHour} * * ${weekDays.join(',')}`
    } else if (mode === 'custom') {
      newCron = `${customMinute} ${customHour} * * *`
    }
    onChange(newCron)
    message.success(`已设置为 ${cronToText(newCron)}`)
    onClose()
  }

  const presetCardStyle = (val: string) => ({
    padding: '8px 14px',
    borderRadius: 8,
    border: `1.5px solid ${val === selectedPreset ? '#5b46f5' : '#f0f0f0'}`,
    background: val === selectedPreset ? '#f5f3ff' : '#fafafa',
    color: val === selectedPreset ? '#5b46f5' : '#666',
    fontWeight: val === selectedPreset ? 600 : 400,
    cursor: 'pointer',
    textAlign: 'center' as const,
    fontSize: 13,
    transition: 'all 0.15s',
    userSelect: 'none' as const,
  })

  return (
    <Modal
      title="设置全量同步时间"
      open={open}
      onCancel={onClose}
      destroyOnClose={false}
      maskClosable
      width={480}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="save" type="primary" onClick={handleSave}>确定</Button>,
      ]}
    >
      <Alert
        message={cronToText(cron)}
        description={<Text code style={{ fontSize: 11 }}>{cron}</Text>}
        type="info"
        showIcon
        style={{ marginBottom: 16, fontSize: 13 }}
      />

      <Tabs
        defaultActiveKey="preset"
        onChange={key => setMode(key as typeof mode)}
        items={[
          {
            key: 'preset',
            label: '快速选择',
            children: (
              <div>
                {presetGroups.map(group => (
                  <div key={group.title} style={{ marginBottom: 14 }}>
                    <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{group.title}</Text>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {group.items.map(p => (
                        <div
                          key={p.value}
                          style={presetCardStyle(p.value)}
                          onClick={() => setSelectedPreset(p.value)}
                        >
                          {p.label}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ),
          },
          {
            key: 'custom',
            label: '自定义',
            children: (
              <div>
                <Text type="secondary" style={{ display: 'block', marginBottom: 16, fontSize: 13 }}>
                  每天在固定时间执行全量同步
                </Text>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Select
                    value={customHour}
                    onChange={v => setCustomHour(v)}
                    style={{ width: 100 }}
                    options={Array.from({ length: 24 }, (_, i) => ({ label: `${i.toString().padStart(2, '0')}`, value: i }))}
                  />
                  <Text style={{ fontSize: 20, fontWeight: 600 }}>:</Text>
                  <Select
                    value={customMinute}
                    onChange={v => setCustomMinute(v)}
                    style={{ width: 100 }}
                    options={[{ label: '00', value: 0 }, { label: '15', value: 15 }, { label: '30', value: 30 }, { label: '45', value: 45 }]}
                  />
                </div>
                <div style={{ textAlign: 'center', marginTop: 12 }}>
                  <Tag color="blue">{cronToText(`${customMinute} ${customHour} * * *`)}</Tag>
                </div>
              </div>
            ),
          },
          {
            key: 'weekly',
            label: '每周指定日',
            children: (
              <div>
                <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
                  选择星期几，设定执行时间
                </Text>
                <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 16 }}>
                  {weekDayLabels.map((label, idx) => (
                    <div
                      key={idx}
                      onClick={() => toggleWeekDay(idx)}
                      style={{
                        width: 32, height: 32, borderRadius: '50%',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 12, cursor: 'pointer', userSelect: 'none',
                        background: weekDays.includes(idx) ? '#5b46f5' : '#f5f5f5',
                        color: weekDays.includes(idx) ? '#fff' : '#999',
                        fontWeight: weekDays.includes(idx) ? 600 : 400,
                        transition: 'all 0.15s',
                      }}
                    >
                      {label.slice(1)}
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Select
                    value={customHour}
                    onChange={v => setCustomHour(v)}
                    style={{ width: 100 }}
                    options={Array.from({ length: 24 }, (_, i) => ({ label: `${i.toString().padStart(2, '0')}`, value: i }))}
                  />
                  <Text style={{ fontSize: 20, fontWeight: 600 }}>:</Text>
                  <Select
                    value={customMinute}
                    onChange={v => setCustomMinute(v)}
                    style={{ width: 100 }}
                    options={[{ label: '00', value: 0 }, { label: '15', value: 15 }, { label: '30', value: 30 }, { label: '45', value: 45 }]}
                  />
                </div>
                <div style={{ textAlign: 'center', marginTop: 12 }}>
                  <Tag color="blue">
                    {weekDays.length
                      ? `每周${weekDays.map(d => weekDayLabels[d]).join('、')} ${customHour}:${customMinute.toString().padStart(2, '0')}`
                      : '请选择星期几'}
                  </Tag>
                </div>
              </div>
            ),
          },
        ]}
      />
    </Modal>
  )
}

function SettingsTab() {
  const [form] = Form.useForm()
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [recalculating, setRecalculating] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const settingsData = await getContributorBoardSettings()
        if (!cancelled) {
          setSettings(settingsData)
          form.setFieldValue('y_axis_type', settingsData.quadrant_config.y_axis_type)
          form.setFieldValue('merged_pr_weight', settingsData.scoring_weights.merged_pr_weight)
          form.setFieldValue('code_line_weight', settingsData.scoring_weights.code_line_weight)
          form.setFieldValue('review_weight', settingsData.scoring_weights.review_weight)
          form.setFieldValue('approval_weight', settingsData.scoring_weights.approval_weight)
          form.setFieldValue('core_module_weight', settingsData.scoring_weights.core_module_weight)
          form.setFieldValue('files_changed_weight', settingsData.scoring_weights.files_changed_weight)
          form.setFieldValue('bugfix_weight', settingsData.scoring_weights.bugfix_weight)
          form.setFieldValue('feature_weight', settingsData.scoring_weights.feature_weight)
          form.setFieldValue('active_duration_weight', settingsData.scoring_weights.active_duration_weight)
          form.setFieldValue('star_threshold', settingsData.role_thresholds.star_threshold)
          form.setFieldValue('active_threshold', settingsData.role_thresholds.active_threshold)
          form.setFieldValue('max_error_score', settingsData.role_thresholds.max_error_score)
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
          files_changed_weight: values.files_changed_weight,
          bugfix_weight: values.bugfix_weight,
          feature_weight: values.feature_weight,
          active_duration_weight: values.active_duration_weight,
        }),
        updateRoleThresholds({
          star_threshold: values.star_threshold,
          active_threshold: values.active_threshold,
          max_error_score: values.max_error_score,
        }),
      ])
      message.success('设置已保存')
      window.dispatchEvent(new CustomEvent('settings-updated'))
      const data = await getContributorBoardSettings()
      setSettings(data)
    } catch (err) {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleRecalculate = async () => {
    try {
      setRecalculating(true)
      const result = await recalculateScores()
      message.success(`重算完成，共更新 ${result?.recalculated_count ?? 0} 位贡献者的分数`)
      window.dispatchEvent(new CustomEvent('settings-updated'))
    } catch {
      message.error('重算失败')
    } finally {
      setRecalculating(false)
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
            以下配置将同时影响「四象限看板」和「排行榜中的象限标识」。修改保存后，点击"重算分数"使新权重生效。
          </Text>
        </div>
        <Space>
          <Button type="primary" loading={saving} onClick={handleSave}>
            保存设置
          </Button>
          <Button danger loading={recalculating} onClick={handleRecalculate}>
            重算分数
          </Button>
        </Space>
      </div>

      {/* 卡片布局：第一排 Y轴+基础权重+阈值，第二排 新增维度 */}
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
              <Form.Item label="核心模块权重" name="core_module_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={15} step={1}
                  marks={{ 0: '0', 8: '8', 15: '15' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 卡片 2.5: 新增权重维度 */}
        <Col xs={24} md={8}>
          <Card title={<><ExperimentOutlined style={{ marginRight: 6, color: '#722ed1' }} />新增评分维度</>}
            size="small" style={cardStyle} bodyStyle={{ padding: '20px 24px 24px' }}>
            <Form form={form}>
              <Form.Item label="文件变更数权重" name="files_changed_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={2} step={0.1}
                  marks={{ 0: '0', 1: '1', 2: '2' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(1) }} />
              </Form.Item>
              <Form.Item label="BugFix 贡献权重" name="bugfix_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={15} step={1}
                  marks={{ 0: '0', 8: '8', 15: '15' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
              <Form.Item label="Feature 贡献权重" name="feature_weight" style={sliderFormItemStyle}>
                <Slider min={0} max={10} step={1}
                  marks={{ 0: '0', 5: '5', 10: '10' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(0) }} />
              </Form.Item>
              <Form.Item label="活跃持续时间权重" name="active_duration_weight" style={{ marginBottom: 0 }}>
                <Slider min={0} max={0.05} step={0.005}
                  marks={{ 0: '0', 0.025: '0.025', 0.05: '0.05' }}
                  tooltip={{ formatter: (v) => (v ?? 0).toFixed(3) }} />
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
/*                     分析设置 — 权重版本管理                        */
/* ------------------------------------------------------------------ */

function ContributorBoardSettingsPage() {
  const [activeSubTab, setActiveSubTab] = useState('current')
  const [activeVersion, setActiveVersion] = useState<ScoringVersion | null>(null)
  const [versions, setVersions] = useState<ScoringVersion[]>([])
  const [versionsTotal, setVersionsTotal] = useState(0)
  const [snapshots, setSnapshots] = useState<ScoreSnapshot[]>([])
  const [snapshotsTotal, setSnapshotsTotal] = useState(0)
  const [audits, setAudits] = useState<ScoreUpdateAudit[]>([])
  const [auditsTotal, setAuditsTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [calculating, setCalculating] = useState(false)
  const [aiCalculating, setAiCalculating] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [applying, setApplying] = useState<number | null>(null)
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [selectedForCompare, setSelectedForCompare] = useState<number | null>(null)
  const [comparingVersion, setComparingVersion] = useState<number | null>(null)
  const [compareResult, setCompareResult] = useState<VersionCompareResult | null>(null)
  const [selectedSnapshot, setSelectedSnapshot] = useState<ScoreSnapshot | null>(null)
  const [aiReporting, setAiReporting] = useState(false)
  const [aiReport, setAiReport] = useState<any>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [aiReports, setAiReports] = useState<AIReportListItem[]>([])
  const [selectedReport, setSelectedReport] = useState<AIReportDetailType | null>(null)
  const [reportsPage, setReportsPage] = useState(1)
  const [manualWeights, setManualWeights] = useState<Record<string, number>>({})
  const [applyMode, setApplyMode] = useState<'draft' | 'apply' | null>(null)
  const [savingWeights, setSavingWeights] = useState(false)
  const [llmProviders, setLlmProviders] = useState<LLMProvider[]>([])
  const [modelSelectOpen, setModelSelectOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<'ai-weights' | 'ai-report' | null>(null)
  const [selectedModel, setSelectedModel] = useState<string | null>(null)

  useEffect(() => {
    loadAll().then()
    loadLlmProviders().then()
  }, [])

  useEffect(() => {
    window.addEventListener('settings-updated', loadAll)
    return () => window.removeEventListener('settings-updated', loadAll)
  }, [])

  const loadAll = async () => {
    try {
      const [av, vs, ss, au, st, reports] = await Promise.all([
        getActiveScoringVersion().catch(() => null),
        listScoringVersions().catch(() => ({ versions: [], total: 0 })),
        listScoreSnapshots().catch(() => ({ snapshots: [], total: 0 })),
        listScoreAuditLogs().catch(() => ({ audits: [], total: 0 })),
        getContributorBoardSettings().catch(() => null),
        listAIReports({ skip: 0, limit: 50 }).catch(() => ({ reports: [], total: 0 })),
      ])
      setActiveVersion(av)
      setVersions(vs.versions)
      setVersionsTotal(vs.total)
      setSnapshots(ss.snapshots)
      setSnapshotsTotal(ss.total)
      setAudits(au.audits)
      setAuditsTotal(au.total)
      setSettings(st)
      setAiReports(reports.reports)
      if (st) {
        setManualWeights({ ...st.scoring_weights } as Record<string, number>)
      }
    } finally {
      setLoading(false)
    }
  }

  const loadLlmProviders = async () => {
    try {
      const providers = await getLLMProviders()
      setLlmProviders(providers)
    } catch {
      setLlmProviders([])
    }
  }

  const handleAutoWeights = async () => {
    try {
      setCalculating(true)
      const result = await calculateAutoWeights() as AutoWeightsResult
      // 自动创建版本记录
      await createScoringVersion({
        method: 'auto',
        weights: result.weights as Record<string, number>,
        data_summary: result.data_summary,
        confidence_score: result.confidence,
        description: `自动算权 (置信度: ${result.confidence.toFixed(2)})`,
        apply_immediately: false,
      })
      message.success(`自动算权完成 (置信度: ${result.confidence.toFixed(2)})，已创建新版本（草稿）`)
      await loadAll()
    } catch {
      message.error('自动算权失败')
    } finally {
      setCalculating(false)
    }
  }

  const handleAIWeightsClick = () => {
    setPendingAction('ai-weights')
    setSelectedModel(null)
    setModelSelectOpen(true)
  }

  const handleAIWeights = async (model: string | null) => {
    try {
      setAiCalculating(true)
      const result = await aiAnalyzeWeights({ model }) as AutoWeightsResult
      const aiModel = result.data_summary?.ai_model || '默认'
      const aiAnalysis = result.data_summary?.ai_analysis || ''
      if (aiAnalysis) {
        Modal.info({
          title: 'AI 评权分析结果',
          content: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, maxHeight: 400, overflow: 'auto' }}>{aiAnalysis}</pre>,
          width: 600,
        })
      }
      // 自动创建版本记录
      await createScoringVersion({
        method: 'ai',
        weights: result.weights as Record<string, number>,
        data_summary: result.data_summary,
        confidence_score: result.confidence,
        ai_prompt: result.data_summary?.ai_prompt as any,
        ai_response: aiAnalysis as any,
        ai_model: aiModel as any,
        description: `AI 评权 (模型: ${aiModel}, 置信度: ${result.confidence.toFixed(2)})`,
        apply_immediately: false,
      })
      message.success(`AI 评权完成 (置信度: ${result.confidence.toFixed(2)}, 模型: ${aiModel})，已创建新版本（草稿）`)
      await loadAll()
    } catch (e: any) {
      const msg = e.response?.data?.msg || 'AI 评权失败，请检查 LLM 配置'
      message.error(msg)
    } finally {
       setAiCalculating(false)
    }
  }

  const handleGenerateAIReportClick = () => {
    setPendingAction('ai-report')
    setSelectedModel(null)
    setModelSelectOpen(true)
  }

  const handleGenerateAIReport = async (model: string | null) => {
    try {
      setAiReporting(true)
      setAiReport(null)
      const report = await generateAIReport({ report_type: 'adhoc', model }) as AIReportListItem
      message.success(`AI 综合分析报告生成成功 (报告: ${report.report_name})`)
      await loadAll()
    } catch (e: any) {
      const msg = e.response?.data?.detail || 'AI 报告生成失败，请检查 LLM 配置'
      message.error(msg)
    } finally {
      setAiReporting(false)
    }
  }

  const handleLoadReportDetail = async (reportId: number) => {
    try {
      setReportLoading(true)
      const detail = await getAIReportDetail(reportId) as AIReportDetailType
      setSelectedReport(detail)
    } catch {
      message.error('加载报告详情失败')
    } finally {
      setReportLoading(false)
    }
  }

  const handleExportByVersion = async (versionId: number, format: 'pdf') => {
    try {
      setExporting(true)
      const blob = await exportPdfByVersion(versionId)
      const filename = `contributor_report_${new Date().toISOString().slice(0, 10)}.pdf`
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      message.success('PDF 报告导出成功')
    } catch {
      message.error('导出失败')
    } finally {
      setExporting(false)
    }
  }

  const handleApply = async (versionId: number) => {
    try {
      setApplying(versionId)
      await applyScoringVersion(versionId)
      message.success('版本应用成功，已重新计算所有分数')
      await loadAll()
    } catch {
      message.error('应用失败')
    } finally {
      setApplying(null)
    }
  }

  const handleRollback = async (versionId: number) => {
    try {
      setApplying(versionId)
      await rollbackScoringVersion(versionId)
      message.success('回滚成功，已重新计算所有分数')
      await loadAll()
    } catch {
      message.error('回滚失败')
    } finally {
      setApplying(null)
    }
  }

  const handleRecalculate = async () => {
    try {
      await recalculateScores()
      message.success('分数重算完成')
      await loadAll()
    } catch {
      message.error('重算失败')
    }
  }

  const handleCompare = async () => {
    if (!selectedForCompare || !comparingVersion || selectedForCompare === comparingVersion) return
    try {
      const result = await compareScoringVersions(selectedForCompare, comparingVersion)
      setCompareResult(result)
    } catch {
      message.error('对比失败')
    }
  }

  const handleConfirmSaveWeights = async () => {
    try {
      setSavingWeights(true)
      const applyImmediately = applyMode === 'apply'
      const normalized = normalizeWeights(manualWeights)
      await createScoringVersion({
        method: 'manual',
        weights: normalized as Record<string, number>,
        description: '手动调整权重（归一化）',
        apply_immediately: applyImmediately,
      })
      if (applyImmediately) {
        message.success('权重修改已创建新版本并立即应用')
      } else {
        message.success('权重修改已保存为新版本（草稿），可在版本历史中应用生效')
      }
      setApplyMode(null)
      await loadAll()
    } catch {
      message.error('保存失败')
    } finally {
      setSavingWeights(false)
    }
  }

  const handleSaveWeights = () => {
    setApplyMode('draft')
  }

  if (loading) return <Spin size="large" />

  return (
    <Spin tip="加载中" spinning={calculating}>
      <Title level={4} style={{ marginBottom: 16 }}>
        <SettingOutlined style={{ marginRight: 8, color: '#5b46f5' }} />
        贡献者分析设置
      </Title>

      <Tabs activeKey={activeSubTab} onChange={setActiveSubTab}>
        {/* Tab 1: 当前权重 */}
        <Tabs.TabPane tab="当前权重" key="current">
          <Row gutter={[20, 16]}>
            <Col xs={24} md={12}>
              <Card title="生效版本信息" size="small">
                {activeVersion ? (
                  <Descriptions column={1} size="small" bordered>
                    <Descriptions.Item label="版本">{activeVersion.version}</Descriptions.Item>
                    <Descriptions.Item label="确定方式">
                       <Tag color={METHOD_CONFIG[activeVersion.method]?.color || 'default'}>
                         {METHOD_CONFIG[activeVersion.method]?.emoji || ''} {METHOD_CONFIG[activeVersion.method]?.label || activeVersion.method}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="置信度">
                      {activeVersion.confidence_score ? (
                        <Progress percent={Math.round(activeVersion.confidence_score * 100)} size="small" />
                      ) : <Text type="secondary">N/A</Text>}
                    </Descriptions.Item>
                    <Descriptions.Item label="操作人">{activeVersion.activated_by || 'N/A'}</Descriptions.Item>
                    <Descriptions.Item label="生效时间">{formatTimezone(activeVersion.activated_at)}</Descriptions.Item>
                    <Descriptions.Item label="说明">{activeVersion.description || '-'}</Descriptions.Item>
                  </Descriptions>
                ) : (
                  <Empty description="暂无生效版本" />
                )}
              </Card>
             </Col>
              <Col xs={24} md={12}>
               <Card title="权重配置" size="small">
                 {activeVersion ? (() => {
                    const entries = Object.entries(activeVersion.weights)
                    const maxVal = Math.max(...entries.map(([, v]) => Math.abs(v as number)))
                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {entries.map(([key, value]) => (
                          <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Text style={{ width: 72, flexShrink: 0, fontSize: 12 }}>{WEIGHT_LABELS[key as keyof typeof WEIGHT_LABELS] || key}</Text>
                            <div style={{ flex: 1, background: '#f0f0f0', borderRadius: 4, height: 16, overflow: 'hidden' }}>
                              <div style={{ width: `${maxVal > 0 ? (Math.abs(value as number) / maxVal) * 100 : 0}%`, background: `linear-gradient(90deg, #722ed1, #b745d3)`, height: '100%', borderRadius: 4, minWidth: 2 }} />
                            </div>
                            <Text strong style={{ width: 56, textAlign: 'right', flexShrink: 0, fontSize: 12 }}>
                              {typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : value}
                            </Text>
                          </div>
                        ))}
                      </div>
                    )
                  })() : <Empty />}
               </Card>
             </Col>
           </Row>

          <Row gutter={16} style={{ marginTop: 16 }}>
            <Col span={12}>
              <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
                <Space>
                  <Button type="primary" onClick={handleAutoWeights} loading={calculating}>
                    自动算权
                  </Button>
                  <Button onClick={handleAIWeightsClick} loading={aiCalculating} style={{ background: 'linear-gradient(135deg, #722ed1 0%, #b745d3 100%)', border: 'none', color: '#fff' }}>
                    AI 评权
                  </Button>
                  <Button onClick={handleRecalculate} danger ghost>
                    仅重算分数
                  </Button>
                  <Button onClick={handleGenerateAIReportClick} loading={aiReporting} type="primary" style={{ background: 'linear-gradient(135deg, #722ed1 0%, #b745d3 100%)', border: 'none' }}>
                    📑 AI 综合报告
                  </Button>
                </Space>
                <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                  自动算权基于熵权法，AI 评权调用大模型分析数据分布，AI 综合报告生成多周期决策分析
                </div>
              </Card>
            </Col>
            <Col span={12}>
              <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
                <Space>
                  <Text strong>当前版本</Text>
                  <Tag color="green">{activeVersion?.version || 'N/A'}</Tag>
                  {activeVersion && <Tag color={METHOD_CONFIG[activeVersion.method]?.color}>{METHOD_CONFIG[activeVersion.method]?.emoji} {METHOD_CONFIG[activeVersion.method]?.label}</Tag>}
                </Space>
                <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                  {activeVersion?.activated_at ? `生效于 ${activeVersion.activated_at.slice(0, 10)}` : '尚未生效'}
                </div>
              </Card>
            </Col>
          </Row>

            {settings && (
             <Card title="手动调整权重" extra={<Text type="secondary" style={{ fontSize: 12 }}>* 量纲：倍数量纲，保存后系统自动归一化</Text>} size="small" style={{ marginTop: 16 }}>
               <Row gutter={[16, 16]}>
                 {WEIGHT_DIMENSIONS.map(dw => (
                   <Col span={12} key={dw.key}>
                     <div style={{ marginBottom: 4 }}>
                       <Text style={{ fontWeight: 500 }}>{dw.label}权重</Text>
                       <Text strong style={{ marginLeft: 8, fontSize: 14 }}>
                         {manualWeights[dw.key]?.toFixed(2) ?? settings.scoring_weights[dw.key]}
                       </Text>
                     </div>
                     <Slider value={manualWeights[dw.key] ?? settings.scoring_weights[dw.key]} min={dw.min} max={dw.max} step={dw.step}
                       onChange={v => setManualWeights(prev => ({ ...prev, [dw.key]: v }))}
                       tooltip={{ formatter: (val) => `${val}` }} />
                   </Col>
                 ))}
               </Row>
               <div style={{ marginTop: 12, textAlign: 'center' }}>
                 <Button type="primary" onClick={handleSaveWeights}>
                   保存修改 — 创建新版本
                 </Button>
                 <Text style={{ marginLeft: 16, color: '#999', fontSize: 12 }}>
                   保存后将创建新的手动权重版本（草稿），可在版本历史中应用生效
                 </Text>
               </div>
             </Card>
            )}

          <Modal
            title="确认保存权重"
            open={applyMode !== null}
            onCancel={() => setApplyMode(null)}
            footer={[
              <Button key="draft" onClick={() => { setApplyMode('draft'); handleConfirmSaveWeights(); }}>仅创建（草稿）</Button>,
              <Button key="apply" type="primary" onClick={() => { setApplyMode('apply'); handleConfirmSaveWeights(); }} loading={savingWeights}>
                立即应用
              </Button>,
            ]}>
            <div style={{ marginBottom: 12 }}>
              <Radio.Group value={applyMode} onChange={e => setApplyMode(e.target.value)}>
                <Radio.Button value="draft">仅创建 — 新版本保存为草稿，不立即生效</Radio.Button>
                <Radio.Button value="apply">立即应用 — 新版本立即生效，自动重算所有分数</Radio.Button>
              </Radio.Group>
            </div>
            {applyMode === 'draft' ? (
              <Alert message="仅创建" description="新版本将保存为草稿状态，不会立即生效。您可以在「版本历史」Tab 中对比和应用。" type="info" showIcon />
            ) : (
              <Alert message="立即应用" description="新版本将立即成为生效版本，当前版本将被归档。系统将自动重算所有贡献者的分数并生成快照。" type="warning" showIcon />
            )}
          </Modal>

          {/* 模型选择弹窗 */}
          <Modal
            title={pendingAction === 'ai-weights' ? '选择 AI 模型 - AI 评权' : '选择 AI 模型 - 生成报告'}
            open={modelSelectOpen}
            onCancel={() => setModelSelectOpen(false)}
            footer={[
              <Button key="cancel" onClick={() => setModelSelectOpen(false)}>取消</Button>,
              <Button
                key="confirm"
                type="primary"
                loading={pendingAction === 'ai-weights' ? aiCalculating : aiReporting}
                disabled={!selectedModel && llmProviders.length > 0}
                onClick={() => {
                  setModelSelectOpen(false)
                  if (pendingAction === 'ai-weights') {
                    handleAIWeights(selectedModel)
                  } else if (pendingAction === 'ai-report') {
                    handleGenerateAIReport(selectedModel)
                  }
                }}
              >
                确认执行
              </Button>,
            ]}
          >
            <div style={{ marginBottom: 16 }}>
              <Text type="secondary">选择一个可用的大模型执行操作。未选择时将使用系统默认激活的模型。</Text>
            </div>
            {llmProviders.length === 0 ? (
              <Alert
                message="无可用的 LLM 提供商"
                description="请先在「系统配置 → LLM 配置」中添加并启用至少一个 LLM 提供商。"
                type="warning"
                showIcon
              />
            ) : (
              <Select
                style={{ width: '100%' }}
                placeholder="选择模型（留空使用默认激活模型）"
                value={selectedModel || undefined}
                onChange={v => setSelectedModel(v || null)}
                allowClear
              >
                {llmProviders.filter(p => p.enabled && p.api_key_configured).map(p => (
                  <Select.Option key={p.provider} value={p.default_model}>
                    <Space>
                      <Tag color={p.is_active ? 'gold' : 'blue'}>
                        {p.display_name}
                      </Tag>
                      <Text type="secondary">{p.default_model}</Text>
                      {p.is_active && <Tag color="gold">默认</Tag>}
                    </Space>
                  </Select.Option>
                ))}
              </Select>
            )}
            {llmProviders.filter(p => p.enabled && p.api_key_configured).length === 0 && llmProviders.length > 0 && (
              <Alert
                message="没有已启用且配置 API Key 的模型"
                description="所有已配置的模型都未启用或未配置 API Key，请先在「系统配置 → LLM 配置」中完善配置。"
                type="error"
                showIcon
                style={{ marginTop: 12 }}
              />
            )}
          </Modal>
        </Tabs.TabPane>

        {/* Tab 2: 版本历史 */}
        <Tabs.TabPane tab="版本历史" key="versions">
          <div style={{ marginBottom: 12 }}>
            <Select value={selectedForCompare} placeholder="选择基准版本" allowClear style={{ width: 180, marginRight: 8 }}
              onChange={v => setSelectedForCompare(v)}>
              {versions.map(v => <Select.Option key={v.id} value={v.id}>{v.version} ({v.method})</Select.Option>)}
            </Select>
            <Select value={comparingVersion} placeholder="选择对比版本" allowClear style={{ width: 180, marginRight: 8 }}
              onChange={v => setComparingVersion(v)}>
              {versions.map(v => <Select.Option key={v.id} value={v.id}>{v.version} ({v.method})</Select.Option>)}
            </Select>
            <Button onClick={handleCompare}>对比</Button>
          </div>

          {compareResult && (
            <Card title={`对比: ${compareResult.version_a.version} vs ${compareResult.version_b.version}`} size="small" style={{ marginBottom: 16 }}
              extra={<Button size="small" onClick={() => setCompareResult(null)}>关闭</Button>}>
              <Descriptions column={4} size="small" bordered>
                  {Object.entries(compareResult.weight_diff).map(([key, diff]) => (
                    <Descriptions.Item key={key} label={WEIGHT_LABELS[key as keyof typeof WEIGHT_LABELS] || key}>
                      {diff.from} → {diff.to} ({diff.change_pct > 0 ? '+' : ''}{diff.change_pct.toFixed(1)}%)
                    </Descriptions.Item>
                  ))}
              </Descriptions>
              {compareResult.score_impact?.quadrant_changes && compareResult.score_impact.quadrant_changes.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <Text strong>象限变动 ({compareResult.score_impact.quadrant_changes.length} 人):</Text>
                  <div style={{ marginTop: 4 }}>
                    {compareResult.score_impact.quadrant_changes.map((c: any, i: number) => (
                      <Tag key={i}>{c.login}: {c.from} → {c.to}</Tag>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}

          <Timeline
            items={versions.map(v => ({
              color: v.status === 'active' ? 'green' : 'gray',
              children: (
                <div>
                  <Space>
                    <Text strong>{v.version}</Text>
                    <Tag color={v.status === 'active' ? 'green' : v.status === 'draft' ? 'orange' : 'default'}>{v.status}</Tag>
                     <Tag color={METHOD_CONFIG[v.method]?.color || 'default'}>
                       {METHOD_CONFIG[v.method]?.emoji || ''} {METHOD_CONFIG[v.method]?.label || v.method}
                    </Tag>
                    {v.confidence_score != null && <Text type="secondary">置信度: {v.confidence_score.toFixed(2)}</Text>}
                  </Space>
                  <div style={{ marginTop: 4, marginBottom: 4 }}>
                    <Text type="secondary">{v.created_by}</Text>
                    <Text type="secondary" style={{ marginLeft: 8 }}>{formatTimezone(v.created_at)}</Text>
                    {v.description && <Text style={{ marginLeft: 8 }}>{v.description}</Text>}
                  </div>
                  <Space size="small" style={{ marginBottom: 8 }}>
                    {v.status !== 'active' && (
                      <Button size="small" onClick={() => handleRollback(v.id)} loading={applying === v.id}>
                        {v.status === 'draft' ? '应用' : '回滚'}
                      </Button>
                    )}
                     <Button size="small" type="link" onClick={() => handleExportByVersion(v.id, 'pdf')}>导出 PDF</Button>
                    <Button size="small" type="link" onClick={() => generateScoreSnapshot(v.id).then(r => { if (r) { message.success('快照生成成功'); loadAll(); } })}>生成快照</Button>
                  </Space>
                  {v.ai_response && (
                    <div style={{ marginTop: 8, padding: 8, background: '#f5f5f5', borderRadius: 4 }}>
                      <Text strong>AI 分析:</Text>
                      <pre style={{ margin: '8px 0', fontSize: 12, whiteSpace: 'pre-wrap' }}>{v.ai_response}</pre>
                    </div>
                  )}
                </div>
              ),
            }))}
          />
        </Tabs.TabPane>

        {/* Tab 3: 分数快照 */}
        <Tabs.TabPane tab="分数快照" key="snapshots">
          <div style={{ marginBottom: 12 }}>
            <Space>
              <Button size="small" onClick={() => activeVersion && handleExportByVersion(activeVersion.id, 'pdf')} loading={exporting}>
                导出 PDF (当前版本)
              </Button>
            </Space>
          </div>
          <Table
            dataSource={snapshots}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: true }}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 60 },
              { title: '日期', width: 180, render: (r: ScoreSnapshot) => formatTimezone(r.created_at) || (r.snapshot_date || '-') },
              { title: '贡献者数', dataIndex: 'contributor_count', width: 100 },
              { title: '均值', render: (r: ScoreSnapshot) => r.statistics?.score_mean?.toFixed?.(1) || '-', width: 80 },
              { title: '操作人', dataIndex: 'created_by', width: 100 },
              {
                title: '操作',
                width: 80,
                render: (r: ScoreSnapshot) => (
                  <Button size="small" onClick={() => setSelectedSnapshot(r)}>查看</Button>
                ),
              },
            ]}
          />
          {selectedSnapshot && (
            <Card title={`快照 ${selectedSnapshot.snapshot_date}`} size="small" style={{ marginTop: 16 }}
              extra={<Button size="small" onClick={() => setSelectedSnapshot(null)}>关闭</Button>}>
              <Descriptions column={3} size="small" bordered>
                <Descriptions.Item label="贡献者数">{selectedSnapshot.contributor_count}</Descriptions.Item>
                <Descriptions.Item label="分数均值">
                  {selectedSnapshot.statistics?.score_mean?.toFixed(1)}
                </Descriptions.Item>
                <Descriptions.Item label="四象限分布">
                  {selectedSnapshot.statistics?.quadrant_distribution &&
                    Object.entries(selectedSnapshot.statistics.quadrant_distribution as Record<string, number>).map(([k, v]) => (
                      <Tag key={k} color={quadrantColors[k] || 'default'}>
                        {quadrantLabels[k] || k}: {v}
                      </Tag>
                    ))
                  }
                </Descriptions.Item>
              </Descriptions>
              <Divider />
              <Table
                dataSource={selectedSnapshot.scores}
                rowKey="login"
                size="small"
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: '用户', dataIndex: 'login', width: 180 },
                  { title: '分数', dataIndex: 'score', width: 100, sorter: (a: any, b: any) => a.score - b.score, render: (v: number) => Number(v).toFixed(2) },
                  { title: '错误分', dataIndex: 'error_score', width: 100 },
                  { title: '象限', dataIndex: 'quadrant', width: 100, render: (q: string) => <Tag color={quadrantColors[q]}>{quadrantLabels[q] || q}</Tag> },
                ]}
              />
            </Card>
          )}
        </Tabs.TabPane>

        {/* Tab 4: 审计日志 */}
        <Tabs.TabPane tab="审计日志" key="audit">
          <Table
            dataSource={audits}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: true }}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 60 },
              { title: '操作', dataIndex: 'action', width: 180, render: (a: string) => {
                  const map: Record<string, {color: string, label: string}> = {
                    'create_version': {color: 'blue', label: '创建版本'},
                    'apply_version': {color: 'green', label: '应用版本'},
                    'rollback_version': {color: 'orange', label: '回滚版本'},
                    'generate_snapshot': {color: 'purple', label: '生成快照'},
                  }
                  const info = map[a] || {color: 'default', label: a}
                  return <Tag color={info.color}>{info.label}</Tag>
                }},
              { title: '操作人', dataIndex: 'created_by', width: 100 },
              { title: '详情', dataIndex: 'summary', ellipsis: true },
              {
                title: '最大变动',
                dataIndex: 'max_weight_shift',
                width: 100,
                render: (v: number | null) => v != null
                  ? <Tag color={v > 30 ? 'red' : v > 15 ? 'orange' : 'green'}>{v.toFixed(1)}%</Tag>
                  : '-',
              },
              { title: '时间', dataIndex: 'created_at', render: (v: string) => formatTimezone(v), width: 180 },
            ]}
          />
        </Tabs.TabPane>

        {/* Tab 5: AI 综合报告 */}
        <Tabs.TabPane tab="AI 综合报告" key="ai-report">
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <Text strong>生成 AI 综合分析报告</Text>
                <div style={{ fontSize: 12, color: '#999' }}>
                  基于多周期四象限数据，调用大模型生成团队健康度分析和决策建议
                </div>
              </div>
              <Button type="primary" onClick={handleGenerateAIReportClick} loading={aiReporting}
                style={{ background: 'linear-gradient(135deg, #722ed1 0%, #b745d3 100%)', border: 'none' }}>
                生成新报告
              </Button>
            </div>
          </Card>

          <Spin spinning={reportLoading}>
            {selectedReport ? (
              <div>
                <Button size="small" onClick={() => setSelectedReport(null)} style={{ marginBottom: 12 }}>
                  ← 返回列表
                </Button>

                <Descriptions bordered column={3} size="small" style={{ marginBottom: 16 }}>
                  <Descriptions.Item label="报告名称">
                    <Text strong>{selectedReport.report_name}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="报告类型">
                    <Tag>{selectedReport.report_type || 'adhoc'}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="模型">
                    <Tag color="purple">{selectedReport.llm_model || 'N/A'}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="置信度">
                    {selectedReport.confidence_overall != null ? (
                      <Progress percent={Math.round(selectedReport.confidence_overall * 100)} size="small" />
                    ) : <Text type="secondary">N/A</Text>}
                  </Descriptions.Item>
                  <Descriptions.Item label="操作人">
                    {selectedReport.generated_by || 'N/A'}
                  </Descriptions.Item>
                  <Descriptions.Item label="生成时间">
                    {formatTimezone(selectedReport.generated_at) || 'N/A'}
                  </Descriptions.Item>
                </Descriptions>

                {selectedReport.executive_summary && (
                  <Alert
                    message="Executive Summary"
                    description={selectedReport.executive_summary}
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                  />
                )}

                {selectedReport.report_content && (
                  <Card title="完整报告内容" size="small" style={{ marginBottom: 16 }}>
                    <Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>{selectedReport.report_content}</Paragraph>
                  </Card>
                )}

                {selectedReport.quadrant_analysis && (
                  <Card title="四象限分析" size="small" style={{ marginBottom: 16 }}>
                    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>
                      {JSON.stringify(selectedReport.quadrant_analysis, null, 2)}
                    </pre>
                  </Card>
                )}

                {selectedReport.recommendations && selectedReport.recommendations.length > 0 && (
                  <Card title="决策建议" size="small">
                    {selectedReport.recommendations.map((rec: Record<string, any>, i: number) => (
                      <div key={i} style={{ padding: '8px 12px', background: '#f5f0ff', borderRadius: 6, marginBottom: 8, display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                        <div style={{
                          width: 24, height: 24, borderRadius: '50%',
                          background: 'linear-gradient(135deg, #722ed1, #b745d3)',
                          color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontSize: 12, fontWeight: 'bold', flexShrink: 0
                        }}>{i + 1}</div>
                        <Text style={{ fontSize: 13 }}>
                          {rec.description || rec.title || JSON.stringify(rec)}
                        </Text>
                      </div>
                    ))}
                  </Card>
                )}

                {selectedReport.individual_evaluations && selectedReport.individual_evaluations.length > 0 && (
                  <Card title="个人评价" size="small" style={{ marginTop: 16 }}>
                    <Table
                      dataSource={selectedReport.individual_evaluations}
                      rowKey="login"
                      size="small"
                      pagination={{ pageSize: 5 }}
                      columns={[
                        { title: '用户', dataIndex: 'login' },
                        { title: '评分', dataIndex: 'score' },
                        { title: '评价', dataIndex: 'evaluation', ellipsis: true },
                      ]}
                    />
                  </Card>
                )}
              </div>
            ) : (
              <Table
                dataSource={aiReports}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: '报告名称', dataIndex: 'report_name', width: 200 },
                  { title: '类型', dataIndex: 'report_type', width: 100 },
                  {
                    title: '置信度',
                    dataIndex: 'confidence_overall',
                    width: 120,
                    render: (v: number | null) => v != null
                      ? <Progress percent={Math.round(v * 100)} size="small" />
                      : '-',
                  },
                  { title: '模型', dataIndex: 'llm_model', width: 120 },
                  { title: '操作人', dataIndex: 'generated_by', width: 100 },
                  { title: '生成时间', render: (_: unknown, r: AIReportListItem) => formatTimezone(r.generated_at), width: 180 },
                  {
                    title: '操作',
                    render: (_: unknown, r: AIReportListItem) => (
                      <Space>
                        <Button size="small" type="link" onClick={() => handleLoadReportDetail(r.id)}>查看</Button>
                      </Space>
                    ),
                    width: 120,
                  },
                ]}
              />
            )}
          </Spin>
        </Tabs.TabPane>
      </Tabs>
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                          贡献者详情页                              */
/* ------------------------------------------------------------------ */

function ContributorDetailPage() {
  const navigate = useNavigate()
  const { login } = useParams<{ login: string }>()
  const [referrerTab, setReferrerTab] = useState<string | null>(null)
  const [referrerRepo, setReferrerRepo] = useState<string | null>(null)
  const [detail, setDetail] = useState<ContributorDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [policy, setPolicy] = useState<PolicyResult | null>(null)
  const [trend, setTrend] = useState<ExtendedTrend | null>(null)
  const [heatmap, setHeatmap] = useState<HeatmapType | null>(null)
  const [distribution, setDistribution] = useState<DistributionType | null>(null)
  const [timeline, setTimeline] = useState<TimelineType | null>(null)
  const [heatmapLoading, setHeatmapLoading] = useState(false)
  const [distLoading, setDistLoading] = useState(false)
  const [timelineLoading, setTimelineLoading] = useState(false)

  useEffect(() => {
    try {
      setReferrerTab(sessionStorage.getItem('cb_last_tab'))
      setReferrerRepo(sessionStorage.getItem('cb_last_repo'))
    } catch { /* ignore */ }
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

  const loadHeatmap = async () => {
    if (!login) return
    setHeatmapLoading(true)
    try {
      setHeatmap(await getContributorHeatmap(login))
    } catch { /* ignore */ } finally {
      setHeatmapLoading(false)
    }
  }

  const loadDistribution = async () => {
    if (!login) return
    setDistLoading(true)
    try {
      setDistribution(await getContributorDistribution(login))
    } catch { /* ignore */ } finally {
      setDistLoading(false)
    }
  }

  const loadTimeline = async () => {
    if (!login) return
    setTimelineLoading(true)
    try {
      setTimeline(await getContributorTimeline(login))
    } catch { /* ignore */ } finally {
      setTimelineLoading(false)
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
          <Button onClick={() => {
            const params = new URLSearchParams()
            if (referrerTab && referrerTab !== 'classification') params.set('tab', referrerTab)
            if (referrerRepo) params.set('repo', referrerRepo)
            navigate(`/contributor-board${params.toString() ? '?' + params.toString() : ''}`)
          }} style={{ marginBottom: 8 }}>← 返回看板</Button>
        </Col>
      </Row>

      <Card title={<Space><Avatar src={detail.avatar_url} size="large" icon={<GithubOutlined />} /> {detail.login}</Space>}>
        <Descriptions bordered column={2}>
          <Descriptions.Item label="姓名">{detail.name || '-'}</Descriptions.Item>
          <Descriptions.Item label="公司">{detail.company || '-'}</Descriptions.Item>
          <Descriptions.Item label="首次贡献">{formatTimezone(detail.first_contribution_at, 'YYYY-MM-DD')}</Descriptions.Item>
          <Descriptions.Item label="最近贡献">{formatTimezone(detail.last_contribution_at, 'YYYY-MM-DD')}</Descriptions.Item>
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
        <>
          {/* ── 自动化策略面板 ── */}
          <Card
            style={{
              marginTop: 16,
              borderRadius: 12,
              borderTop: '3px solid',
              borderImage: 'linear-gradient(90deg, #722ed1, #b745d3) 1',
              overflow: 'hidden',
            }}
            title={
              <Space>
                <RocketOutlined style={{ color: '#722ed1', fontSize: 18 }} />
                <span style={{ fontWeight: 600, fontSize: 15 }}>自动化策略中心</span>
                <Tag
                  color={
                    policy.has_critical ? '#ff4d4f' : policy.has_warning ? '#faad14' : '#722ed1'
                  }
                  style={{ fontWeight: 500 }}
                >
                  {policy.has_critical ? '需立即关注' : policy.has_warning ? '有待改进' : '运行良好'}
                </Tag>
              </Space>
            }
            extra={
              <Space size="small">
                <Tag color="blue" style={{ border: 'none' }}>
                  自动 {policy.auto_count}
                </Tag>
                <Tag color="purple" style={{ border: 'none' }}>
                  建议 {policy.suggestion_count}
                </Tag>
                {policy.alert_count > 0 && (
                  <Tag color="red" style={{ border: 'none' }}>
                    告警 {policy.alert_count}
                  </Tag>
                )}
              </Space>
            }
          >
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              {/* 告警区域 */}
              {policy.policies.filter(p => p.execution === 'alert').length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#ff4d4f', marginBottom: 6 }}>
                    <CloseCircleOutlined /> 需关注 ({policy.alert_count})
                  </div>
                  <Row gutter={[8, 8]}>
                    {policy.policies.filter(p => p.execution === 'alert').map((p, i) => (
                      <Col span={12} key={`alert-${i}`}>
                        <div style={{
                          padding: '8px 12px', borderRadius: 6,
                          background: '#fff2f0', border: '1px solid #ffccc7',
                        }}>
                          <Space size={4} style={{ fontSize: 12 }}>
                            <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                            <span style={{ fontWeight: 500, flex: 1 }}>{p.description}</span>
                            <Tag color="error" style={{ fontSize: 10, lineHeight: '16px' }}>{p.impact_level}</Tag>
                          </Space>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4, paddingLeft: 18 }}>{p.reason}</div>
                        </div>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}

              {/* 自动执行区域 */}
              {policy.policies.filter(p => p.execution === 'auto').length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#52c41a', marginBottom: 6 }}>
                    <CheckCircleOutlined /> 已自动执行 ({policy.auto_count})
                  </div>
                  <Row gutter={[8, 8]}>
                    {policy.policies.filter(p => p.execution === 'auto').map((p, i) => (
                      <Col span={12} key={`auto-${i}`}>
                        <div style={{
                          padding: '8px 12px', borderRadius: 6,
                          background: '#f6ffed', border: '1px solid #d9f7be',
                        }}>
                          <Space size={4} style={{ fontSize: 12 }}>
                            <CheckCircleOutlined style={{ color: '#52c41a' }} />
                            <span style={{ fontWeight: 500, flex: 1 }}>{p.description}</span>
                            <Tag color="success" style={{ fontSize: 10, lineHeight: '16px' }}>自动</Tag>
                          </Space>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4, paddingLeft: 18 }}>{p.reason}</div>
                        </div>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}

              {/* 建议执行区域 */}
              {policy.policies.filter(p => p.execution === 'suggestion').length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#722ed1', marginBottom: 6 }}>
                    <FlagOutlined /> 推荐建议 ({policy.suggestion_count})
                  </div>
                  <Row gutter={[8, 8]}>
                    {policy.policies.filter(p => p.execution === 'suggestion').map((p, i) => (
                      <Col span={12} key={`sug-${i}`}>
                        <div style={{
                          padding: '8px 12px', borderRadius: 6,
                          background: '#faf5ff', border: '1px solid #d3adf7',
                        }}>
                          <Space size={4} style={{ fontSize: 12 }}>
                            <StarOutlined style={{ color: '#722ed1' }} />
                            <span style={{ fontWeight: 500, flex: 1 }}>{p.description}</span>
                            <Tag color={
                              p.impact_level === '高' ? 'error' : p.impact_level === '中' ? 'warning' : 'default'
                            } style={{ fontSize: 10, lineHeight: '16px' }}>{p.impact_level}</Tag>
                            <Button size="small" type="primary" style={{
                              height: 20, padding: '0 6px', fontSize: 11,
                              background: '#722ed1', borderColor: '#722ed1',
                            }}>采纳</Button>
                          </Space>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4, paddingLeft: 18 }}>{p.reason}</div>
                        </div>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}

              {/* 兜底 info */}
              {policy.policies.filter(p => !['alert', 'auto', 'suggestion'].includes(p.execution)).length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#1890ff', marginBottom: 6 }}>
                    <CheckCircleOutlined /> 其他
                  </div>
                  <Row gutter={[8, 8]}>
                    {policy.policies.filter(p => !['alert', 'auto', 'suggestion'].includes(p.execution)).map((p, i) => (
                      <Col span={12} key={`other-${i}`}>
                        <div style={{
                          padding: '8px 12px', borderRadius: 6,
                          background: '#f0f5ff', border: '1px solid #b6e0ff',
                        }}>
                          <Space size={4} style={{ fontSize: 12 }}>
                            <CheckCircleOutlined style={{ color: '#1890ff' }} />
                            <span style={{ fontWeight: 500, flex: 1 }}>{p.description}</span>
                            <Tag color="blue" style={{ fontSize: 10, lineHeight: '16px' }}>信息</Tag>
                          </Space>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4, paddingLeft: 18 }}>{p.reason}</div>
                        </div>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}
            </Space>
          </Card>
        </>
      )}

      <Divider />

      <Row gutter={[16, 16]}>
        <Col span={12}>
          <Card title="提交量趋势">
            <div style={{ height: 350 }}>
              {trend?.commit_trend && trend.commit_trend.length > 0 ? (
                <EChartsBarChart data={trend.commit_trend} />
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="Review 趋势">
            <div style={{ height: 350 }}>
              {trend?.review_trend && trend.review_trend.length > 0 ? (
                <EChartsLineChart
                  data={trend.review_trend}
                  series={[
                    { name: 'Review 数', dataKey: 'review_count', color: '#1890ff' },
                    { name: 'Approval 数', dataKey: 'approval_count', color: '#722ed1' },
                  ]}
                />
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>

        <Col span={24}>
          <Card title="多维分数趋势">
            <div style={{ height: 350 }}>
              {trend?.score_trend && trend.score_trend.length > 0 ? (
                <EChartsLineChart
                  data={trend.score_trend}
                  series={[
                    { name: '贡献分数', dataKey: 'contribution_score', color: '#52c41a' },
                    { name: '出错分数', dataKey: 'error_score', color: '#ff4d4f' },
                    { name: '检视次数', dataKey: 'review_count', color: '#1890ff' },
                    { name: 'PR合并数', dataKey: 'merged_pr_count', color: '#722ed1' },
                    { name: '代码行数', dataKey: 'code_additions', color: '#fa8c16' },
                  ]}
                />
              ) : (
                <Empty description="暂无趋势数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>
      </Row>

      {/* ===== 贡献活动热力图 ===== */}
      <Card
        title="贡献活动热力图"
        extra={<Button size="small" onClick={loadHeatmap} loading={heatmapLoading}>加载热力图</Button>}
        style={{ marginTop: 16 }}
      >
        <div style={{ height: 260 }}>
          {heatmap?.heatmap_data && heatmap.heatmap_data.length > 0 ? (
            <ContributorHeatmapChart data={heatmap.heatmap_data} />
          ) : (
            <Empty description="暂无热力图数据，请点击右上角加载" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </div>
      </Card>

      {/* ===== 模块分布 + 提交类型分布 ===== */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card
            title="模块贡献分布"
            extra={<Button size="small" onClick={loadDistribution} loading={distLoading}>加载分布</Button>}
          >
            <div style={{ height: 350 }}>
              {distribution?.modules && distribution.modules.length > 0 ? (
                <EChartsPieChart
                  data={distribution.modules.map(m => ({
                    name: m.module,
                    value: m.commit_count,
                  }))}
                />
              ) : (
                <Empty description="暂无模块分布数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="提交类型分布">
            <div style={{ height: 350 }}>
              {distribution?.type_distribution && distribution.type_distribution.length > 0 ? (
                <CommitTypeBarChart data={distribution.type_distribution} />
              ) : (
                <Empty description="暂无类型分布数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </Card>
        </Col>
      </Row>

      {/* ===== 贡献时间线 ===== */}
      <Card
        title="最近提交活动 (Timeline)"
        extra={<Button size="small" onClick={loadTimeline} loading={timelineLoading}>加载时间线</Button>}
        style={{ marginTop: 16 }}
      >
        {timeline?.commits && timeline.commits.length > 0 ? (
          <Timeline
            mode="left"
            items={timeline.commits.map(c => ({
              color: classificationColors[c.classification] || 'gray',
              children: (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <Tag color={classificationColors[c.classification] || 'default'}>
                      {classificationLabels[c.classification] || c.classification}
                    </Tag>
                    {c.module && <Tag>{c.module}</Tag>}
                    <Text code style={{ fontSize: 12 }}>{c.commit_hash.slice(0, 8)}</Text>
                  </div>
                  <Text strong>{c.message}</Text>
                  <div style={{ marginTop: 4 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {c.committed_at
                        ? `${formatTimezone(c.committed_at, 'YYYY-MM-DD HH:mm')} · `
                        : ''}
                      {c.files_changed > 0 && `${c.files_changed} files · `}
                      +{c.code_additions}/-{c.code_deletions}
                    </Text>
                  </div>
                </div>
              ),
            }))}
          />
        ) : (
          <Empty description="暂无时间线数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*                       手动同步 子组件                              */
/* ------------------------------------------------------------------ */

const { RangePicker } = DatePicker

function ManualSyncPanel({ selectedRepo }: { selectedRepo?: string }) {
  const [syncMode, setSyncMode] = useState<'full' | 'incremental' | 'range'>('incremental')
  const [singleDate, setSingleDate] = useState<dayjs.Dayjs | null>(null)
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null)
  const [targetRepo, setTargetRepo] = useState<string | undefined>(selectedRepo)
  const [syncLoading, setSyncLoading] = useState(false)
  const [currentRecord, setCurrentRecord] = useState<SyncRecord | null>(null)
  const [syncProgress, setSyncProgress] = useState(0)
  const [syncProgressMsg, setSyncProgressMsg] = useState('')
  const [repos, setRepos] = useState<string[]>([])
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    setTargetRepo(selectedRepo)
  }, [selectedRepo])

  useEffect(() => {
    const init = async () => {
      try {
        const data = await getReposList()
        setRepos(data.repos ?? [])
      } catch { /* ignore */ }

      // 检查是否有正在运行的同步任务，刷新后恢复进度条
      try {
        const history = await getSyncHistory({ limit: 1, offset: 0 })
        const running = history.records?.find((r: SyncRecord) => r.status === 'running' || r.status === 'pending')
        if (running) {
          setCurrentRecord(running)
          setSyncProgress(running.progress_percent)
          setSyncProgressMsg(running.progress_message || '')
          setSyncLoading(true)
          startPollSync(running.id)
        }
      } catch { /* ignore */ }
    }
    init()
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [])

  const startPollSync = (recordId: number) => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    pollTimerRef.current = setInterval(async () => {
      try {
        const status = await getSyncStatus(recordId)
        setCurrentRecord(status)
        setSyncProgress(status.progress_percent)
        setSyncProgressMsg(status.progress_message || '')
        if (['completed', 'failed', 'cancelled'].includes(status.status)) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
          setSyncLoading(false)
          message.success(status.status === 'completed' ? '同步完成' : '同步失败')
          setCurrentRecord(null)
          setSyncProgress(0)
          setSyncProgressMsg('')
          window.dispatchEvent(new Event('settings-updated'))
          window.dispatchEvent(new Event('sync-history-refresh'))
        }
      } catch { /* polling error */ }
    }, 2000)
  }

  const handleSync = async () => {
    if (syncLoading) return
    if (syncMode === 'incremental' && !singleDate) { message.warning('请选择起始日期'); return }
    if (syncMode === 'range' && (!dateRange || !dateRange[0] || !dateRange[1])) { message.warning('请选择完整的时间范围'); return }

    setSyncLoading(true)
    try {
      const result = await syncContributors({
        fullResync: syncMode === 'full',
        repoName: targetRepo || undefined,
        sinceDate: syncMode === 'range' ? dateRange?.[0]?.format('YYYY-MM-DD') : singleDate?.format('YYYY-MM-DD'),
        untilDate: syncMode === 'range' ? dateRange?.[1]?.format('YYYY-MM-DD') : undefined,
      })
      if (result.record_id) {
        startPollSync(result.record_id)
        message.success('同步已触发，后台执行中...')
      } else { message.warning('同步触发失败'); setSyncLoading(false) }
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err ? (err as { detail: string }).detail : '同步触发失败'
      message.error(msg)
      setSyncLoading(false)
    }
  }

  const [cancelling, setCancelling] = useState(false)

  const isRunning = currentRecord?.status === 'running' || currentRecord?.status === 'pending'

  const handleCancel = async () => {
    if (!currentRecord || cancelling) return
    setCancelling(true)
    try {
      await cancelSync(currentRecord.id)
      message.success('同步任务已取消')
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
      setSyncLoading(false)
      setCurrentRecord(null)
      setSyncProgress(0)
      setSyncProgressMsg('')
    } catch {
      message.error('取消失败')
    } finally {
      setCancelling(false)
    }
  }

  const cardStyle = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
    border: '1px solid #f0f0f0',
  }

  const itemCards = [
    {
      title: <><CloudUploadOutlined style={{ marginRight: 6, color: '#1890ff' }} />同步模式</>,
      children: (
        <Radio.Group
          value={syncMode}
          onChange={(e) => { if (!isRunning) setSyncMode(e.target.value) }}
          disabled={isRunning}
          optionType="button"
          buttonStyle="solid"
          size="middle"
        >
          <Radio.Button value="full">全量</Radio.Button>
          <Radio.Button value="incremental">增量</Radio.Button>
          <Radio.Button value="range">范围</Radio.Button>
        </Radio.Group>
      ),
    },
    {
      title: <><CalendarOutlined style={{ marginRight: 6, color: '#52c41a' }} />{syncMode === 'incremental' ? '起始日期' : syncMode === 'full' ? '说明' : '时间范围'}</>,
      children: syncMode === 'full' ? (
        <Alert message="从 2024-01-01 开始拉取所有 PR 数据，耗时较长" type="info" showIcon style={{ fontSize: 13 }} />
      ) : syncMode === 'incremental' ? (
        <DatePicker
          value={singleDate}
          onChange={setSingleDate}
          placeholder="选择起始日期"
          style={{ width: 220 }}
          disabledDate={(current) => current && current > dayjs().endOf('day')}
        />
      ) : (
        <RangePicker
          value={dateRange}
          onChange={(dates) => setDateRange(dates as [dayjs.Dayjs, dayjs.Dayjs] | null)}
          placeholder={['起始日期', '截止日期']}
          style={{ width: 340 }}
        />
      ),
    },
    {
      title: <><GithubOutlined style={{ marginRight: 6, color: '#722ed1' }} />目标仓库</>,
      extra: (
        <Button
          type="primary"
          icon={<CloudUploadOutlined />}
          loading={syncLoading && !currentRecord}
          disabled={isRunning}
          onClick={handleSync}
        >
          {isRunning ? '同步中' : '开始'}
        </Button>
      ),
      children: (
        <Select
          allowClear
          placeholder="选择仓库（留空则同步全部）"
          value={targetRepo}
          onChange={setTargetRepo}
          style={{ width: '100%' }}
          options={repos.map(r => ({ label: r, value: r }))}
          suffixIcon={<SearchOutlined />}
        />
      ),
    },
  ]

  return (
    <div>
      <Row gutter={[16, 16]}>
        {itemCards.map((card, i) => (
          <Col xs={24} md={8} key={i}>
            <Card size="small" title={card.title} extra={card.extra} style={cardStyle} bodyStyle={{ padding: '16px 20px' }}>
              {card.children}
            </Card>
          </Col>
        ))}
      </Row>

      {(currentRecord && (currentRecord.status === 'running' || currentRecord.status === 'pending')) && (
        <Card size="small" style={{ ...cardStyle, marginTop: 16 }} bodyStyle={{ padding: '16px 20px' }}
          extra={
            <Button danger size="small" icon={<StopOutlined />} loading={cancelling} onClick={handleCancel}>
              终止同步
            </Button>
          }>
          <Space style={{ marginBottom: 6 }}>
            {currentRecord.status === 'running'
              ? <Tag color="processing"><ClockCircleOutlined /> 执行中</Tag>
              : <Tag color="default"><ClockCircleOutlined /> 等待中</Tag>}
            {currentRecord.contributors_synced > 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {currentRecord.contributors_synced} 贡献者 · {currentRecord.commits_classified} 提交
              </Text>
            )}
          </Space>
          <Progress
            percent={Math.round(syncProgress)}
            status="active"
            strokeColor={{ '0%': '#108ee9', '100%': '#87d068' }}
            size="small"
          />
          {syncProgressMsg && (
            <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 4 }}>{syncProgressMsg}</Text>
          )}
        </Card>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*                        同步记录历史 Tab                              */
/* ------------------------------------------------------------------ */

function SyncHistoryTab() {
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [records, setRecords] = useState<SyncRecord[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [filterStatus, setFilterStatus] = useState<string | null>(null)
  const [filterRepo, setFilterRepo] = useState<string | null>(null)
  const [repos, setRepos] = useState<string[]>([])
  const [detailRecord, setDetailRecord] = useState<SyncRecord | null>(null)
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const [summary, setSummary] = useState<SyncSummary | null>(null)

  useEffect(() => {
    const init = async () => {
      try {
        const data = await getReposList()
        setRepos(data.repos ?? [])
      } catch { /* ignore */ }
    }
    init()
  }, [])

  const loadHistory = async () => {
    setLoading(true)
    try {
      const offset = (pagination.current - 1) * pagination.pageSize
      const result = await getSyncHistory({
        limit: pagination.pageSize,
        offset,
        repo_name: filterRepo,
        status: filterStatus,
      })
      setRecords(result.records)
      setTotal(result.total)
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }

  const loadSummary = async () => {
    try {
      const data = await getSyncSummary({ repo_name: filterRepo })
      setSummary(data)
    } catch { /* ignore */ }
  }

  const handleCancel = async (recordId: number) => {
    if (cancellingId) return
    setCancellingId(recordId)
    try {
      await cancelSync(recordId)
      message.success('同步任务已取消')
      loadHistory()
    } catch {
      message.error('取消失败')
    } finally {
      setCancellingId(null)
    }
  }

  const handleRetry = async (r: SyncRecord) => {
    if (retryingId) return
    setRetryingId(r.id)
    try {
      const sinceDate = formatTimezone(r.since_date, 'YYYY-MM-DD') || undefined
      const untilDate = formatTimezone(r.until_date, 'YYYY-MM-DD') || undefined
      const fullResync = r.sync_type === 'full'
      const result = await syncContributors({
        fullResync,
        repoName: r.repo_name || undefined,
        sinceDate,
        untilDate,
      })
      if (result.record_id) {
        message.success('重试已触发，后台执行中...')
        loadHistory()
      } else {
        message.warning('重试触发失败')
      }
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err ? (err as { detail: string }).detail : '重试失败'
      message.error(msg)
    } finally {
      setRetryingId(null)
    }
  }

  const [refreshTrigger, setRefreshTrigger] = useState(0)

  useEffect(() => {
    loadHistory()
    loadSummary()
  }, [filterRepo, filterStatus, pagination.current, pagination.pageSize, refreshTrigger])

  useEffect(() => {
    const handler = () => {
      setRefreshTrigger(t => t + 1)
    }
    window.addEventListener('sync-history-refresh', handler)
    return () => window.removeEventListener('sync-history-refresh', handler)
  }, [])

  const statusConfig: Record<string, { color: string; label: string; icon: JSX.Element | null }> = {
    pending: { color: 'default', label: '等待中', icon: <ClockCircleOutlined /> },
    running: { color: 'processing', label: '执行中', icon: <ClockCircleOutlined /> },
    completed: { color: 'success', label: '成功', icon: <CheckCircleOutlined /> },
    failed: { color: 'error', label: '失败', icon: <CloseCircleOutlined /> },
    partial: { color: 'warning', label: '部分成功', icon: <FlagOutlined /> },
    cancelled: { color: 'orange', label: '已取消', icon: <StopOutlined /> },
  }

  const syncTypeConfig: Record<string, { color: string; label: string; icon: JSX.Element }> = {
    full: { color: 'blue', label: '全量', icon: <ReloadOutlined /> },
    incremental: { color: 'green', label: '增量', icon: <CalendarOutlined /> },
    range: { color: 'orange', label: '范围', icon: <ColumnHeightOutlined /> },
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
      align: 'center' as const,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const cfg = statusConfig[status] || { color: 'default', label: status, icon: null }
        return <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
      },
    },
    {
      title: '同步类型',
      dataIndex: 'sync_type',
      key: 'sync_type',
      width: 90,
      render: (v: string) => {
        const cfg = syncTypeConfig[v] || { color: 'default', label: v || '全量', icon: <ReloadOutlined /> }
        return <Tag color={cfg.color} style={{ margin: 0 }}>{cfg.icon} {cfg.label}</Tag>
      },
    },
    {
      title: '来源',
      dataIndex: 'trigger_source',
      key: 'trigger_source',
      width: 90,
      render: (v: string) => {
        const map: Record<string, { color: string; label: string }> = {
          manual: { color: 'blue', label: '手动' },
          scheduled_full: { color: 'cyan', label: '定时全量' },
          scheduled_incremental: { color: 'purple', label: '定时增量' },
        }
        const cfg = map[v ?? ''] || { color: 'default', label: v ?? '其他' }
        return <Tag color={cfg.color} style={{ margin: 0 }}>{cfg.label}</Tag>
      },
    },
    {
      title: '仓库',
      dataIndex: 'repo_name',
      key: 'repo_name',
      width: 180,
      render: (v: string | null) => v || '全部仓库',
    },
    {
      title: '时间范围',
      key: 'date_range',
      width: 200,
      render: (_: unknown, r: SyncRecord) => {
        if (r.sync_type === 'full') {
          return '2024-01-01 ~ 至今'
        }
        if (r.until_date) {
          return `${formatTimezone(r.since_date, 'YYYY-MM-DD')} ~ ${formatTimezone(r.until_date, 'YYYY-MM-DD')}`
        }
        return `${formatTimezone(r.since_date, 'YYYY-MM-DD')} ~ 至今`
      },
    },
    {
      title: '进度',
      key: 'progress',
      width: 120,
      render: (_: unknown, r: SyncRecord) => (
        <div>
          <Progress percent={Math.round(r.progress_percent)} size="small"
            status={r.status === 'failed' ? 'exception' : r.status === 'cancelled' ? 'normal' : r.status === 'completed' ? 'success' : r.status === 'partial' ? 'normal' : 'active'} />
          {r.progress_message && <div style={{ fontSize: 11, color: '#999' }}>{r.progress_message}</div>}
        </div>
      ),
    },
    {
      title: 'PR数',
      dataIndex: 'total_prs_scanned',
      key: 'total_prs_scanned',
      width: 70,
      align: 'right' as const,
      render: (v: number) => v || '-',
    },
    {
      title: '贡献者',
      dataIndex: 'contributors_synced',
      key: 'contributors_synced',
      width: 80,
      align: 'right' as const,
    },
    {
      title: '提交数',
      dataIndex: 'commits_classified',
      key: 'commits_classified',
      width: 80,
      align: 'right' as const,
    },
    {
      title: '耗时',
      key: 'duration',
      width: 80,
      align: 'right' as const,
      render: (_: unknown, r: SyncRecord) => r.duration_seconds > 0 ? `${(r.duration_seconds / 60).toFixed(1)}min` : '-',
    },
    {
      title: '时间',
      key: 'created_at',
      width: 150,
      render: (_: unknown, r: SyncRecord) => formatTimezone(r.created_at, 'YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      fixed: 'right' as const,
      render: (_: unknown, r: SyncRecord) => {
        const errors = typeof r.errors === 'string' ? (r.errors ? JSON.parse(r.errors) : []) : (r.errors || [])
        return (
          <Space size="small">
            {errors.length > 0 && (
              <Tag color="blue" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }} onClick={() => setDetailRecord(r)}>
                <EyeOutlined /> 错误
              </Tag>
            )}
            {(r.status === 'running' || r.status === 'pending') && (
              <Tag color="red" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }} onClick={() => handleCancel(r.id)}>
                <DeleteOutlined /> 终止
              </Tag>
            )}
            {(r.status === 'failed' || r.status === 'partial') && (
              <Tag color="orange" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }} onClick={() => handleRetry(r)}>
                <ReloadOutlined /> 重试
              </Tag>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      {/* 统计卡片 */}
      {summary && (
        <Row gutter={16} style={{ marginBottom: 20 }}>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="总同步次数" value={summary.total_syncs} suffix="次" valueStyle={{ color: '#1890ff' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="成功次数" value={summary.completed_count} suffix="次" valueStyle={{ color: '#52c41a' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="失败次数" value={summary.failed_count} suffix="次" valueStyle={{ color: summary.failed_count > 0 ? '#ff4d4f' : '#999' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="正在运行" value={summary.running_count} suffix="个" valueStyle={{ color: summary.running_count > 0 ? '#faad14' : '#999' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="扫描 PR 总计" value={summary.total_prs_scanned} precision={0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="贡献者总计" value={summary.total_contributors} precision={0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="提交总计" value={summary.total_commits} precision={0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="上次同步" value={summary.last_sync_at ? fromTimezoneNow(summary.last_sync_at) : '无记录'} valueStyle={{ fontSize: 13, color: summary.last_sync_status === 'completed' ? '#52c41a' : '#999' }} />
            </Card>
          </Col>
        </Row>
      )}

      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <Button onClick={() => { loadHistory(); loadSummary() }} icon={<ReloadOutlined />}>刷新</Button>
        <Select
          placeholder="选择仓库"
          allowClear
          showSearch
          style={{ width: 200 }}
          value={filterRepo}
          onChange={(v) => { setFilterRepo(v); setPagination(p => ({ ...p, current: 1 })) }}
          options={[
            { label: '全部仓库', value: null },
            ...repos.map(r => ({ label: r, value: r })),
          ]}
        />
        <Select
          allowClear
          placeholder="状态过滤"
          style={{ width: 140 }}
          value={filterStatus}
          onChange={(v) => { setFilterStatus(v); setPagination(p => ({ ...p, current: 1 })) }}
          options={[
            { label: '等待中', value: 'pending' },
            { label: '执行中', value: 'running' },
            { label: '成功', value: 'completed' },
            { label: '失败', value: 'failed' },
            { label: '部分成功', value: 'partial' },
            { label: '已取消', value: 'cancelled' },
          ]}
        />
        <Text type="secondary">共 {total} 条记录</Text>
      </div>
      {total === 0 && !loading && (
        <Empty
          description="暂无同步记录，请先执行数据同步以采集贡献者数据"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: '20px 0' }}
        />
      )}
      {total > 0 && (
        <Table
          columns={columns}
          dataSource={records}
          loading={loading}
          rowKey="id"
          pagination={{
            ...pagination,
            total,
            showSizeChanger: true,
            showTotal: () => `共 ${total} 条`,
            onChange: (page, size) => setPagination({ current: page, pageSize: size }),
          }}
          size="small"
        />
      )}

      <Modal
        title={<><CloudUploadOutlined /> 同步记录详情</>}
        open={!!detailRecord}
        onCancel={() => setDetailRecord(null)}
        footer={[<Button key="close" onClick={() => setDetailRecord(null)}>关闭</Button>]}
        width={700}
      >
        {detailRecord && (
          <div>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="状态">
                {(() => {
                  const cfg = statusConfig[detailRecord.status] || { color: 'default', label: detailRecord.status, icon: null }
                  return <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
                })()}
              </Descriptions.Item>
              <Descriptions.Item label="同步类型">
                {(() => {
                  const cfg = syncTypeConfig[detailRecord.sync_type] || { color: 'default', label: detailRecord.sync_type || '全量', icon: <ReloadOutlined /> }
                  return <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
                })()}
              </Descriptions.Item>
              <Descriptions.Item label="仓库">
                {detailRecord.repo_name || '全部仓库'}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {detailRecord.trigger_source === 'manual' ? <Tag color="blue">手动</Tag>
                  : detailRecord.trigger_source === 'scheduled_full' ? <Tag color="cyan">定时全量</Tag>
                  : detailRecord.trigger_source === 'scheduled_incremental' ? <Tag color="purple">定时增量</Tag>
                  : detailRecord.trigger_source}
              </Descriptions.Item>
              <Descriptions.Item label="时间范围">
                {detailRecord.sync_type === 'full' ? '2024-01-01 ~ 至今'
                  : detailRecord.until_date
                    ? `${formatTimezone(detailRecord.since_date, 'YYYY-MM-DD')} ~ ${formatTimezone(detailRecord.until_date, 'YYYY-MM-DD')}`
                    : `${formatTimezone(detailRecord.since_date, 'YYYY-MM-DD')} ~ 至今`}
              </Descriptions.Item>
              <Descriptions.Item label="扫描 PR 数">
                {detailRecord.total_prs_scanned || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {formatTimezone(detailRecord.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="开始时间">
                {formatTimezone(detailRecord.started_at)}
              </Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {formatTimezone(detailRecord.completed_at)}
              </Descriptions.Item>
              <Descriptions.Item label="耗时">
                {detailRecord.duration_seconds > 0 ? `${(detailRecord.duration_seconds / 60).toFixed(1)} min` : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="贡献者数">
                {detailRecord.contributors_synced}
              </Descriptions.Item>
              <Descriptions.Item label="提交数">
                {detailRecord.commits_classified}
              </Descriptions.Item>
              <Descriptions.Item label="构建计划数">
                {detailRecord.build_plans_created}
              </Descriptions.Item>
              <Descriptions.Item label="进度">
                <Progress percent={Math.round(detailRecord.progress_percent)} size="small"
                  status={detailRecord.status === 'failed' ? 'exception' : detailRecord.status === 'cancelled' ? 'normal' : detailRecord.status === 'completed' ? 'success' : detailRecord.status === 'partial' ? 'normal' : 'active'} />
              </Descriptions.Item>
            </Descriptions>

            {(() => {
              const errors = typeof detailRecord.errors === 'string' ? (detailRecord.errors ? JSON.parse(detailRecord.errors) : []) : (detailRecord.errors || [])
              if (errors.length > 0) {
                return (
                  <div style={{ marginTop: 16 }}>
                    <Text strong>错误信息 ({errors.length})：</Text>
                    <div style={{ marginTop: 8 }}>
                      {errors.map((err: string, i: number) => (
                        <Alert key={i} message={err} type="error" showIcon style={{ marginBottom: 4 }} />
                      ))}
                    </div>
                  </div>
                )
              }
              return null
            })()}
          </div>
        )}
      </Modal>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*                       定时设置 子组件                              */
/* ------------------------------------------------------------------ */

function ScheduleSettingsPanel() {
  const [scheduleForm] = Form.useForm()
  const [schedule, setSchedule] = useState<ContributorSyncScheduleConfig | null>(null)
  const [scheduleLoading, setScheduleLoading] = useState(true)
  const [scheduleSaving, setScheduleSaving] = useState(false)
  const [cronModalOpen, setCronModalOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setScheduleLoading(true)
      try {
        const data = await getSyncSchedule()
        if (!cancelled) {
          setSchedule(data)
          scheduleForm.setFieldValue('scheduled_enabled', data.enabled)
          scheduleForm.setFieldValue('scheduled_full_cron', data.full_cron)
          scheduleForm.setFieldValue('scheduled_incremental_interval', data.incremental_interval_minutes)
        }
      } catch { /* ignore */ } finally {
        if (!cancelled) setScheduleLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [scheduleForm])

  const handleScheduleSave = async () => {
    try {
      const values = await scheduleForm.validateFields()
      setScheduleSaving(true)
      await updateSyncSchedule({
        enabled: values.scheduled_enabled,
        full_cron: values.scheduled_full_cron,
        incremental_interval_minutes: values.scheduled_incremental_interval,
      })
      message.success('定时同步设置已保存，Scheduler 将在下次重载时生效')
      window.dispatchEvent(new CustomEvent('settings-updated'))
      const data = await getSyncSchedule()
      setSchedule(data)
    } catch (err) {
      message.error('保存定时同步设置失败')
    } finally {
      setScheduleSaving(false)
    }
  }

  const cardStyle = {
    background: '#fff',
    borderRadius: 12,
    boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
    border: '1px solid #f0f0f0',
  }

  const cronToText = (expr: string) => {
    const p = expr.split(' ')
    if (p.length !== 5) return expr
    const [min, hour, day, _month, dow] = p
    if (dow !== '*') {
      const dayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
      const days = dow.split(',').map(n => parseInt(n)).map(n => dayNames[n] || n).join('、')
      return `每周${days} ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
    }
    if (day !== '*') return `每月${day}日 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
    return `每天 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
  }

  return (
    <Spin spinning={scheduleLoading}>
      <Card size="small" style={cardStyle} bodyStyle={{ padding: '24px 32px' }}
        title={
          <Space>
            <ClockCircleOutlined style={{ color: '#722ed1', fontSize: 18 }} />
            <span style={{ fontWeight: 600 }}>定时同步设置</span>
          </Space>
        }
        extra={
          <Space>
            <Form form={scheduleForm} style={{ marginBottom: 0 }}>
              <Form.Item name="scheduled_enabled" valuePropName="checked" style={{ marginBottom: 0 }}>
                <Switch size="small" checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
            </Form>
            <Text type="secondary" style={{ fontSize: 12 }}>启用定时</Text>
          </Space>
        }
      >
        <Descriptions bordered size="small" column={1}
          style={{ marginBottom: 0 }}
        >
          <Descriptions.Item label={
            <Space><RocketOutlined style={{ color: '#fa8c16' }} /><span>全量同步时间</span></Space>
          }>
            <Space>
              <Tag color="purple" style={{ padding: '4px 12px', fontSize: 13 }}>{cronToText(schedule?.full_cron || '0 2 * * *')}</Tag>
              <Text type="secondary" style={{ fontSize: 11 }}>{schedule?.full_cron || '0 2 * * *'}</Text>
              <Button type="link" size="small" onClick={() => setCronModalOpen(true)}>修改</Button>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label={
            <Space><ReloadOutlined style={{ color: '#13c2c2' }} /><span>增量同步间隔</span></Space>
          }>
            <Space>
              <Form form={scheduleForm}>
                <Form.Item name="scheduled_incremental_interval" style={{ marginBottom: 0, display: 'inline-block' }}>
                  <InputNumber min={30} max={1440} step={30} style={{ width: 140 }} addonAfter="分钟" />
                </Form.Item>
              </Form>
              <Text type="secondary" style={{ fontSize: 11 }}>
                当前 {(schedule?.incremental_interval_minutes ?? 360) / 60} 小时
              </Text>
            </Space>
          </Descriptions.Item>
        </Descriptions>

        <Divider style={{ margin: '16px 0' }} />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            修改后需保存并等待下次 Scheduler 重载生效
          </Text>
          <Button type="primary" loading={scheduleSaving} onClick={handleScheduleSave}>
            保存设置
          </Button>
        </div>
      </Card>

      <CronModal
        open={cronModalOpen}
        cron={schedule?.full_cron || '0 2 * * *'}
        onChange={(newCron) => {
          scheduleForm.setFieldValue('scheduled_full_cron', newCron)
        }}
        onClose={() => setCronModalOpen(false)}
      />
    </Spin>
  )
}

/* ------------------------------------------------------------------ */
/*                          数据同步 Tab（含子Tab）                    */
/* ------------------------------------------------------------------ */

function SyncTab({ selectedRepo }: { selectedRepo?: string }) {
  return (
    <Card size="small" bordered={false} style={{ borderRadius: 12 }} bodyStyle={{ padding: '0 0 0 0' }}>
      <Tabs
        tabPosition="left"
        destroyInactiveTabPane
        items={[
          {
            key: 'manual',
            label: <span><CloudUploadOutlined /> 手动同步</span>,
            children: <ManualSyncPanel selectedRepo={selectedRepo} />,
          },
          {
            key: 'history',
            label: <span><ReloadOutlined /> 同步记录</span>,
            children: <SyncHistoryTab />,
          },
          {
            key: 'schedule',
            label: <span><ClockCircleOutlined /> 定时设置</span>,
            children: <ScheduleSettingsPanel />,
          },
        ]}
      />
    </Card>
  )
}

/* ------------------------------------------------------------------ */
/*                           主页面组件                                */
/* ------------------------------------------------------------------ */

let _pollTimer: ReturnType<typeof setInterval> | null = null

function ContributorBoard() {
   const navigate = useNavigate()
   const searchParams = new URLSearchParams(window.location.search)
    const [activeTab, setActiveTab] = useState<'classification' | 'builds' | 'quadrant' | 'ranking' | 'sync' | 'settings'>(
     (searchParams.get('tab') as any) || 'classification'
    )
   const [selectedRepo, setSelectedRepo] = useState<string | undefined>(
     searchParams.get('repo') || undefined
   )
   const [repos, setRepos] = useState<string[]>([])

   const loadRepos = async () => {
    try {
      const data = await getReposList()
      setRepos(data.repos ?? [])
    } catch { /* ignore */ }
  }

   const tabRef = useRef(activeTab)
   const [settingsRefreshTrigger, setSettingsRefreshTrigger] = useState(0)

   useEffect(() => {
    tabRef.current = activeTab
   }, [activeTab])

   useEffect(() => {
    const handler = () => setSettingsRefreshTrigger(t => t + 1)
    window.addEventListener('settings-updated', handler)
    return () => window.removeEventListener('settings-updated', handler)
   }, [])

   useEffect(() => {
    loadRepos()
   }, [])

   const updateUrlState = (tab: string, repo: string | undefined) => {
    const params = new URLSearchParams()
    if (tab && tab !== 'classification') params.set('tab', tab)
    if (repo) params.set('repo', repo)
    const query = params.toString()
    navigate(`/contributor-board${query ? '?' + query : ''}`, { replace: true })
   }

   useEffect(() => {
    updateUrlState(activeTab, selectedRepo)
   }, [selectedRepo])

   const tabItems = [
    {
      key: 'classification',
      label: <><CodeOutlined /> 提交分类统计</>,
      children: <ClassificationTab selectedRepo={selectedRepo} />,
    },
    {
      key: 'builds',
      label: <><RocketOutlined /> 构建优化统计</>,
      children: <BuildsTab selectedRepo={selectedRepo} />,
    },
    {
      key: 'quadrant',
      label: <><GithubOutlined /> 四象限看板</>,
      children: <QuadrantTab refreshKey={settingsRefreshTrigger} selectedRepo={selectedRepo} />,
    },
    {
      key: 'ranking',
      label: <><TrophyOutlined /> 排行榜</>,
      children: <RankingTab loading={false} selectedRepo={selectedRepo} />,
    },
    {
      key: 'sync',
      label: <><CloudUploadOutlined /> 数据同步</>,
      children: <SyncTab selectedRepo={selectedRepo} />,
    },
    {
      key: 'settings',
      label: <><ToolOutlined /> 分析设置</>,
      children: <ContributorBoardSettingsPage />,
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

        {repos.length === 0 && (
          <Result
            status="warning"
            title="暂无贡献者数据"
            subTitle="首次使用请先执行全量数据同步，采集 GitHub PR/提交数据后即可使用分析功能"
            extra={<Button type="primary" onClick={() => { setActiveTab('sync'); updateUrlState('sync', undefined) }}>前往数据同步</Button>}
            style={{ padding: '40px 0' }}
          />
      )}

        {repos.length > 0 && (
         <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
           <Select
             allowClear
             placeholder="选择仓库（影响所有 Tab）"
             value={selectedRepo}
             onChange={setSelectedRepo}
             style={{ width: 260 }}
             options={repos.map(r => ({ label: r, value: r }))}
             suffixIcon={<SearchOutlined />}
           />
         </div>
       )}

       <Tabs
        activeKey={activeTab}
        items={tabItems}
        destroyInactiveTabPane
        onChange={(key) => {
          setActiveTab(key as typeof activeTab)
          updateUrlState(key, selectedRepo)
        }}
       />
     </div>
   )
}

export { ContributorDetailPage }
export default ContributorBoard
