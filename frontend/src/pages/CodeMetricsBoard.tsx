import { useState, useEffect, type ReactNode } from 'react'
import { Card, Tabs, Table, Statistic, Row, Col, Tag, Empty, Typography, Select, Spin, message, Button, Input, Space, Alert, Modal, Breadcrumb, Tooltip, Popover } from 'antd'
import { CodeOutlined, DownloadOutlined, SyncOutlined, ArrowRightOutlined, ThunderboltOutlined, FileTextOutlined, FunctionOutlined, BarsOutlined, GithubOutlined, LinkOutlined } from '@ant-design/icons'
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend,
  PieChart, Pie, Cell, BarChart, Bar,
} from 'recharts'
import {
  getOverview, getComplexity, getDuplication, getHeatmap, getTrends,
  syncHeatmap, compareVersions, exportMetrics,
  getDerivedMetrics, getAlerts, getCICorrelation, triggerCollection,
  getFileComplexity, getFileHeatmapDetail,
  listFiles, listFunctions, getDrilldown,
  type CodeMetricsOverview, type ComplexityItem, type DuplicationItem,
  type HeatmapItem, type TrendItem, type CompareResult,
  type DerivedMetrics, type CodeMetricsAlert, type CICorrelationItem,
  type FileAggItem, type FunctionDetailItem, type DrilldownResult,
} from '../services/codeMetrics'

const { Title, Text, Paragraph } = Typography

const PIE_COLORS = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2', '#eb2f96', '#a0d911']

// vllm-ascend 仓库 GitHub 配置
const GITHUB_REPO_URL = 'https://github.com/vllm-project/vllm-ascend'
// TODO(follow-up): 当前硬编码 main 分支。若 snapshot 采集自非 main 分支或文件被改/删，
// #L<line> 锚点会指向错误位置。待 CodeMetricsSnapshot 增加 commit_sha 字段后，
// 可改用 `${repo}/blob/${sha}/${path}#L${line}` 实现精确锚点。
const GITHUB_DEFAULT_BRANCH = 'main'

const LANG_GROUP_ORDER = ['C/C++', 'Python', 'CMake/Shell/YAML', 'JavaScript', 'Go', 'Java', 'Others'] as const

function groupLanguageLoc(loc: Record<string, number>): Record<string, number> {
  const groups: Record<string, number> = {}
  LANG_GROUP_ORDER.forEach((k) => { groups[k] = 0 })
  for (const [lang, val] of Object.entries(loc)) {
    const lower = lang.toLowerCase()
    let key: string
    if (['c++', 'c', 'c/c++ header', 'c header'].includes(lower)) key = 'C/C++'
    else if (lower === 'python') key = 'Python'
    else if (['cmake', 'shell', 'bourne shell', 'c shell', 'fish shell', 'yaml', 'make'].includes(lower)) key = 'CMake/Shell/YAML'
    else if (['javascript', 'typescript'].includes(lower)) key = 'JavaScript'
    else if (lower === 'go') key = 'Go'
    else if (lower === 'java') key = 'Java'
    else key = 'Others'
    groups[key] += val
  }
  const result: Record<string, number> = {}
  LANG_GROUP_ORDER.forEach((k) => { if (groups[k] > 0) result[k] = groups[k] })
  return result
}
// 注意：与后端 backend/api/v1/code_metrics.py:_KNOWN_MODULES 保持同步，
// 新增模块时请同时修改两端。
const KNOWN_MODULE_SEGMENTS = ['vllm_ascend', 'csrc', 'tests', 'benchmarks', 'tools', 'docs', 'examples', 'configs']

/**
 * 将存储的 file_path 转换为 vllm-ascend 仓库的相对路径。
 * 存储的路径可能是绝对路径（如 /tmp/checkout/vllm-ascend/vllm_ascend/core/__init__.py）
 * 或相对路径（如 vllm_ascend/core/__init__.py）。
 * 通过定位第一个已知模块段，返回从该段开始的相对路径。
 */
function toRepoRelativePath(filePath: string): string {
  if (!filePath) return ''
  const norm = filePath.replace(/\\/g, '/')
  const parts = norm.split('/').filter((p) => p && p !== '.')
  const idx = parts.findIndex((seg) => KNOWN_MODULE_SEGMENTS.includes(seg))
  if (idx >= 0) return parts.slice(idx).join('/')
  // 没匹配到已知模块：若路径只有一段（文件名），直接返回；否则返回原样
  return parts.length === 1 ? parts[0] : norm
}

/**
 * 构造 GitHub 文件查看链接（main 分支）。
 * 可选 line 参数指定起始行号，会追加 #L<line> 锚点。
 */
function githubFileUrl(filePath: string, line?: number | null): string {
  const rel = toRepoRelativePath(filePath)
  const base = `${GITHUB_REPO_URL}/blob/${GITHUB_DEFAULT_BRANCH}/${rel}`
  return line && line > 0 ? `${base}#L${line}` : base
}

/**
 * 文件链接 Popover：点击文件行时弹出，展示 GitHub 链接并提供跳转/复制。
 * 同时提供「查看函数列表」按钮，保留原有下钻能力。
 */
function FileLinkPopover({
  filePath,
  onViewFunctions,
  children,
}: {
  filePath: string
  onViewFunctions: () => void
  children: ReactNode
}) {
  const url = githubFileUrl(filePath)
  const rel = toRepoRelativePath(filePath)
  const content = (
    <div style={{ width: 460 }}>
      <Paragraph style={{ marginBottom: 8 }}>
        <Text type="secondary">仓库路径（main 分支）：</Text>
        <br />
        <Text code copyable style={{ wordBreak: 'break-all', fontSize: 12 }}>
          {rel}
        </Text>
      </Paragraph>
      <Paragraph style={{ marginBottom: 12 }}>
        <Text type="secondary">GitHub 链接：</Text>
        <br />
        <Text code style={{ wordBreak: 'break-all', fontSize: 11 }}>
          {url}
        </Text>
      </Paragraph>
      <Space>
        <Button
          type="primary"
          size="small"
          icon={<GithubOutlined />}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
        >
          在 GitHub 查看
        </Button>
        <Button
          size="small"
          icon={<FunctionOutlined />}
          onClick={() => onViewFunctions()}
        >
          查看函数列表
        </Button>
        <Button
          size="small"
          icon={<LinkOutlined />}
          onClick={() => {
            navigator.clipboard?.writeText(url).then(
              () => message.success('链接已复制'),
              () => message.error('复制失败'),
            )
          }}
        >
          复制链接
        </Button>
      </Space>
    </div>
  )
  return (
    <Popover content={content} trigger="click" placement="leftTop" destroyTooltipOnHide>
      {children}
    </Popover>
  )
}

