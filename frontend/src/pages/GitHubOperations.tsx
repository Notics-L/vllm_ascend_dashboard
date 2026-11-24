import { useState } from 'react'
import { Alert, AutoComplete, Button, DatePicker, Descriptions, Form, Input, Select, Space, Tag } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useQuery } from '@tanstack/react-query'
import type { Dayjs } from 'dayjs'
import DailyCasePage from './daily-pipeline/cases/DailyCasePage'
import { dailyTableRefresh } from './daily-pipeline/shared/dailyTableRefresh'
import { getGitHubOperations } from '../services/githubOperations'
import type { GitHubCredentialSource, GitHubOperation, GitHubOperationFilters } from '../services/githubOperations'

const timeFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const results = {
  success: { label: '成功', color: 'green' },
  redirect: { label: '重定向', color: 'blue' },
  failed: { label: '失败', color: 'red' },
}
const sources: Record<string, string> = {
  github_client: '通用 GitHub 客户端',
  github_api: 'GitHub REST',
  system_config: '系统配置',
  workflow_actions: 'Actions 操作',
  ci: 'CI 运行',
  ci_sync: 'CI 同步',
  pr_pipeline: 'PR 流水线',
  pr_sync: 'PR 同步',
  pr_historical_sync: 'PR 历史同步',
  contributor_board: '贡献者看板',
  project_dashboard: '项目看板',
  resource_dashboard: '资源看板',
  resource_metrics: '资源指标',
  npu_occupancy: 'NPU 占用',
  release_management: '发布件管理',
  image_build_monitor: '镜像构建监控',
  models: '模型管理',
  model_sync: '模型同步',
  model_sync_configs: '模型同步配置',
  test_board: '测试看板',
  test_board_sync: '测试看板同步',
  coverage_sync: '覆盖率同步',
  code_metrics: '代码度量',
  code_heatmap_sync: '代码热力图同步',
  nightly_data_sync: 'Nightly 数据同步',
  daily_report: '运行日报',
  daily_summary: '每日总结',
  notifications: '通知服务',
  daily_config: 'Daily 配置与触发',
  daily_collection: 'Daily 采集',
  daily_revert: 'Daily 回退',
  failure_analysis: 'CI 失败分析',
  agent: 'AI 问题定位',
}
const processes: Record<string, string> = { api: 'API', scheduler: '调度器', collector: '采集器' }
const credentialSources: Record<GitHubCredentialSource, string> = {
  global: '全局 Token',
  daily: 'Daily 专用 Token',
  actions: 'Actions 专用 Token',
}
const credentialOptions = Object.entries(credentialSources).map(([value, label]) => ({ value, label }))
const sourceOptions = Object.entries(sources).map(([value, label]) => ({ value, label }))
const errors: Record<string, string> = {
  timeout: '请求超时', network: '网络错误', git: 'Git 命令失败', cancelled: '调用已取消',
}
const operationOptions = [{ value: 'read', label: '读取' }, { value: 'write', label: '写入' }]

const runActions: Record<string, string> = {
  rerun: '重跑工作流',
  'rerun-failed-jobs': '重跑失败任务',
  cancel: '取消工作流',
  'force-cancel': '强制取消工作流',
}

function describeOperation({ method, endpoint }: GitHubOperation) {
  if (method === 'PUSH') return '推送 Git 分支'
  if (method === 'FETCH') return '拉取 Git 引用'
  if (method === 'LS-REMOTE') return '查询 Git 远程引用'
  if (endpoint === '/rate_limit') return '查询 GitHub API 配额'
  if (endpoint === '/user') return '查询 Token 对应身份'

  const path = endpoint.replace(/^\/repos\/[^/]+\/[^/]+/, '')
  const dispatch = /^\/actions\/workflows\/([^/]+)\/dispatches$/.exec(path)
  if (method === 'POST' && dispatch) return `触发工作流 ${dispatch[1]}`
  const run = /^\/actions\/runs\/(\d+)(?:\/([^/]+))?$/.exec(path)
  if (method === 'POST' && run && runActions[run[2]]) return `${runActions[run[2]]} #${run[1]}`
  if (method === 'POST' && path === '/issues') return '创建 Issue'
  if (method === 'POST' && path === '/pulls') return '创建 PR'
  const issue = /^\/issues\/(\d+)$/.exec(path)
  if (method === 'PATCH' && issue) return `更新 Issue #${issue[1]}`
  if (method === 'GET' && path.startsWith('/actions/workflows')) return '查询工作流'
  if (method === 'GET' && path.endsWith('/logs')) return '获取 Actions 日志'
  if (method === 'GET' && run) return `查询工作流运行 #${run[1]}`
  return `${method} ${endpoint}`
}

