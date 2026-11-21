import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import cronstrue from 'cronstrue'
import 'cronstrue/locales/zh_CN'
import {
  createSchedule,
  getCronPreview,
  getMySchedulePermission,
  getSchedules,
  getWorkflowActionError,
  setScheduleEnabled,
  triggerSchedule,
  updateSchedule,
  type WorkflowSchedule,
  type WorkflowSchedulePayload,
} from '../services/workflowActions'
import { formatTimezone } from '../utils/timezone'
import YamlInputsEditor from './YamlInputsEditor'

const EMPTY_FORM: WorkflowSchedulePayload = {
  name: '',
  workflow_path: '',
  ref: 'main',
  inputs_yaml: '',
  cron_expression: '0 2 * * *',
  enabled: true,
}

const submissionStatus = {
  submitted: { text: '已提交', color: 'green' },
  failed: { text: '提交失败', color: 'red' },
  unknown: { text: '提交状态未确认', color: 'orange' },
}

const describeCron = (expression: string | null) => {
  if (!expression) return 'Cron 规则无效'
  try {
    const [, , day, , weekday] = expression.split(' ')
    const description = cronstrue.toString(expression, {
      locale: 'zh_CN',
      use24HourTimeFormat: true,
    })
    return day !== '*' && weekday !== '*'
      ? `${description}（日期和星期须同时满足）`
      : description
  } catch {
    return '按 Cron 表达式执行'
  }
}

const renderSubmissionStatus = (schedule: WorkflowSchedule) => {
  const status =
    schedule.last_submission_status &&
    submissionStatus[schedule.last_submission_status]
  return status ? (
    <Tag color={status.color}>{status.text}</Tag>
  ) : (
    <Tag>{schedule.last_run_at ? '历史记录' : '尚未触发'}</Tag>
  )
}