// ===========================================================================
// 下钻明细弹窗：支持 LOC 分布、文件列表、函数列表、维度聚合，带面包屑导航
// ===========================================================================

type DrillView =
  | { kind: 'loc-breakdown' }
  | { kind: 'files'; language?: string; module?: string; search?: string }
  | { kind: 'functions'; language?: string; module?: string; file_path?: string; search?: string; min_complexity?: number }
  | { kind: 'drilldown'; language?: string; module?: string }

interface DrillState {
  open: boolean
  title: string
  stack: DrillView[]
}

const initialDrill: DrillState = { open: false, title: '', stack: [] }

function DrillBreadcrumb({ stack, onJump }: { stack: DrillView[]; onJump: (idx: number) => void }) {
  const labels = stack.map((v, i) => {
    if (v.kind === 'loc-breakdown') return '代码行数分布'
    if (v.kind === 'files') {
      const parts = ['文件列表']
      if (v.language) parts.push(`语言=${v.language}`)
      if (v.module) parts.push(`模块=${v.module}`)
      return parts.join(' · ')
    }
    if (v.kind === 'functions') {
      const parts = ['函数列表']
      if (v.language) parts.push(`语言=${v.language}`)
      if (v.module) parts.push(`模块=${v.module}`)
      if (v.file_path) parts.push(`文件=${v.file_path.split('/').pop()}`)
      return parts.join(' · ')
    }
    if (v.kind === 'drilldown') {
      const parts = ['维度聚合']
      if (v.language) parts.push(`语言=${v.language}`)
      if (v.module) parts.push(`模块=${v.module}`)
      return parts.join(' · ')
    }
    return `层级${i + 1}`
  })
  return (
    <Breadcrumb
      style={{ marginBottom: 12 }}
      items={labels.map((l, i) => ({
        title: i === labels.length - 1 ? <Text strong>{l}</Text> : <a onClick={() => onJump(i)}>{l}</a>,
      }))}
    />
  )
}

