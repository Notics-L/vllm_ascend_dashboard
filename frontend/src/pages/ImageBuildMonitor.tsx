import { useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  TimePicker,
  Tooltip,
  Typography,
  message,
  type TableColumnsType,
} from 'antd'
import {
  BellOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  LinkOutlined,
  MailOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import {
  ackAlert,
  createTarget,
  deleteTarget,
  getOverview,
  getGithubStatus,
  getTargetRuns,
  listAlerts,
  listCandidates,
  listTargets,
  renotifyAlert,
  resolveAlert,
  runCheck,
  sendTestEmail,
  updateTarget,
  type MonitorAlert,
  type MonitorTarget,
  type TargetPayload,
} from '../services/imageBuildMonitor'

const { Text } = Typography

const ALERT_TYPE_ZH: Record<string, string> = {
  failure: '构建失败',
  missing: '定时未触发',
  stuck: '构建卡住',
}
const ALERT_TYPE_COLOR: Record<string, string> = {
  failure: 'red',
  missing: 'magenta',
  stuck: 'orange',
}
const STATUS_ZH: Record<string, string> = {
  unresolved: '未恢复',
  open: '未处理',
  acknowledged: '已确认',
  resolved: '已恢复',
}
const STATUS_COLOR: Record<string, string> = {
  unresolved: 'orange',
  open: 'red',
  acknowledged: 'gold',
  resolved: 'green',
}
const RUN_STATUS_COLOR: Record<string, string> = {
  success: 'green',
  failure: 'red',
  cancelled: 'default',
  timed_out: 'volcano',
  startup_failure: 'volcano',
  in_progress: 'processing',
  queued: 'blue',
  waiting: 'blue',
  requested: 'blue',
  pending: 'blue',
  skipped: 'default',
  neutral: 'default',
  action_required: 'orange',
}

const RUN_STATUS_ZH: Record<string, string> = {
  success: '成功',
  failure: '失败',
  cancelled: '已取消',
  timed_out: '超时',
  startup_failure: '启动失败',
  in_progress: '运行中',
  queued: '排队中',
  waiting: '等待中',
  requested: '请求中',
  pending: '待处理',
  completed: '已完成',
  skipped: '已跳过',
  neutral: '中性',
  action_required: '需处理',
}

function fmtTime(value: string | null | undefined) {
  if (!value) return '-'
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

function fmtDuration(seconds: number | null | undefined) {
  if (!seconds) return '-'
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h${m % 60}m`
}

const WEEKDAY_OPTIONS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 0, label: '周日' },
]

function buildCron(values: {
  frequency: string
  minute?: number
  time?: dayjs.Dayjs
  weekdays?: number[]
  day?: number
  custom?: string
}): string {
  const minute = values.minute ?? 0
  const hour = values.time?.hour() ?? 2
  const min = values.time?.minute() ?? 0
  switch (values.frequency) {
    case 'hourly':
      return `${minute} * * * *`
    case 'weekly':
      return `${min} ${hour} * * ${(values.weekdays?.length ? values.weekdays : [1]).join(',')}`
    case 'monthly':
      return `${min} ${hour} ${values.day ?? 1} * *`
    case 'custom':
      return (values.custom || '').trim()
    case 'daily':
    default:
      return `${min} ${hour} * * *`
  }
}

function describeCron(values: {
  frequency: string
  minute?: number
  time?: dayjs.Dayjs
  weekdays?: number[]
  day?: number
  custom?: string
}): string {
  const time = values.time ? values.time.format('HH:mm') : '02:00'
  switch (values.frequency) {
    case 'hourly':
      return `每小时第 ${values.minute ?? 0} 分钟执行`
    case 'weekly':
      return `每周 ${(values.weekdays?.length ? values.weekdays : [1]).join('、')} 的 ${time} 执行`
    case 'monthly':
      return `每月 ${values.day ?? 1} 日 ${time} 执行`
    case 'custom':
      return '自定义 5 段 cron 表达式'
    case 'daily':
    default:
      return `每天 ${time} 执行`
  }
}

export default function ImageBuildMonitor() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [days, setDays] = useState(7)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<MonitorTarget | null>(null)
  const [runTarget, setRunTarget] = useState<MonitorTarget | null>(null)
  const [alertStatus, setAlertStatus] = useState<string | undefined>('unresolved')
  const [alertType, setAlertType] = useState<string | undefined>()
  const [cronOpen, setCronOpen] = useState(false)
  const [emailOpen, setEmailOpen] = useState(false)
  const [form] = Form.useForm()
  const [cronForm] = Form.useForm()
  const [emailForm] = Form.useForm()
  const cronFrequency = Form.useWatch('frequency', cronForm)
  const cronValues = Form.useWatch([], cronForm)

  const overviewQuery = useQuery({
    queryKey: ['image-build-monitor-overview', days],
    queryFn: () => getOverview(days),
  })
  const githubStatusQuery = useQuery({
    queryKey: ['image-build-monitor-github-status'],
    queryFn: getGithubStatus,
    staleTime: 60_000,
  })
  const targetsQuery = useQuery({
    queryKey: ['image-build-monitor-targets'],
    queryFn: listTargets,
  })
  const candidatesQuery = useQuery({
    queryKey: ['image-build-monitor-candidates'],
    queryFn: listCandidates,
  })
  const alertsQuery = useQuery({
    queryKey: ['image-build-monitor-alerts', alertStatus, alertType, days],
    queryFn: () =>
      listAlerts({
        status: alertStatus,
        alert_type: alertType,
        page: 1,
        page_size: 50,
      }),
  })
  const runsQuery = useQuery({
    queryKey: ['image-build-monitor-runs', runTarget?.id],
    queryFn: () => getTargetRuns(runTarget!.id, 20),
    enabled: !!runTarget,
  })

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ['image-build-monitor-overview'] })
    queryClient.invalidateQueries({ queryKey: ['image-build-monitor-targets'] })
    queryClient.invalidateQueries({ queryKey: ['image-build-monitor-candidates'] })
    queryClient.invalidateQueries({ queryKey: ['image-build-monitor-alerts'] })
    queryClient.invalidateQueries({ queryKey: ['image-build-monitor-github-status'] })
  }

  const saveMutation = useMutation({
    mutationFn: async (values: TargetPayload & { id?: number }) => {
      const payload: TargetPayload = { ...values }
      delete (payload as { id?: number }).id
      if (editing) await updateTarget(editing.id, payload)
      else await createTarget(payload)
    },
    onSuccess: () => {
      message.success(editing ? '监控目标已更新' : '监控目标已创建')
      setModalOpen(false)
      setEditing(null)
      form.resetFields()
      refreshAll()
    },
    onError: (e: Error) => message.error(`保存失败：${e.message}`),
  })

  const checkMutation = useMutation({
    mutationFn: runCheck,
    onSuccess: (res) => {
      if (res.targets === 0) {
        message.warning('当前没有启用的监控目标，请先点击「新增监控」配置目标')
      } else {
        message.success(
          `检测完成：同步 ${res.synced_runs ?? 0} 条 GitHub 运行，新增 ${res.created} 条告警，通知 ${res.notified} 条，恢复 ${res.resolved} 条`,
        )
      }
      if (res.errors?.length) {
        message.warning(`部分目标同步失败：${res.errors.join('；')}`)
      }
      refreshAll()
    },
    onError: (e: Error) => message.error(`检测失败：${e.message}`),
  })

  const testEmailMutation = useMutation({
    mutationFn: sendTestEmail,
    onSuccess: (res) => {
      if (res.success) {
        message.success(`测试邮件已发送至 ${res.recipients.join(', ')}`)
        setEmailOpen(false)
        emailForm.resetFields()
      } else {
        message.error(`邮件发送失败：${res.error || '请检查 SMTP 配置'}`)
      }
    },
    onError: (e: Error) => message.error(`邮件发送失败：${e.message}`),
  })

  const actionMutation = useMutation({
    mutationFn: async ({ action, id }: { action: 'ack' | 'resolve' | 'renotify'; id: number }) => {
      if (action === 'ack') await ackAlert(id)
      else if (action === 'resolve') await resolveAlert(id)
      else await renotifyAlert(id)
    },
    onSuccess: (_, vars) => {
      message.success(
        vars.action === 'ack' ? '已标记为已知晓，告警仍保留在未恢复列表' : vars.action === 'resolve' ? '已标记恢复' : '已重新发送通知',
      )
      refreshAll()
    },
    onError: (e: Error) => message.error(`操作失败：${e.message}`),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteTarget,
    onSuccess: () => {
      message.success('监控目标已删除')
      refreshAll()
    },
    onError: (e: Error) => message.error(`删除失败：${e.message}`),
  })

  const overview = overviewQuery.data
  const targets = targetsQuery.data ?? []
  const alerts = alertsQuery.data?.items ?? []

  const candidateOptions = useMemo(() => {
    const items = candidatesQuery.data ?? []
    return items
      .filter((c) => !c.already_monitored || c.id === editing?.release_workflow_id)
      .map((c) => ({
        value: c.id,
        label: `${c.repo_owner}/${c.repo_name} · ${c.workflow_name}`,
      }))
  }, [candidatesQuery.data, editing])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      enabled: true,
      timezone: 'Asia/Shanghai',
      grace_minutes: 30,
      lookback_days: 2,
      notify_emails: [],
      notify_on_failure: true,
      notify_on_missing: true,
      notify_on_stuck: true,
      stuck_threshold_minutes: 180,
      cooldown_minutes: 60,
    })
    setModalOpen(true)
  }

  const openEdit = (row: MonitorTarget) => {
    setEditing(row)
    form.setFieldsValue({
      release_workflow_id: row.release_workflow_id,
      enabled: row.enabled,
      schedule_cron: row.schedule_cron,
      timezone: row.timezone,
      grace_minutes: row.grace_minutes,
      lookback_days: row.lookback_days,
      notify_emails: row.notify_emails,
      notify_on_failure: row.notify_on_failure,
      notify_on_missing: row.notify_on_missing,
      notify_on_stuck: row.notify_on_stuck,
      stuck_threshold_minutes: row.stuck_threshold_minutes,
      cooldown_minutes: row.cooldown_minutes,
    })
    setModalOpen(true)
  }

  const openCronBuilder = () => {
    const current = (form.getFieldValue('schedule_cron') as string | undefined) || ''
    cronForm.setFieldsValue({
      frequency: current ? 'custom' : 'daily',
      minute: 0,
      time: dayjs().hour(2).minute(0).second(0),
      weekdays: [1, 2, 3, 4, 5],
      day: 1,
      custom: current,
    })
    setCronOpen(true)
  }

  const applyCron = async () => {
    const values = await cronForm.validateFields()
    const cron = buildCron(values)
    if (!cron) {
      message.warning('请填写 cron 表达式')
      return
    }
    form.setFieldValue('schedule_cron', cron)
    setCronOpen(false)
  }

  const openEmailTest = () => {
    const firstTarget = targets[0]
    emailForm.setFieldsValue({
      recipients: firstTarget?.notify_emails ?? [],
      target_id: firstTarget?.id,
    })
    setEmailOpen(true)
  }

  const targetColumns: TableColumnsType<MonitorTarget> = [
    {
      title: 'Workflow',
      key: 'workflow',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 600 }}>{r.workflow_name}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.workflow_path}</Text>
        </div>
      ),
    },
    {
      title: '仓库',
      key: 'repo',
      render: (_, r) => `${r.repo_owner}/${r.repo_name}`,
    },
    {
      title: '期望调度',
      key: 'cron',
      render: (_, r) => (
        <div>
          <Tag color={r.schedule_cron ? 'blue' : 'default'}>{r.schedule_cron || '未配置'}</Tag>
          <div style={{ fontSize: 12, color: '#999' }}>容忍 {r.grace_minutes} 分钟 · {r.timezone}</div>
        </div>
      ),
    },
    {
      title: '收件人',
      dataIndex: 'notify_emails',
      render: (v: string[]) =>
        v?.length ? (
          <Tooltip title={v.join(', ')}>
            <Tag icon={<MailOutlined />}>{v.length} 人</Tag>
          </Tooltip>
        ) : (
          <Text type="secondary">未配置</Text>
        ),
    },
    {
      title: '最近运行',
      key: 'latest',
      render: (_, r) => {
        if (!r.latest_run) return <Text type="secondary">暂无运行</Text>
        return (
          <div>
            <Tag color={RUN_STATUS_COLOR[r.latest_run.status || ''] || 'default'}>
              {RUN_STATUS_ZH[r.latest_run.status || ''] ?? r.latest_run.status ?? '-'}
            </Tag>
            <div style={{ fontSize: 12, color: '#999' }}>
              {fmtTime(r.latest_run.created_at)} · {fmtDuration(r.latest_run.wall_duration)}
            </div>
          </div>
        )
      },
    },
    {
      title: '未恢复告警',
      dataIndex: 'open_alert_count',
      render: (v: number) => <Tag color={v > 0 ? 'red' : 'green'}>{v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      render: (v: boolean, r) => (
        <Tooltip
          title={
            r.last_check_error
              ? `最近检测异常：${r.last_check_error}`
              : `最近成功：${fmtTime(r.last_check_success_at)}`
          }
        >
          <Tag color={r.last_check_error ? 'orange' : v ? 'green' : 'default'}>
            {r.last_check_error ? '检测异常' : v ? '监控中' : '已停用'}
          </Tag>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, r) => (
        <Space size={4}>
          <Button size="small" type="link" icon={<HistoryOutlined />} onClick={() => setRunTarget(r)}>
            运行
          </Button>
          <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(r)}>
            编辑
          </Button>
          <Popconfirm
            title="删除后历史告警也会一并删除，确认？"
            onConfirm={() => deleteMutation.mutate(r.id)}
          >
            <Button size="small" type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const alertColumns: TableColumnsType<MonitorAlert> = [
    {
      title: '类型',
      dataIndex: 'alert_type',
      width: 110,
      render: (v: string) => <Tag color={ALERT_TYPE_COLOR[v] || 'default'}>{ALERT_TYPE_ZH[v] || v}</Tag>,
    },
    {
      title: '级别',
      dataIndex: 'severity',
      width: 80,
      render: (v: string) => <Tag color={v === 'critical' ? 'red' : 'gold'}>{v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_ZH[v] || v}</Tag>,
    },
    {
      title: 'Workflow',
      key: 'workflow',
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 600 }}>{r.workflow_name}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.repo_owner}/{r.repo_name}</Text>
        </div>
      ),
    },
    {
      title: '告警内容',
      key: 'title',
      render: (_, r) => (
        <div>
          <div>{r.title}</div>
          <Text type="secondary" style={{ fontSize: 12 }}>{r.message}</Text>
        </div>
      ),
    },
    {
      title: '首次发现',
      dataIndex: 'first_detected_at',
      width: 150,
      render: (v: string) => fmtTime(v),
    },
    {
      title: '通知',
      key: 'notification',
      width: 100,
      render: (_, r) => {
        if (!r.last_notified_at) return <Text type="secondary">未发送</Text>
        return r.notification_success ? (
          <Tooltip title={fmtTime(r.last_notified_at)}>
            <Tag color="green">已发送</Tag>
          </Tooltip>
        ) : (
          <Tooltip title={r.notification_error || '发送失败'}>
            <Tag color="red">失败</Tag>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_, r) => (
        <Space size={2}>
          {r.run_url && (
            <Button size="small" type="link" icon={<LinkOutlined />} href={r.run_url} target="_blank">
              Run
            </Button>
          )}
          {r.status === 'open' && (
            <Tooltip title="标记为已知晓：停止重复邮件提醒，但告警仍保留在列表，需处理完后点击「恢复」">
              <Button
                size="small"
                type="link"
                onClick={() => actionMutation.mutate({ action: 'ack', id: r.id })}
              >
                已知晓
              </Button>
            </Tooltip>
          )}
          {r.status !== 'resolved' && (
            <Button
              size="small"
              type="link"
              onClick={() => actionMutation.mutate({ action: 'resolve', id: r.id })}
            >
              恢复
            </Button>
          )}
          <Button
            size="small"
            type="link"
            icon={<BellOutlined />}
            onClick={() => actionMutation.mutate({ action: 'renotify', id: r.id })}
          >
            重发
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>镜像构建监控</Typography.Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            监控定时镜像构建的失败、未触发与长时间卡住，并邮件通知责任人
          </Text>
          <div style={{ fontSize: 12, color: '#999', marginTop: 2 }}>
            最近检测成功：{fmtTime(overview?.monitor_health?.last_success_at)}
            {overview?.monitor_health?.last_checked_at
              ? ` · 最近检测：${fmtTime(overview.monitor_health.last_checked_at)}`
              : ''}
          </div>
        </div>
        <Space>
          <Select
            value={days}
            style={{ width: 120 }}
            onChange={setDays}
            options={[
              { value: 1, label: '近 1 天' },
              { value: 7, label: '近 7 天' },
              { value: 14, label: '近 14 天' },
              { value: 30, label: '近 30 天' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={refreshAll}>刷新</Button>
          <Button
            icon={<PlayCircleOutlined />}
            loading={checkMutation.isPending}
            onClick={() => checkMutation.mutate()}
          >
            立即检测
          </Button>
          <Button icon={<MailOutlined />} onClick={openEmailTest}>
            邮件测试
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增监控
          </Button>
        </Space>
      </div>

      {(overview?.monitor_health?.targets_with_error ?? 0) > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`${overview?.monitor_health?.targets_with_error} 个监控目标最近检测异常`}
          description="请查看目标列表并检查 GitHub Token、网络或 SMTP 配置；检测任务本身仍会继续运行。"
        />
      )}

      {githubStatusQuery.data && !githubStatusQuery.data.ok && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`GitHub 数据源不可用（${githubStatusQuery.data.mode}）`}
          description={`${githubStatusQuery.data.message}。请到「管理 → 系统配置 → 编辑 GitHub」更新有效 Token 后重试；否则「立即检测」只能基于本地已采集的历史数据。`}
        />
      )}

      {overview && overview.unresolved_alerts > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={`当前有 ${overview.unresolved_alerts} 条未恢复镜像构建告警`}
          description="「已知晓」仅停止重复邮件提醒，告警仍会保留在列表；处理完成后请点击「恢复」关闭告警。"
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title="监控目标" value={overview?.targets_total ?? 0} suffix={`/ ${overview?.targets_enabled ?? 0} 启用`} /></Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title={`近 ${days} 天运行`} value={overview?.runs_total ?? 0} /></Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title="失败运行" value={overview?.failed_runs ?? 0} valueStyle={{ color: (overview?.failed_runs ?? 0) > 0 ? '#cf1322' : '#3f8600' }} /></Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title="定时未触发" value={overview?.by_type?.missing ?? 0} valueStyle={{ color: '#c41d7f' }} /></Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title="构建卡住" value={overview?.by_type?.stuck ?? 0} valueStyle={{ color: '#d46b08' }} /></Card>
        </Col>
        <Col xs={12} md={8} lg={4}>
          <Card size="small"><Statistic title="未恢复告警" value={overview?.unresolved_alerts ?? 0} valueStyle={{ color: (overview?.unresolved_alerts ?? 0) > 0 ? '#cf1322' : '#3f8600' }} /></Card>
        </Col>
      </Row>

      <Card
        size="small"
        title="监控目标"
        style={{ marginBottom: 20 }}
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={() => targetsQuery.refetch()}>刷新</Button>}
      >
        <Table
          rowKey="id"
          size="small"
          loading={targetsQuery.isLoading}
          columns={targetColumns}
          dataSource={targets}
          pagination={false}
          scroll={{ x: 1100 }}
        />
        {!targetsQuery.isLoading && targets.length === 0 && (
          <Alert
            style={{ marginTop: 12 }}
            type="info"
            showIcon
            message="尚未配置监控目标"
            description="候选 Workflow 来自「发布件管理」已注册的 Release Workflow。请先在发布件管理中同步目标仓库的 Workflow，再回到本页新增监控。"
          />
        )}
      </Card>

      <Card
        size="small"
        title={`告警列表（${alertsQuery.data?.total ?? 0}）`}
        extra={
          <Space>
            <Select
              allowClear
              placeholder="告警类型"
              style={{ width: 140 }}
              value={alertType}
              onChange={setAlertType}
              options={Object.entries(ALERT_TYPE_ZH).map(([value, label]) => ({ value, label }))}
            />
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 120 }}
              value={alertStatus}
              onChange={setAlertStatus}
              options={Object.entries(STATUS_ZH).map(([value, label]) => ({ value, label }))}
            />
            <Button size="small" icon={<ReloadOutlined />} onClick={() => alertsQuery.refetch()}>刷新</Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={alertsQuery.isLoading}
          columns={alertColumns}
          dataSource={alerts}
          pagination={false}
          scroll={{ x: 1200 }}
        />
      </Card>

      <Modal
        title={editing ? '编辑监控目标' : '新增监控目标'}
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
        }}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        width={720}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => saveMutation.mutate(values)}
        >
          <Form.Item
            name="release_workflow_id"
            label="目标 Workflow"
            rules={[{ required: true, message: '请选择要监控的 Workflow' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={candidateOptions}
              placeholder="选择已注册的发布件 Workflow"
            />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="schedule_cron"
                label="期望调度 Cron"
                tooltip="5 段 cron，例如 0 2 * * *；留空则不做未触发检测"
              >
                <Input
                  placeholder="0 2 * * *"
                  addonAfter={
                    <Button type="link" size="small" style={{ padding: 0 }} onClick={openCronBuilder}>
                      可视化
                    </Button>
                  }
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="timezone" label="时区">
                <Select
                  options={[
                    { value: 'Asia/Shanghai', label: 'Asia/Shanghai' },
                    { value: 'UTC', label: 'UTC' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="grace_minutes" label="调度容忍窗口（分钟）">
                <InputNumber min={0} max={1440} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="lookback_days" label="回溯天数">
                <InputNumber min={1} max={30} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="stuck_threshold_minutes" label="卡住阈值（分钟）">
                <InputNumber min={5} max={2880} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="notify_emails"
            label="通知邮箱"
          >
            <Select mode="tags" tokenSeparators={[',', ';', ' ']} placeholder="输入邮箱后回车，可添加多个" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="notify_on_failure" label="失败通知" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="notify_on_missing" label="未触发通知" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="notify_on_stuck" label="卡住通知" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="cooldown_minutes"
                label="重复提醒间隔（分钟）"
                tooltip="同一条未恢复告警超过该时间后会再次邮件提醒"
              >
                <InputNumber min={5} max={10080} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="enabled" label="启用监控" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Drawer
        title={
          <span style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {runTarget ? `${runTarget.workflow_name} · 最近运行` : '最近运行'}
          </span>
        }
        open={!!runTarget}
        onClose={() => setRunTarget(null)}
        width={920}
      >
        {runTarget && (
          <Descriptions
            size="small"
            column={2}
            bordered
            style={{ marginBottom: 16 }}
            labelStyle={{ width: 96, whiteSpace: 'nowrap', background: '#fafafa' }}
            contentStyle={{ whiteSpace: 'nowrap' }}
          >
            <Descriptions.Item label="仓库">
              <Tag color="blue">{runTarget.repo_owner}/{runTarget.repo_name}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="期望调度">
              <Tag color={runTarget.schedule_cron ? 'geekblue' : 'default'}>
                {runTarget.schedule_cron || '未配置'}
              </Tag>
              <Text type="secondary" style={{ marginLeft: 8 }}>{runTarget.timezone}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="最近检测">{fmtTime(runTarget.last_checked_at)}</Descriptions.Item>
            <Descriptions.Item label="未恢复告警">
              <Tag color={runTarget.open_alert_count > 0 ? 'red' : 'green'}>{runTarget.open_alert_count}</Tag>
            </Descriptions.Item>
          </Descriptions>
        )}
        <Table
          rowKey="id"
          size="small"
          loading={runsQuery.isLoading}
          dataSource={runsQuery.data ?? []}
          pagination={false}
          scroll={{ x: 860 }}
          columns={[
            {
              title: 'Run',
              key: 'run',
              width: 190,
              render: (_, r) => (
                <Space size={4} style={{ whiteSpace: 'nowrap' }}>
                  <Text strong>#{r.github_run_id}</Text>
                  {r.url && (
                    <Button size="small" type="link" icon={<LinkOutlined />} href={r.url} target="_blank">
                      GitHub
                    </Button>
                  )}
                </Space>
              ),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 100,
              render: (v: string) => <Tag color={RUN_STATUS_COLOR[v || ''] || 'default'}>{RUN_STATUS_ZH[v || ''] ?? v ?? '-'}</Tag>,
            },
            {
              title: 'Event',
              dataIndex: 'event',
              width: 140,
              ellipsis: true,
              render: (v: string) => <Text style={{ whiteSpace: 'nowrap' }}>{v || '-'}</Text>,
            },
            {
              title: '分支',
              dataIndex: 'branch',
              width: 220,
              ellipsis: { showTitle: true },
              render: (v: string) => <Text style={{ whiteSpace: 'nowrap' }}>{v || '-'}</Text>,
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              width: 160,
              render: (v: string) => <Text style={{ whiteSpace: 'nowrap' }}>{fmtTime(v)}</Text>,
            },
            {
              title: '耗时',
              dataIndex: 'wall_duration',
              width: 90,
              render: (v: number) => <Text style={{ whiteSpace: 'nowrap' }}>{fmtDuration(v)}</Text>,
            },
          ]}
        />
      </Drawer>

      <Modal
        title="可视化配置 Cron"
        open={cronOpen}
        onCancel={() => setCronOpen(false)}
        onOk={applyCron}
        okText="应用"
        destroyOnClose
      >
        <Form form={cronForm} layout="vertical" onValuesChange={() => cronForm.validateFields().catch(() => undefined)}>
          <Form.Item name="frequency" label="执行频率" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'hourly', label: '每小时' },
                { value: 'daily', label: '每天' },
                { value: 'weekly', label: '每周' },
                { value: 'monthly', label: '每月' },
                { value: 'custom', label: '自定义表达式' },
              ]}
            />
          </Form.Item>

          {cronFrequency === 'hourly' && (
            <Form.Item name="minute" label="每小时第几分钟">
              <InputNumber min={0} max={59} style={{ width: '100%' }} />
            </Form.Item>
          )}

          {['daily', 'weekly', 'monthly'].includes(cronFrequency) && (
            <Form.Item name="time" label="执行时间" rules={[{ required: true, message: '请选择执行时间' }]}>
              <TimePicker format="HH:mm" style={{ width: '100%' }} minuteStep={5} />
            </Form.Item>
          )}

          {cronFrequency === 'weekly' && (
            <Form.Item name="weekdays" label="每周执行日" rules={[{ required: true, message: '请选择星期' }]}>
              <Select mode="multiple" options={WEEKDAY_OPTIONS} />
            </Form.Item>
          )}

          {cronFrequency === 'monthly' && (
            <Form.Item name="day" label="每月几号" rules={[{ required: true }]}>
              <InputNumber min={1} max={28} style={{ width: '100%' }} />
            </Form.Item>
          )}

          {cronFrequency === 'custom' && (
            <Form.Item
              name="custom"
              label="5 段 Cron 表达式"
              rules={[{ required: true, message: '请输入 cron 表达式' }]}
            >
              <Input placeholder="分 时 日 月 周，例如 0 2 * * *" />
            </Form.Item>
          )}

          <Alert
            type="info"
            showIcon
            message={`将生成：${buildCron(cronValues || { frequency: cronFrequency }) || '-'}`}
            description={cronValues ? describeCron(cronValues) : undefined}
          />
        </Form>
      </Modal>

      <Modal
        title="发送测试告警邮件"
        open={emailOpen}
        onCancel={() => setEmailOpen(false)}
        onOk={() => emailForm.submit()}
        confirmLoading={testEmailMutation.isPending}
        okText="发送测试邮件"
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="将使用数据库中已配置的 SMTP 服务发送一封真实邮件，内容与正式告警模板一致。"
          description={
            <span>
              若发送失败提示「SMTP 未配置」，请先完成 SMTP 参数配置。
              <Typography.Link onClick={() => navigate('/admin/smtp-config')}>
                前往邮件服务配置
              </Typography.Link>
            </span>
          }
        />
        <Form
          form={emailForm}
          layout="vertical"
          onFinish={(values) => testEmailMutation.mutate(values)}
        >
          <Form.Item
            name="recipients"
            label="收件人"
            rules={[{ required: true, message: '请填写至少一个邮箱' }]}
          >
            <Select mode="tags" tokenSeparators={[',', ';', ' ']} placeholder="输入邮箱后回车" />
          </Form.Item>
          <Form.Item name="target_id" label="邮件中的 Workflow（可选）">
            <Select
              allowClear
              options={targets.map((t) => ({
                value: t.id,
                label: `${t.repo_name} · ${t.workflow_name}`,
              }))}
              placeholder="不选择则使用默认仓库信息"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