const columns: ProColumns<GitHubOperation>[] = [
  { title: '操作时间（北京时间）', dataIndex: 'occurred_at', width: 175, render: (_, row) => timeFormat.format(new Date(row.occurred_at)) },
  { title: 'Token 来源', dataIndex: 'credential_source', width: 150, render: (_, row) => row.credential_source ? credentialSources[row.credential_source] : '—' },
  { title: '模块', dataIndex: 'source', width: 140, render: (_, row) => sources[row.source] ?? row.source },
  { title: '操作人', dataIndex: 'actor', width: 115, ellipsis: true, render: (_, row) => row.actor ?? (processes[row.process] || row.process) },
  { title: '操作', key: 'action', ellipsis: true, render: (_, row) => describeOperation(row) },
  { title: '仓库', dataIndex: 'repository', width: 180, ellipsis: true },
  {
    title: '结果', dataIndex: 'result', width: 122,
    render: (_, row) => <Tag color={results[row.result].color}>
      {results[row.result].label}{row.http_status ? ` · ${row.http_status}` : ''}
    </Tag>,
  },
]

type FilterFields = Omit<GitHubOperationFilters, 'start' | 'end'> & { dates?: [Dayjs, Dayjs] }

function OperationDetails({ row }: { row: GitHubOperation }) {
  return <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={[
    { key: 'process', label: '服务进程', children: processes[row.process] ?? row.process },
    { key: 'actor', label: '看板用户', children: row.actor ?? '—' },
    { key: 'id', label: '记录 ID', children: row.id },
    { key: 'method', label: '请求方法', children: row.method },
    { key: 'duration', label: '耗时', children: `${row.duration_ms} ms` },
    { key: 'requestId', label: 'GitHub 请求 ID', children: row.github_request_id ?? '—' },
    { key: 'error', label: '错误类型', children: row.error_type ? errors[row.error_type] ?? row.error_type : '—' },
    { key: 'endpoint', label: '目标', span: { xs: 1, sm: 2, lg: 3 }, children: <code style={{ overflowWrap: 'anywhere' }}>{row.endpoint}</code> },
  ]} />
}

export default function GitHubOperations() {
  const [form] = Form.useForm<FilterFields>()
  const [filters, setFilters] = useState<GitHubOperationFilters>({})
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const history = useQuery({
    queryKey: ['github-operations', page, pageSize, filters],
    queryFn: () => getGitHubOperations(page, pageSize, filters),
  })

  function applyFilters(values: FilterFields) {
    const { dates, repository, source, actor, ...fields } = values
    setFilters({
      ...fields,
      repository: repository?.trim() || undefined,
      source: source?.trim() || undefined,
      actor: actor?.trim() || undefined,
      start: dates && `${dates[0].format('YYYY-MM-DD')}T00:00:00+08:00`,
      end: dates && `${dates[1].add(1, 'day').format('YYYY-MM-DD')}T00:00:00+08:00`,
    })
    setPage(1)
  }

  return <DailyCasePage title="GitHub 操作记录">
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Form form={form} layout="inline" onFinish={applyFilters} style={{ rowGap: 8 }}>
        <Form.Item name="credential_source" label="Token 来源">
          <Select options={credentialOptions} placeholder="全部" style={{ width: 156 }} allowClear />
        </Form.Item>
        <Form.Item name="source" label="模块">
          <AutoComplete
            options={sourceOptions}
            filterOption={(value, option) => Boolean(option && (option.label.includes(value) || option.value.includes(value.toLowerCase())))}
            placeholder="全部"
            style={{ width: 136 }}
            maxLength={40}
            allowClear
          />
        </Form.Item>
        <Form.Item name="actor" label="操作人">
          <Input placeholder="看板用户名" maxLength={50} style={{ width: 130 }} allowClear />
        </Form.Item>
        <Form.Item name="repository" label="仓库">
          <Input placeholder="owner/repo" maxLength={140} allowClear />
        </Form.Item>
        <Form.Item name="operation_type" label="类型">
          <Select options={operationOptions} placeholder="全部" style={{ width: 90 }} allowClear />
        </Form.Item>
        <Form.Item name="result" label="结果">
          <Select
            options={Object.entries(results).map(([value, { label }]) => ({ value, label }))}
            placeholder="全部" style={{ width: 105 }} allowClear
          />
        </Form.Item>
        <Form.Item name="dates" label="操作日期">
          <DatePicker.RangePicker />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">查询</Button>
          <Button onClick={() => { form.resetFields(); applyFilters({}) }}>重置</Button>
        </Space>
      </Form>
      {history.isError && <Alert type="error" showIcon message="GitHub 操作记录加载失败，请刷新重试" />}
      <ProTable<GitHubOperation>
        rowKey="id"
        size="small"
        columns={columns}
        search={false}
        toolBarRender={false}
        cardProps={false}
        scroll={{ x: 1180 }}
        dataSource={history.data?.items ?? []}
        locale={{ emptyText: history.isError ? '暂时无法加载记录' : '暂无操作记录' }}
        expandable={{ expandedRowRender: (row) => <OperationDetails row={row} />, expandRowByClick: true }}
        {...dailyTableRefresh(history.refetch, history.isFetching, {
          current: page, pageSize, total: history.data?.total ?? 0, showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          onChange: (current, size) => { setPage(size === pageSize ? current : 1); setPageSize(size) },
        })}
      />
    </Space>
  </DailyCasePage>
}