function LocBreakdownView({
  languageLoc,
  moduleLoc,
  onDrillLanguage,
  onDrillModule,
}: {
  languageLoc: Record<string, number>
  moduleLoc: Record<string, number>
  onDrillLanguage: (lang: string) => void
  onDrillModule: (mod: string) => void
}) {
  const groupedLang = groupLanguageLoc(languageLoc)
  const totalLang = Object.values(groupedLang).reduce((a, b) => a + b, 0) || 1
  const totalMod = Object.values(moduleLoc).reduce((a, b) => a + b, 0) || 1
  const langRows = Object.entries(groupedLang)
    .map(([name, value]) => ({ name, value, pct: (value / totalLang) * 100 }))
    .sort((a, b) => b.value - a.value)
  const modRows = Object.entries(moduleLoc)
    .map(([name, value]) => ({ name, value, pct: (value / totalMod) * 100 }))
    .sort((a, b) => b.value - a.value)

  return (
    <Row gutter={16}>
      <Col span={12}>
        <Card title="语言分布（按代码行数）" size="small" extra={<FileTextOutlined />}>
          <div style={{ height: 200, marginBottom: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={langRows} layout="vertical" margin={{ left: 20, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" />
                <YAxis type="category" dataKey="name" width={70} />
                <RechartsTooltip formatter={(v: any) => `${Number(v).toLocaleString()} 行`} />
                <Bar dataKey="value" name="代码行数" fill="#1677ff" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Table
            dataSource={langRows}
            rowKey="name"
            size="small"
            pagination={false}
            onRow={(r) => ({ onClick: () => onDrillLanguage(r.name), style: { cursor: 'pointer' } })}
            columns={[
              { title: '语言', dataIndex: 'name', width: 100, render: (v: string) => <Tag color="blue">{v}</Tag> },
              { title: '代码行数', dataIndex: 'value', width: 120, render: (v: number) => v.toLocaleString() },
              { title: '占比', dataIndex: 'pct', width: 80, render: (v: number) => `${v.toFixed(1)}%` },
              { title: '操作', width: 80, render: () => <a>下钻 →</a> },
            ]}
          />
        </Card>
      </Col>
      <Col span={12}>
        <Card title="模块分布（按代码行数）" size="small" extra={<BarsOutlined />}>
          <div style={{ height: 200, marginBottom: 12 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={modRows} layout="vertical" margin={{ left: 20, right: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" />
                <YAxis type="category" dataKey="name" width={90} />
                <RechartsTooltip formatter={(v: any) => `${Number(v).toLocaleString()} 行`} />
                <Bar dataKey="value" name="代码行数" fill="#52c41a" />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Table
            dataSource={modRows}
            rowKey="name"
            size="small"
            pagination={false}
            onRow={(r) => ({ onClick: () => onDrillModule(r.name), style: { cursor: 'pointer' } })}
            columns={[
              { title: '模块', dataIndex: 'name', width: 120, render: (v: string) => <Tag color="green">{v}</Tag> },
              { title: '代码行数', dataIndex: 'value', width: 120, render: (v: number) => v.toLocaleString() },
              { title: '占比', dataIndex: 'pct', width: 80, render: (v: number) => `${v.toFixed(1)}%` },
              { title: '操作', width: 80, render: () => <a>下钻 →</a> },
            ]}
          />
        </Card>
      </Col>
    </Row>
  )
}

function FilesListView({
  language,
  module,
  search: initialSearch,
  onOpenFile,
}: {
  language?: string
  module?: string
  search?: string
  onOpenFile: (fp: string) => void
}) {
  const [search, setSearch] = useState(initialSearch || '')
  const [data, setData] = useState<FileAggItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)

  const fetchData = async (p: number, ps: number, s: string) => {
    setLoading(true)
    try {
      const r = await listFiles({
        language,
        module,
        search: s || undefined,
        limit: ps,
        offset: (p - 1) * ps,
      })
      setData(r.items)
      setTotal(r.total)
    } catch (e) {
      console.error(e)
      message.error('加载文件列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData(page, pageSize, search)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, language, module])

  return (
    <div>
      <Space style={{ marginBottom: 12 }}>
        <Input.Search
          placeholder="搜索文件路径"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(v) => {
            setPage(1)
            fetchData(1, pageSize, v)
          }}
          style={{ width: 320 }}
          allowClear
        />
        {(language || module) && (
          <Tag color="blue" closable={false}>
            {language ? `语言: ${language}` : `模块: ${module}`}
          </Tag>
        )}
        <Text type="secondary">共 {total} 个文件</Text>
      </Space>
      <Table
        dataSource={data}
        rowKey="file_path"
        loading={loading}
        size="small"
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个文件`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
        columns={[
          {
            title: '文件路径',
            dataIndex: 'file_path',
            ellipsis: true,
            render: (v: string, r: FileAggItem) => (
              <FileLinkPopover filePath={v} onViewFunctions={() => onOpenFile(r.file_path)}>
                <a onClick={(e) => e.preventDefault()} style={{ cursor: 'pointer' }}>{v}</a>
              </FileLinkPopover>
            ),
          },
          { title: '语言', dataIndex: 'language', width: 90, render: (v: string) => <Tag color="blue">{v}</Tag> },
          { title: '模块', dataIndex: 'module', width: 110, render: (v: string) => <Tag color="green">{v}</Tag> },
          { title: '函数数', dataIndex: 'function_count', width: 80, sorter: (a: FileAggItem, b: FileAggItem) => a.function_count - b.function_count },
          { title: '总复杂度', dataIndex: 'total_complexity', width: 90, sorter: (a: FileAggItem, b: FileAggItem) => a.total_complexity - b.total_complexity },
          { title: '最大复杂度', dataIndex: 'max_complexity', width: 90, render: (v: number) => <Tag color={(v || 0) > 20 ? 'red' : (v || 0) > 15 ? 'orange' : 'green'}>{v}</Tag> },
          { title: '函数行数', dataIndex: 'total_function_lines', width: 90 },
          {
            title: '操作',
            width: 140,
            render: (_: unknown, r: FileAggItem) => (
              <Space size={0} onClick={(e) => e.stopPropagation()}>
                <Tooltip title="查看函数列表">
                  <Button type="link" size="small" icon={<FunctionOutlined />} onClick={() => onOpenFile(r.file_path)} />
                </Tooltip>
                <Tooltip title="在 GitHub 查看（main 分支）">
                  <Button
                    type="link"
                    size="small"
                    icon={<GithubOutlined />}
                    href={githubFileUrl(r.file_path)}
                    target="_blank"
                    rel="noopener noreferrer"
                  />
                </Tooltip>
              </Space>
            ),
          },
        ]}
      />
    </div>
  )
}

function FunctionsListView({
  language,
  module,
  file_path,
  search: initialSearch,
  min_complexity,
}: {
  language?: string
  module?: string
  file_path?: string
  search?: string
  min_complexity?: number
}) {
  const [search, setSearch] = useState(initialSearch || '')
  const [data, setData] = useState<FunctionDetailItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false)

  const fetchData = async (p: number, ps: number, s: string) => {
    setLoading(true)
    try {
      const r = await listFunctions({
        language,
        module,
        file_path,
        search: s || undefined,
        min_complexity,
        limit: ps,
        offset: (p - 1) * ps,
      })
      setData(r.items)
      setTotal(r.total)
    } catch (e) {
      console.error(e)
      message.error('加载函数列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData(page, pageSize, search)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, language, module, file_path, min_complexity])

  return (
    <div>
      <Space style={{ marginBottom: 12, flexWrap: 'wrap' }}>
        <Input.Search
          placeholder="搜索函数名或文件路径"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(v) => {
            setPage(1)
            fetchData(1, pageSize, v)
          }}
          style={{ width: 320 }}
          allowClear
        />
        {(language || module || file_path) && (
          <Tag color="blue">
            {file_path ? `文件: ${file_path.split('/').pop()}` : language ? `语言: ${language}` : `模块: ${module}`}
          </Tag>
        )}
        {min_complexity !== undefined && <Tag color="orange">复杂度 ≥ {min_complexity}</Tag>}
        <Text type="secondary">共 {total} 个函数</Text>
      </Space>
      {file_path && (
        <Card size="small" style={{ marginBottom: 12, background: '#fafafa' }}>
          <Space>
            <GithubOutlined />
            <Text strong>当前文件：</Text>
            <Text code style={{ wordBreak: 'break-all' }}>{toRepoRelativePath(file_path)}</Text>
            <Button
              type="link"
              size="small"
              icon={<GithubOutlined />}
              href={githubFileUrl(file_path)}
              target="_blank"
              rel="noopener noreferrer"
            >
              在 GitHub 查看（main 分支）
            </Button>
            <Button
              size="small"
              icon={<LinkOutlined />}
              onClick={() => {
                navigator.clipboard?.writeText(githubFileUrl(file_path)).then(
                  () => message.success('链接已复制'),
                  () => message.error('复制失败'),
                )
              }}
            >
              复制链接
            </Button>
          </Space>
        </Card>
      )}
      <Table
        dataSource={data}
        rowKey={(r) => `${r.file_path}:${r.function_name}:${r.start_line ?? ''}`}
        loading={loading}
        size="small"
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 个函数`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
        columns={[
          { title: '函数', dataIndex: 'function_name', width: 220, ellipsis: true },
          {
            title: '文件路径',
            dataIndex: 'file_path',
            ellipsis: true,
            render: (v: string) => (
              <Tooltip title="点击在 GitHub 查看文件">
                <a
                  href={githubFileUrl(v)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  {v}
                </a>
              </Tooltip>
            ),
          },
          { title: '语言', dataIndex: 'language', width: 80, render: (v: string) => <Tag color="blue">{v}</Tag> },
          { title: '模块', dataIndex: 'module', width: 100, render: (v: string) => <Tag color="green">{v}</Tag> },
          { title: '圈复杂度', dataIndex: 'cyclomatic_complexity', width: 90, sorter: (a: FunctionDetailItem, b: FunctionDetailItem) => (a.cyclomatic_complexity || 0) - (b.cyclomatic_complexity || 0), render: (v: number) => <Tag color={(v || 0) > 20 ? 'red' : (v || 0) > 15 ? 'orange' : 'green'}>{v}</Tag> },
          { title: '嵌套深度', dataIndex: 'max_nesting_depth', width: 90 },
          { title: '函数行数', dataIndex: 'function_lines', width: 90 },
          {
            title: '起始行',
            dataIndex: 'start_line',
            width: 100,
            render: (v: number | null, r: FunctionDetailItem) => v && v > 0 ? (
              <Tooltip title="在 GitHub 查看此函数位置">
                <a
                  href={githubFileUrl(r.file_path, v)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  <GithubOutlined /> L{v}
                </a>
              </Tooltip>
            ) : '-',
          },
        ]}
      />
    </div>
  )
}

function DrilldownView({
  language,
  module,
  onViewFiles,
  onViewFunctions,
  onOpenFile,
}: {
  language?: string
  module?: string
  onViewFiles: () => void
  onViewFunctions: () => void
  onOpenFile: (fp: string) => void
}) {
  const [data, setData] = useState<DrilldownResult | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    getDrilldown(language, module, 10)
      .then(setData)
      .catch((e) => {
        console.error(e)
        message.error('加载维度聚合失败')
      })
      .finally(() => setLoading(false))
  }, [language, module])

  if (loading) return <Spin />
  if (!data || !data.has_data) return <Empty description="暂无数据" />

  const dim = language ? `语言: ${language}` : module ? `模块: ${module}` : '全量'
  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message={
          <Space split={<span>·</span>}>
            <Text strong>{dim}</Text>
            <span>代码行数: <b>{data.loc.toLocaleString()}</b></span>
            <span>文件数: <b>{data.file_count}</b></span>
            <span>函数数: <b>{data.function_count}</b></span>
            <span>函数总行数: <b>{data.total_function_lines.toLocaleString()}</b></span>
            <span>平均复杂度: <b>{data.avg_complexity}</b></span>
            <span>最大复杂度: <b>{data.max_complexity}</b></span>
          </Space>
        }
      />
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<FileTextOutlined />} onClick={onViewFiles}>查看文件列表</Button>
        <Button icon={<FunctionOutlined />} onClick={onViewFunctions}>查看函数列表</Button>
      </Space>
      <Row gutter={16}>
        <Col span={12}>
          <Card title="Top 复杂度文件" size="small" extra={<Text type="secondary" style={{ fontSize: 12 }}>点击查看函数 / GitHub</Text>}>
            <Table
              dataSource={data.top_files}
              rowKey="file_path"
              size="small"
              pagination={false}
              onRow={(r) => ({ onClick: () => onOpenFile(r.file_path), style: { cursor: 'pointer' } })}
              columns={[
                { title: '文件', dataIndex: 'file_path', ellipsis: true },
                { title: '函数数', dataIndex: 'function_count', width: 70 },
                { title: '总复杂度', dataIndex: 'total_complexity', width: 80 },
                {
                  title: 'GitHub',
                  width: 70,
                  render: (_: unknown, r: FileAggItem) => (
                    <Tooltip title="在 GitHub 查看（main 分支）">
                      <Button
                        type="link"
                        size="small"
                        icon={<GithubOutlined />}
                        href={githubFileUrl(r.file_path)}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Tooltip>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="Top 复杂度函数" size="small" extra={<Text type="secondary" style={{ fontSize: 12 }}>点击查看文件函数</Text>}>
            <Table
              dataSource={data.top_functions}
              rowKey={(r) => `${r.file_path}:${r.function_name}:${r.start_line ?? ''}`}
              size="small"
              pagination={false}
              onRow={(r) => ({ onClick: () => onOpenFile(r.file_path), style: { cursor: 'pointer' } })}
              columns={[
                { title: '函数', dataIndex: 'function_name', ellipsis: true },
                { title: '文件', dataIndex: 'file_path', ellipsis: true },
                { title: '复杂度', dataIndex: 'cyclomatic_complexity', width: 70, render: (v: number) => <Tag color={(v || 0) > 20 ? 'red' : 'orange'}>{v}</Tag> },
                {
                  title: 'GitHub',
                  width: 70,
                  render: (_: unknown, r: FunctionDetailItem) => (
                    <Tooltip title={r.start_line ? `在 GitHub 查看第 ${r.start_line} 行` : '在 GitHub 查看'}>
                      <Button
                        type="link"
                        size="small"
                        icon={<GithubOutlined />}
                        href={githubFileUrl(r.file_path, r.start_line)}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Tooltip>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </div>
  )
}

function DrillModal({
  state,
  onClose,
  languageLoc,
  moduleLoc,
}: {
  state: DrillState
  onClose: () => void
  languageLoc: Record<string, number>
  moduleLoc: Record<string, number>
}) {
  const [stack, setStack] = useState<DrillView[]>([])

  useEffect(() => {
    if (state.open) setStack(state.stack)
  }, [state.open, state.stack])

  const current = stack[stack.length - 1]
  const push = (v: DrillView) => setStack((s) => [...s, v])
  const jump = (idx: number) => setStack((s) => s.slice(0, idx + 1))

  const renderView = () => {
    if (!current) return null
    if (current.kind === 'loc-breakdown') {
      return (
        <LocBreakdownView
          languageLoc={languageLoc}
          moduleLoc={moduleLoc}
          onDrillLanguage={(lang) => push({ kind: 'drilldown', language: lang })}
          onDrillModule={(mod) => push({ kind: 'drilldown', module: mod })}
        />
      )
    }
    if (current.kind === 'drilldown') {
      return (
        <DrilldownView
          language={current.language}
          module={current.module}
          onViewFiles={() => push({ kind: 'files', language: current.language, module: current.module })}
          onViewFunctions={() => push({ kind: 'functions', language: current.language, module: current.module })}
          onOpenFile={(fp) => push({ kind: 'functions', language: current.language, module: current.module, file_path: fp })}
        />
      )
    }
    if (current.kind === 'files') {
      return (
        <FilesListView
          language={current.language}
          module={current.module}
          search={current.search}
          onOpenFile={(fp) => push({ kind: 'functions', file_path: fp })}
        />
      )
    }
    if (current.kind === 'functions') {
      return (
        <FunctionsListView
          language={current.language}
          module={current.module}
          file_path={current.file_path}
          search={current.search}
          min_complexity={current.min_complexity}
        />
      )
    }
    return null
  }

  return (
    <Modal
      open={state.open}
      title={state.title}
      onCancel={onClose}
      footer={null}
      width={1000}
      destroyOnClose
    >
      {stack.length > 1 && <DrillBreadcrumb stack={stack} onJump={jump} />}
      {renderView()}
    </Modal>
  )
}

function CodeMetricsBoard() {
  const [activeTab, setActiveTab] = useState('overview')
  const [period, setPeriod] = useState(30)
  const [loading, setLoading] = useState(false)

  const [overview, setOverview] = useState<CodeMetricsOverview | null>(null)
  const [complexity, setComplexity] = useState<ComplexityItem[]>([])
  const [duplication, setDuplication] = useState<DuplicationItem[]>([])
  const [heatmap, setHeatmap] = useState<HeatmapItem[]>([])
  const [trends, setTrends] = useState<TrendItem[]>([])

  const [compareResult, setCompareResult] = useState<CompareResult | null>(null)
  const [tagA, setTagA] = useState('')
  const [tagB, setTagB] = useState('')
  const [comparing, setComparing] = useState(false)
  const [tabLoading, setTabLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const [derived, setDerived] = useState<DerivedMetrics | null>(null)
  const [alerts, setAlerts] = useState<CodeMetricsAlert[]>([])
  const [ciCorrelation, setCICorrelation] = useState<CICorrelationItem[]>([])
  const [triggering, setTriggering] = useState(false)

  const [detailModal, setDetailModal] = useState<{ visible: boolean; title: string; content: ReactNode }>({ visible: false, title: '', content: null })
  const [drillState, setDrillState] = useState<DrillState>(initialDrill)
  const [expandedFile, setExpandedFile] = useState<string | null>(null)
  const [fileFunctions, setFileFunctions] = useState<any[]>([])

  const openDrill = (title: string, view: DrillView) => setDrillState({ open: true, title, stack: [view] })

  const ensureComplexity = async () => {
    if (complexity.length === 0) {
      try { const r = await getComplexity(100); setComplexity(r.items) } catch { /* ignore */ }
    }
    return complexity
  }
  const ensureDuplication = async () => {
    if (duplication.length === 0) {
      try { const r = await getDuplication(100); setDuplication(r.items) } catch { /* ignore */ }
    }
    return duplication
  }

  const loadOverview = async (d: number) => {
    setLoading(true)
    try { setOverview(await getOverview(d)) } catch (e) { console.error('Overview failed:', e); message.error('加载数据失败') }
    finally { setLoading(false) }
    getDerivedMetrics(period).then(r => setDerived(r)).catch(() => {})
  }

  useEffect(() => {
    getAlerts().then(r => setAlerts(r.alerts)).catch(() => {})
  }, [])

  useEffect(() => {
    if (activeTab === 'overview') loadOverview(period)
    else if (activeTab === 'complexity') {
      setTabLoading(true)
      getComplexity(100).then(r => setComplexity(r.items)).catch(e => { console.error(e); message.error('加载数据失败') }).finally(() => setTabLoading(false))
    }
    else if (activeTab === 'duplication') {
      setTabLoading(true)
      getDuplication(100).then(r => setDuplication(r.items)).catch(e => { console.error(e); message.error('加载数据失败') }).finally(() => setTabLoading(false))
    }
    else if (activeTab === 'heatmap') {
      setTabLoading(true)
      getHeatmap(50).then(r => setHeatmap(r.items)).catch(e => { console.error(e); message.error('加载数据失败') }).finally(() => setTabLoading(false))
    }
    else if (activeTab === 'trends') {
      setTabLoading(true)
      getTrends(period).then(r => setTrends(r.items)).catch(e => { console.error(e); message.error('加载数据失败') }).finally(() => setTabLoading(false))
    }
    else if (activeTab === 'compare') {
      // no auto-load, wait for user input
    }
    else if (activeTab === 'ci-correlation') {
      setTabLoading(true)
      getCICorrelation(period).then(r => setCICorrelation(r.items)).catch(e => { console.error(e); message.error('加载失败') }).finally(() => setTabLoading(false))
    }
  }, [activeTab, period])

  const radarData = overview?.health_scores ? [
    { dimension: '复杂度', score: overview.health_scores.complexity || 0 },
    { dimension: '重复率', score: overview.health_scores.duplication || 0 },
    { dimension: '函数体量', score: overview.health_scores.method_size || 0 },
    { dimension: '技术债务', score: overview.health_scores.tech_debt || 0 },
    { dimension: 'Lint', score: overview.health_scores.lint || 0 },
  ] : []

  const languagePieData = overview?.language_loc
    ? Object.entries(groupLanguageLoc(overview.language_loc)).map(([name, value]) => ({ name, value }))
    : []

  const modulePieData = overview?.module_loc
    ? Object.entries(overview.module_loc).map(([name, value]) => ({ name, value }))
    : []

  const tabItems = [
    {
      key: 'overview',
      label: '总览',
      children: (
        <Spin spinning={loading}>
          {!overview?.has_data ? (
            <Empty description="暂无代码度量数据，请等待 CI 采集" />
          ) : (
            <>
              <Row gutter={16} style={{ marginBottom: 16 }}>
                <Col span={4}><Card><Statistic title="健康度评分" value={overview.health_score || 0} suffix="/100" valueStyle={{ color: (overview.health_score || 0) >= 80 ? '#52c41a' : (overview.health_score || 0) >= 60 ? '#faad14' : '#ff4d4f' }} /></Card></Col>
                <Col span={4}>
                  <Card hoverable onClick={() => openDrill('代码行数分布', { kind: 'loc-breakdown' })}>
                    <Statistic title="代码行数" value={overview.metrics?.total_loc || 0} />
                    <Text type="secondary" style={{ fontSize: 12 }}>点击查看语言/模块分布</Text>
                  </Card>
                </Col>
                <Col span={4}>
                  <Card hoverable onClick={() => openDrill('函数总数明细', { kind: 'functions' })}>
                    <Statistic title="函数总数" value={overview.metrics?.total_functions || 0} />
                    <Text type="secondary" style={{ fontSize: 12 }}>点击查看函数列表</Text>
                  </Card>
                </Col>
                <Col span={4}>
                  <Card hoverable onClick={() => openDrill('文件总数明细', { kind: 'files' })}>
                    <Statistic title="文件总数" value={overview.metrics?.total_files || 0} />
                    <Text type="secondary" style={{ fontSize: 12 }}>点击查看文件列表</Text>
                  </Card>
                </Col>
                <Col span={4}>
                  <Card hoverable onClick={async () => {
                    const items = await ensureComplexity()
                    setDetailModal({
                      visible: true,
                      title: '超大复杂度函数列表',
                      content: (
                        <Table
                          dataSource={items}
                          rowKey={(r) => `${r.file_path}:${r.function_name}`}
                          columns={[
                            { title: '函数', dataIndex: 'function_name', width: 200 },
                            { title: '文件', dataIndex: 'file_path', ellipsis: true },
                            { title: '复杂度', dataIndex: 'cyclomatic_complexity', width: 80, render: (v: number) => <Tag color={(v || 0) > 20 ? 'red' : 'orange'}>{v}</Tag> },
                          ]}
                          pagination={{ pageSize: 10 }}
                        />
                      ),
                    })
                  }}>
                    <Statistic title="超大复杂度函数" value={overview.metrics?.cc_huge_count || 0} valueStyle={{ color: (overview.metrics?.cc_huge_count || 0) > 0 ? '#ff4d4f' : '#52c41a' }} />
                  </Card>
                </Col>
                <Col span={4}>
                  <Card hoverable onClick={async () => {
                    const items = await ensureDuplication()
                    setDetailModal({
                      visible: true,
                      title: '重复代码块列表',
                      content: (
                        <Table
                          dataSource={items}
                          rowKey={(r) => `${r.file_a}:${r.file_b}`}
                          columns={[
                            { title: '文件 A', dataIndex: 'file_a', ellipsis: true },
                            { title: '文件 B', dataIndex: 'file_b', ellipsis: true },
                            { title: '重复行数', dataIndex: 'lines', width: 100 },
                            { title: '代码片段', dataIndex: 'fragment', ellipsis: true, width: 300 },
                          ]}
                          pagination={{ pageSize: 10 }}
                        />
                      ),
                    })
                  }}>
                    <Statistic title="重复率" value={overview.metrics?.dup_ratio || 0} precision={2} suffix="%" valueStyle={{ color: (overview.metrics?.dup_ratio || 0) > 10 ? '#ff4d4f' : '#52c41a' }} />
                  </Card>
                </Col>
              </Row>

              <Row gutter={16}>
                <Col span={12}>
                  <Card title="健康度雷达图" size="small">
                    <ResponsiveContainer width="100%" height={300}>
                      <RadarChart data={radarData}>
                        <PolarGrid />
                        <PolarAngleAxis dataKey="dimension" />
                        <PolarRadiusAxis domain={[0, 100]} />
                        <Radar name="评分" dataKey="score" stroke="#1677ff" fill="#1677ff" fillOpacity={0.6} />
                      </RadarChart>
                    </ResponsiveContainer>
                  </Card>
                </Col>
                <Col span={6}>
                  <Card title="语言分布" size="small" hoverable>
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={languagePieData}
                          dataKey="value"
                          nameKey="name"
                          cx="50%"
                          cy="50%"
                          outerRadius={80}
                          label={(e: any) => e.name}
                          onClick={(e: any) => {
                            if (e && e.name) openDrill(`语言下钻: ${e.name}`, { kind: 'drilldown', language: e.name })
                          }}
                        >
                          {languagePieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                        </Pie>
                        <RechartsTooltip />
                      </PieChart>
                    </ResponsiveContainer>
                  </Card>
                </Col>
                <Col span={6}>
                  <Card title="模块分布" size="small" hoverable>
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={modulePieData}
                          dataKey="value"
                          nameKey="name"
                          cx="50%"
                          cy="50%"
                          outerRadius={80}
                          label={(e: any) => e.name}
                          onClick={(e: any) => {
                            if (e && e.name) openDrill(`模块下钻: ${e.name}`, { kind: 'drilldown', module: e.name })
                          }}
                        >
                          {modulePieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                        </Pie>
                        <RechartsTooltip />
                      </PieChart>
                    </ResponsiveContainer>
                  </Card>
                </Col>
              </Row>

              <Card title="更多指标" size="small" style={{ marginTop: 16 }}>
                <Row gutter={16}>
                  <Col span={4}><Statistic title="平均复杂度" value={overview.metrics?.cc_per_method || 0} precision={1} /></Col>
                  <Col span={4}><Statistic title="最大复杂度" value={overview.metrics?.cc_maximum || 0} /></Col>
                  <Col span={4}><Statistic title="重复块数" value={overview.metrics?.dup_blocks || 0} /></Col>
                  <Col span={4}><Statistic title="Lint 错误" value={overview.metrics?.lint_errors || 0} /></Col>
                  <Col span={4}>
                    <div style={{ cursor: 'pointer' }} onClick={() => {
                      setDetailModal({
                        visible: true,
                        title: 'TODO / FIXME 统计',
                        content: (
                          <Row gutter={16}>
                            <Col span={12}><Statistic title="TODO" value={overview.metrics?.todo_count || 0} /></Col>
                            <Col span={12}><Statistic title="FIXME" value={overview.metrics?.fixme_count || 0} /></Col>
                          </Row>
                        ),
                      })
                    }}>
                      <Statistic title="TODO/FIXME" value={(overview.metrics?.todo_count || 0) + (overview.metrics?.fixme_count || 0)} />
                    </div>
                  </Col>
                </Row>
                <Row gutter={16} style={{ marginTop: 16 }}>
                  <Col span={8}><Statistic title="TODO" value={overview.metrics?.todo_count || 0} /></Col>
                  <Col span={8}><Statistic title="FIXME" value={overview.metrics?.fixme_count || 0} /></Col>
                  <Col span={8}><Statistic title="Lint 错误" value={overview.metrics?.lint_errors || 0} /></Col>
                </Row>
              </Card>

              {derived && (
                <Card title="PR 衍生指标" size="small" style={{ marginTop: 16 }}>
                  <Row gutter={16}>
                    <Col span={4}><Statistic title="PR 总数" value={derived.pr_count} /></Col>
                    <Col span={4}><Statistic title="新增行数" value={derived.total_additions} /></Col>
                    <Col span={4}><Statistic title="删除行数" value={derived.total_deletions} /></Col>
                  </Row>
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col span={12}>
                      <Text strong>PR 大小分布</Text>
                      <div style={{ marginTop: 8 }}>
                        {Object.entries(derived.size_distribution).map(([k, v]) => (
                          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                            <Text>{k}</Text>
                            <Tag>{v}</Tag>
                          </div>
                        ))}
                      </div>
                    </Col>
                    <Col span={12}>
                      <Text strong>修改类型分布</Text>
                      <div style={{ marginTop: 8 }}>
                        {Object.entries(derived.type_distribution).map(([k, v]) => (
                          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                            <Text>{k}</Text>
                            <Tag>{v}</Tag>
                          </div>
                        ))}
                      </div>
                    </Col>
                  </Row>
                </Card>
              )}

              <div style={{ marginTop: 8, textAlign: 'right' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  快照日期: {overview.snapshot_date} | 状态: {overview.collection_status}
                </Text>
              </div>
            </>
          )}
        </Spin>
      ),
    },
    {
      key: 'complexity',
      label: '复杂度',
      children: (
        <Spin spinning={tabLoading}>
          <Table
            dataSource={complexity}
            rowKey={(r) => `${r.file_path}:${r.function_name}`}
            expandable={{
              expandedRowKeys: expandedFile ? [expandedFile] : [],
              onExpand: async (expanded: boolean, record: ComplexityItem) => {
                if (expanded) {
                  setExpandedFile(`${record.file_path}:${record.function_name}`)
                  try {
                    const r = await getFileComplexity(record.file_path)
                    setFileFunctions(r.items)
                  } catch { setFileFunctions([]) }
                } else {
                  setExpandedFile(null)
                }
              },
              expandedRowRender: () => (
                <Table
                  dataSource={fileFunctions}
                  rowKey="function_name"
                  size="small"
                  columns={[
                    { title: '函数', dataIndex: 'function_name', width: 250 },
                    { title: '复杂度', dataIndex: 'cyclomatic_complexity', width: 80 },
                    { title: '嵌套深度', dataIndex: 'max_nesting_depth', width: 80 },
                    { title: '行数', dataIndex: 'function_lines', width: 80 },
                    { title: '起始行', dataIndex: 'start_line', width: 80 },
                  ]}
                  pagination={false}
                />
              ),
            }}
            columns={[
              { title: '函数', dataIndex: 'function_name', width: 200 },
              { title: '文件', dataIndex: 'file_path', width: 300, ellipsis: true },
              { title: '语言', dataIndex: 'language', width: 80, render: (v: string) => v ? <Tag color={v === 'Python' ? 'blue' : 'orange'}>{v}</Tag> : '-' },
              { title: '圈复杂度', dataIndex: 'cyclomatic_complexity', width: 100, sorter: (a: ComplexityItem, b: ComplexityItem) => (a.cyclomatic_complexity || 0) - (b.cyclomatic_complexity || 0), render: (v: number) => <Tag color={(v || 0) > 20 ? 'red' : (v || 0) > 15 ? 'orange' : 'green'}>{v}</Tag> },
              { title: '嵌套深度', dataIndex: 'max_nesting_depth', width: 100 },
              { title: '函数行数', dataIndex: 'function_lines', width: 100 },
            ]}
            pagination={{ pageSize: 20 }}
          />
        </Spin>
      ),
    },
    {
      key: 'duplication',
      label: '重复率',
      children: (
        <Spin spinning={tabLoading}>
          <Table
            dataSource={duplication}
            rowKey={(r) => `${r.file_a}:${r.file_b}`}
            columns={[
              { title: '文件 A', dataIndex: 'file_a', ellipsis: true },
              { title: '文件 B', dataIndex: 'file_b', ellipsis: true },
              { title: '重复行数', dataIndex: 'lines', width: 100, sorter: (a: DuplicationItem, b: DuplicationItem) => a.lines - b.lines },
              { title: '代码片段', dataIndex: 'fragment', ellipsis: true, width: 300 },
            ]}
            pagination={{ pageSize: 20 }}
          />
        </Spin>
      ),
    },
    {
      key: 'heatmap',
      label: '热力图',
      children: (
        <Spin spinning={tabLoading}>
          <Button icon={<SyncOutlined />} loading={syncing} onClick={async () => {
            setSyncing(true)
            try {
              const r = await syncHeatmap(30)
              message.success(`同步完成: ${r.updated} 个文件`)
              const h = await getHeatmap(50)
              setHeatmap(h.items)
            } catch { message.error('同步失败') }
            finally { setSyncing(false) }
          }} style={{ marginBottom: 16 }}>从 PR 数据同步热力图</Button>
          <Table
            dataSource={heatmap}
            rowKey="file_path"
            columns={[
              { title: '文件路径', dataIndex: 'file_path', ellipsis: true },
              { title: '变更次数', dataIndex: 'change_count', width: 100, sorter: (a: HeatmapItem, b: HeatmapItem) => a.change_count - b.change_count, render: (v: number) => <Tag color={v > 20 ? 'red' : v > 10 ? 'orange' : 'blue'}>{v}</Tag> },
              { title: 'Bug 修复次数', dataIndex: 'bug_fix_count', width: 120 },
              { title: '最后变更', dataIndex: 'last_changed', width: 180 },
            ]}
            onRow={(record: HeatmapItem) => ({
              onClick: async () => {
                try {
                  const detail = await getFileHeatmapDetail(record.file_path)
                  setDetailModal({
                    visible: true,
                    title: `文件变更详情: ${record.file_path}`,
                    content: (
                      <div>
                        <Row gutter={16}>
                          <Col span={8}><Statistic title="变更次数" value={detail.change_count || 0} /></Col>
                          <Col span={8}><Statistic title="Bug修复次数" value={detail.bug_fix_count || 0} /></Col>
                          <Col span={8}><Statistic title="最后变更" value={detail.last_changed || 'N/A'} /></Col>
                        </Row>
                      </div>
                    ),
                  })
                } catch { message.error('加载详情失败') }
              }
            })}
            pagination={{ pageSize: 20 }}
          />
        </Spin>
      ),
    },
    {
      key: 'trends',
      label: '趋势',
      children: (
        <Spin spinning={tabLoading}>
          <Select value={period} onChange={setPeriod} options={[
            { label: '近 30 天', value: 30 },
            { label: '近 90 天', value: 90 },
            { label: '近 180 天', value: 180 },
            { label: '近 365 天', value: 365 },
          ]} style={{ marginBottom: 16 }} />
          <Card title="代码规模趋势" size="small" style={{ marginBottom: 16 }}>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={trends} onClick={(e: any) => {
                if (e && e.activePayload && e.activePayload[0]) {
                  const data = e.activePayload[0].payload
                  setDetailModal({
                    visible: true,
                    title: `快照详情: ${data.date}`,
                    content: (
                      <Row gutter={16}>
                        <Col span={6}><Statistic title="代码行数" value={data.total_loc} /></Col>
                        <Col span={6}><Statistic title="函数总数" value={data.total_functions} /></Col>
                        <Col span={6}><Statistic title="健康度" value={data.health_score} /></Col>
                        <Col span={6}><Statistic title="超大复杂度" value={data.cc_huge_count} /></Col>
                        <Col span={6}><Statistic title="重复率" value={data.dup_ratio} suffix="%" /></Col>
                        <Col span={6}><Statistic title="Lint错误" value={data.lint_errors} /></Col>
                        <Col span={6}><Statistic title="TODO/FIXME" value={data.todo_count} /></Col>
                      </Row>
                    ),
                  })
                }
              }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <RechartsTooltip />
                <Legend />
                <Line type="monotone" dataKey="total_loc" name="代码行数" stroke="#1677ff" />
                <Line type="monotone" dataKey="total_functions" name="函数总数" stroke="#52c41a" />
              </LineChart>
            </ResponsiveContainer>
          </Card>
          <Card title="质量趋势" size="small">
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={trends} onClick={(e: any) => {
                if (e && e.activePayload && e.activePayload[0]) {
                  const data = e.activePayload[0].payload
                  setDetailModal({
                    visible: true,
                    title: `快照详情: ${data.date}`,
                    content: (
                      <Row gutter={16}>
                        <Col span={6}><Statistic title="代码行数" value={data.total_loc} /></Col>
                        <Col span={6}><Statistic title="函数总数" value={data.total_functions} /></Col>
                        <Col span={6}><Statistic title="健康度" value={data.health_score} /></Col>
                        <Col span={6}><Statistic title="超大复杂度" value={data.cc_huge_count} /></Col>
                        <Col span={6}><Statistic title="重复率" value={data.dup_ratio} suffix="%" /></Col>
                        <Col span={6}><Statistic title="Lint错误" value={data.lint_errors} /></Col>
                        <Col span={6}><Statistic title="TODO/FIXME" value={data.todo_count} /></Col>
                      </Row>
                    ),
                  })
                }
              }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <RechartsTooltip />
                <Legend />
                <Line type="monotone" dataKey="health_score" name="健康度" stroke="#1677ff" />
                <Line type="monotone" dataKey="cc_per_method" name="平均复杂度" stroke="#faad14" />
                <Line type="monotone" dataKey="dup_ratio" name="重复率" stroke="#ff4d4f" />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Spin>
      ),
    },
    {
      key: 'compare',
      label: '版本对比',
      children: (
        <>
          <Space style={{ marginBottom: 16 }}>
            <Input placeholder="Tag A (如 v0.13.0)" value={tagA} onChange={e => setTagA(e.target.value)} style={{ width: 200 }} />
            <ArrowRightOutlined />
            <Input placeholder="Tag B (如 v0.18.0)" value={tagB} onChange={e => setTagB(e.target.value)} style={{ width: 200 }} />
            <Button type="primary" loading={comparing} disabled={!tagA || !tagB} onClick={async () => {
              setComparing(true)
              try {
                const result = await compareVersions(tagA, tagB)
                setCompareResult(result)
                if (result.error) message.warning(result.error)
              } catch (e) { message.error('对比失败') }
              finally { setComparing(false) }
            }}>对比</Button>
          </Space>
          {compareResult?.a && compareResult?.b ? (
            <Table
              dataSource={Object.keys(compareResult.a).filter(k => k !== 'tag' && k !== 'snapshot_date').map(k => ({
                key: k, metric: k, a: compareResult.a![k], b: compareResult.b![k], delta: compareResult.deltas[k] || 0
              }))}
              columns={[
                { title: '指标', dataIndex: 'metric', width: 200 },
                { title: compareResult.a.tag || 'A', dataIndex: 'a', width: 150 },
                { title: compareResult.b.tag || 'B', dataIndex: 'b', width: 150 },
                { title: '变化', dataIndex: 'delta', width: 100, render: (v: number) => <Tag color={v > 0 ? 'red' : v < 0 ? 'green' : 'default'}>{v > 0 ? '+' : ''}{v}</Tag> },
              ]}
              pagination={false}
            />
          ) : compareResult?.error ? (
            <Empty description={compareResult.error} />
          ) : (
            <Empty description="输入两个 tag 进行对比" />
          )}
        </>
      ),
    },
    {
      key: 'ci-correlation',
      label: 'CI 关联',
      children: (
        <Spin spinning={tabLoading}>
          {ciCorrelation.length === 0 ? (
            <Empty description="暂无关联数据" />
          ) : (
            <>
              <Card title="代码质量 vs CI 成功率" size="small" style={{ marginBottom: 16 }}>
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={ciCorrelation}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis yAxisId="left" />
                    <YAxis yAxisId="right" orientation="right" />
                    <RechartsTooltip />
                    <Legend />
                    <Line yAxisId="left" type="monotone" dataKey="health_score" name="健康度" stroke="#1677ff" />
                    <Line yAxisId="right" type="monotone" dataKey="ci_success_rate" name="CI 成功率(%)" stroke="#52c41a" />
                  </LineChart>
                </ResponsiveContainer>
              </Card>
              <Card title="超大复杂度函数 vs CI 成功率" size="small">
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={ciCorrelation}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="date" />
                    <YAxis yAxisId="left" />
                    <YAxis yAxisId="right" orientation="right" />
                    <RechartsTooltip />
                    <Legend />
                    <Line yAxisId="left" type="monotone" dataKey="cc_huge_count" name="超大复杂度函数数" stroke="#ff4d4f" />
                    <Line yAxisId="right" type="monotone" dataKey="ci_success_rate" name="CI 成功率(%)" stroke="#52c41a" />
                  </LineChart>
                </ResponsiveContainer>
              </Card>
            </>
          )}
        </Spin>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>
            <CodeOutlined style={{ marginRight: 8 }} />
            代码度量看板
          </Title>
          <Text type="secondary">vllm-ascend 仓库代码质量量化度量 — 圈复杂度 / 重复率 / 技术债务</Text>
        </div>
        <Space>
          <Button icon={<ThunderboltOutlined />} loading={triggering} onClick={async () => {
            setTriggering(true)
            try {
              const r = await triggerCollection('main')
              if (r.status === 'triggered') message.success('采集任务已触发')
              else message.warning(r.message || '触发失败')
            } catch { message.error('触发失败') }
            finally { setTriggering(false) }
          }}>手动采集</Button>
          <Button icon={<DownloadOutlined />} onClick={async () => {
            try {
              const blob = await exportMetrics('csv', period)
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = 'code_metrics.csv'
              a.click()
              URL.revokeObjectURL(url)
              message.success('导出成功')
            } catch { message.error('导出失败') }
          }}>导出 CSV</Button>
        </Space>
      </div>
      {alerts.length > 0 && (
        <Alert
          message={`代码度量告警 (${alerts.length})`}
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {alerts.map((a, i) => (
                <li key={i}>
                  <Tag color={a.level === 'error' ? 'red' : a.level === 'warning' ? 'orange' : 'blue'}>{a.level}</Tag>
                  {a.message}
                </li>
              ))}
            </ul>
          }
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      <Modal
        open={detailModal.visible}
        title={detailModal.title}
        onCancel={() => setDetailModal({ ...detailModal, visible: false })}
        footer={null}
        width={800}
      >
        {detailModal.content}
      </Modal>
      <DrillModal
        state={drillState}
        onClose={() => setDrillState((s) => ({ ...s, open: false }))}
        languageLoc={overview?.language_loc || {}}
        moduleLoc={overview?.module_loc || {}}
      />
    </div>
  )
}

export default CodeMetricsBoard