function WorkflowActionSchedules() {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<WorkflowSchedulePayload>()
  const [editor, setEditor] = useState<number | 'new' | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [notice, setNotice] = useState<{ text: string; error: boolean }>()
  const [previewExpression, setPreviewExpression] = useState('')
  const cronExpression = Form.useWatch('cron_expression', form)?.trim() ?? ''
  const schedules = useQuery({
    queryKey: ['workflow-action-schedules'],
    queryFn: getSchedules,
    refetchInterval: 60_000,
  })
  const permission = useQuery({
    queryKey: ['workflow-action-schedule-permission'],
    queryFn: getMySchedulePermission,
  })
  const canManage = permission.data === true
  const selected = schedules.data?.find(
    (schedule) => schedule.id === selectedId,
  )

  useEffect(() => {
    if (editor === null) return
    const timer = setTimeout(() => setPreviewExpression(cronExpression), 250)
    return () => clearTimeout(timer)
  }, [cronExpression, editor])

  const canPreview = editor !== null && cronExpression.split(/\s+/).length === 5
  const preview = useQuery({
    queryKey: ['workflow-action-cron-preview', previewExpression],
    queryFn: () => getCronPreview(previewExpression),
    enabled: canPreview && cronExpression === previewExpression,
    retry: false,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: ['workflow-action-schedules'],
    })
  }
  const save = useMutation({
    mutationFn: (data: WorkflowSchedulePayload) =>
      typeof editor === 'number'
        ? updateSchedule(editor, data)
        : createSchedule(data),
    onSuccess: () => {
      setEditor(null)
      refresh()
    },
  })
  const setEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      setScheduleEnabled(id, enabled),
    onSuccess: refresh,
    onError: (error) =>
      setNotice({ text: getWorkflowActionError(error), error: true }),
  })
  const trigger = useMutation({
    mutationFn: triggerSchedule,
    onSuccess: (result) => {
      setNotice({ text: result.output, error: !result.success })
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['workflow-action-logs'] })
    },
    onError: (error) =>
      setNotice({ text: getWorkflowActionError(error), error: true }),
  })

  const openEditor = (schedule?: WorkflowSchedule) => {
    save.reset()
    form.setFieldsValue(schedule ?? EMPTY_FORM)
    setSelectedId(null)
    setEditor(schedule?.id ?? 'new')
  }
  const openDetails = (id: number) => {
    setSelectedId(id)
    void schedules.refetch()
  }
  const confirmTrigger = (schedule: WorkflowSchedule) => {
    Modal.confirm({
      title: `确认立即触发「${schedule.name}」？`,
      content: `${schedule.workflow_path} · ${schedule.ref}`,
      okText: '立即触发',
      cancelText: '取消',
      onOk: () => trigger.mutate(schedule.id),
    })
  }

  const cronHelp = !canPreview
    ? '五段依次为分钟、小时、日期、月份、星期。数字星期从周一（0）到周日（6）。'
    : cronExpression !== previewExpression || preview.isPending
      ? '正在校验 Cron 规则…'
      : preview.isError
        ? getWorkflowActionError(preview.error)
        : `${describeCron(preview.data ?? null)}（北京时间）`

  return (
    <Card
      title="定时任务"
      extra={
        canManage && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => openEditor()}
          >
            新建定时任务
          </Button>
        )
      }
      style={{ marginBottom: 24 }}
    >
      {notice && (
        <Alert
          type={notice.error ? 'error' : 'success'}
          showIcon
          closable
          onClose={() => setNotice(undefined)}
          message={
            <span style={{ whiteSpace: 'pre-wrap' }}>{notice.text}</span>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      {(schedules.isError || permission.isError) && (
        <Alert
          type="error"
          showIcon
          message={
            schedules.isError ? '定时任务加载失败' : '定时任务权限加载失败'
          }
          action={
            <Button
              size="small"
              onClick={() => {
                void schedules.refetch()
                void permission.refetch()
              }}
            >
              重试
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      <Table<WorkflowSchedule>
        rowKey="id"
        loading={schedules.isLoading}
        dataSource={schedules.data ?? []}
        pagination={false}
        scroll={{ x: 800 }}
        columns={[
          { title: '任务名', dataIndex: 'name' },
          {
            title: '触发规则（北京时间）',
            render: (_, schedule) => (
              <>
                <Typography.Text code>
                  {schedule.cron_expression}
                </Typography.Text>
                <div>{describeCron(schedule.cron_preview_expression)}</div>
              </>
            ),
          },
          {
            title: '下次计划触发',
            dataIndex: 'next_run_at',
            render: (value: string | null) =>
              value ? formatTimezone(value, 'YYYY-MM-DD HH:mm') : '—',
          },
          {
            title: '最近提交',
            render: (_, schedule) => renderSubmissionStatus(schedule),
          },
          {
            title: '操作',
            render: (_, schedule) => (
              <Space wrap>
                <Button
                  type="link"
                  size="small"
                  onClick={() => openDetails(schedule.id)}
                >
                  查看详情
                </Button>
                {canManage ? (
                  <>
                    <Button
                      type="link"
                      size="small"
                      loading={
                        trigger.isPending && trigger.variables === schedule.id
                      }
                      onClick={() => confirmTrigger(schedule)}
                    >
                      立即触发
                    </Button>
                    <Switch
                      aria-label={`启用 ${schedule.name}`}
                      checked={schedule.enabled}
                      disabled={setEnabled.isPending}
                      onChange={(enabled) =>
                        setEnabled.mutate({ id: schedule.id, enabled })
                      }
                    />
                  </>
                ) : (
                  <Tag>{schedule.enabled ? '已启用' : '已停用'}</Tag>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={selected?.name}
        open={!!selected}
        onCancel={() => setSelectedId(null)}
        footer={
          <Space>
            <Button onClick={() => setSelectedId(null)}>关闭</Button>
            {canManage && selected && (
              <Button type="primary" onClick={() => openEditor(selected)}>
                编辑任务
              </Button>
            )}
          </Space>
        }
        width={680}
      >
        {selected && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <div>
              <Typography.Text strong>最近提交</Typography.Text>{' '}
              {renderSubmissionStatus(selected)}
              {selected.last_run_at && (
                <Typography.Text type="secondary">
                  {formatTimezone(selected.last_run_at)}
                </Typography.Text>
              )}
              {selected.last_output && (
                <pre
                  style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                >
                  {selected.last_output}
                </pre>
              )}
            </div>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="工作流" span={2}>
                {selected.workflow_path}
              </Descriptions.Item>
              <Descriptions.Item label="Ref">{selected.ref}</Descriptions.Item>
              <Descriptions.Item label="设置人">
                {selected.setter_username}
              </Descriptions.Item>
              <Descriptions.Item label="触发规则" span={2}>
                {describeCron(selected.cron_preview_expression)}（北京时间） ·{' '}
                <code>{selected.cron_expression}</code>
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Text strong>Inputs YAML</Typography.Text>
              {selected.inputs_yaml ? (
                <Typography.Paragraph
                  copyable={{ text: selected.inputs_yaml }}
                  style={{
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'monospace',
                    maxHeight: 240,
                    overflow: 'auto',
                  }}
                >
                  {selected.inputs_yaml}
                </Typography.Paragraph>
              ) : (
                <div>使用工作流默认参数</div>
              )}
            </div>
          </Space>
        )}
      </Modal>

      <Modal
        title={editor === 'new' ? '新建定时任务' : '编辑定时任务'}
        open={canManage && editor !== null}
        onCancel={() => setEditor(null)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        okText="保存"
        cancelText="取消"
        forceRender
      >
        {save.isError && (
          <Alert
            type="error"
            showIcon
            message={getWorkflowActionError(save.error)}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => save.mutate(values)}
        >
          <Form.Item
            name="name"
            label="任务名"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="workflow_path"
            label="工作流路径"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input placeholder=".github/workflows/example.yaml" />
          </Form.Item>
          <Form.Item
            name="ref"
            label="Ref"
            rules={[{ required: true, whitespace: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="cron_expression"
            label="Cron 表达式"
            rules={[{ required: true, whitespace: true }]}
            extra={<span aria-live="polite">{cronHelp}</span>}
          >
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="inputs_yaml" label="Inputs YAML（可选）">
            <YamlInputsEditor />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

export default WorkflowActionSchedules
