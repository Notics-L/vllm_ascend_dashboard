import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'
import { Alert, Button, Checkbox, ConfigProvider, Descriptions, Drawer, Flex, Form, Input, InputNumber, Modal, Radio, Select, Space, Switch, Tabs, Tag, Typography, message } from 'antd'
import { ProTable, type ProColumns } from '@ant-design/pro-components'
import { ArrowLeftOutlined, DeleteOutlined, EditOutlined, HistoryOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { getDailySourceRecommendation } from '../services/dailyPipeline'
import { formatTimezone } from '../utils/timezone'
import {
  deleteImageBuildTriggerTasks,
  getImageBuildTriggerRecord,
  getImageBuildTriggerRecords,
  getImageBuildTriggerTasks,
  getImageBuildWorkflows,
  saveImageBuildTriggerTask,
  syncImageBuildWorkflows,
  triggerImageBuildTask,
  type ImageBuildSourceRefMode,
  type ImageBuildTriggerRecord,
  type ImageBuildTriggerTask,
  type ImageBuildTriggerTaskValues,
  type ImageBuildWorkflowInput,
} from '../services/imageBuild'
import DailyCasePage from './daily-pipeline/cases/DailyCasePage'
import { ImageBuildCollection } from './ImageBuildCollection'
import { dailyTableRefresh } from './daily-pipeline/shared/dailyTableRefresh'
import './daily-pipeline/shared/DailyStableTabs.css'

const triggerTasksQueryKey = ['image-build-trigger-tasks']
const triggerRecordsQueryKey = ['image-build-trigger-records']
const workflowQueryOptions = queryOptions({
  queryKey: ['image-build-workflows'],
  queryFn: ({ signal }) => getImageBuildWorkflows(signal),
})
const sourceBranchInput = 'vllm_ascend_branch'
const sourceRefInput = 'source_ref'
const sourceRefModeLabels: Record<ImageBuildSourceRefMode, string> = {
  branch: '按源码分支',
  recommended: '使用系统推荐',
  manual: '手动填写',
}
const sourceRefModeOptions = Object.entries(sourceRefModeLabels).map(([value, label]) => ({ value, label }))
const buildActionDialogStyle = {
  className: 'daily-cases-actions',
  transitionName: '',
  maskTransitionName: '',
  okType: 'default',
  okButtonProps: { size: 'small', color: 'primary', variant: 'solid' },
  cancelButtonProps: { size: 'small' },
} as const
const triggerTaskColumns: ProColumns<ImageBuildTriggerTask>[] = [
  { title: 'Workflow 分支', dataIndex: 'workflow_branch', width: 200 },
  {
    title: '构建源码分支',
    width: 200,
    render: (_, task) => displayTaskInput(task, sourceBranchInput),
  },
  {
    title: 'Cron 表达式',
    dataIndex: 'cron_expression',
    width: 172,
    render: (_, task) => task.cron_expression.trim() || '仅手动',
  },
]
const triggerRecordLabels = {
  submitting: { label: '提交中', color: 'processing' },
  submitted: { label: '已提交', color: 'success' },
  failed: { label: '提交失败', color: 'error' },
  unknown: { label: '待确认', color: 'warning' },
}
const triggerRecordColumns: ProColumns<ImageBuildTriggerRecord>[] = [
  {
    title: '触发时间',
    dataIndex: 'triggered_at',
    width: 170,
    render: (_, record) => formatTimezone(record.triggered_at),
  },
  { title: 'Workflow 分支', dataIndex: 'workflow_branch', width: 160 },
  { title: '触发人', dataIndex: 'username', width: 100 },
  {
    title: '触发结果',
    dataIndex: 'status',
    width: 104,
    render: (_, record) => {
      const { label, color } = triggerRecordLabels[record.status]
      return <Tag color={color}>{label}</Tag>
    },
  },
  { title: '说明', dataIndex: 'message', width: 230 },
  {
    title: 'GitHub Run',
    dataIndex: 'run_url',
    width: 104,
    render: (_, record) => record.run_url
      ? <Typography.Link href={record.run_url} target="_blank" rel="noreferrer">查看</Typography.Link>
      : '—',
  },
]
function requestError(error: unknown, fallback: string) {
  const detail = isAxiosError<{ detail?: unknown }>(error) ? error.response?.data?.detail : undefined
  return typeof detail === 'string' ? detail : fallback
}

function parseChips(value: unknown): string[] {
  if (typeof value !== 'string') return []
  try {
    const parsed: unknown = JSON.parse(value)
    return Array.isArray(parsed) && parsed.every((chip) => typeof chip === 'string') ? parsed : []
  } catch {
    return []
  }
}

function displayTaskInput(task: ImageBuildTriggerTask, name: string) {
  const value = Object.prototype.hasOwnProperty.call(task.inputs, name) ? task.inputs[name] : undefined
  if (name === sourceRefInput && (
    task.source_ref_mode === 'recommended' || (task.source_ref_mode === 'branch' && !value)
  )) {
    return sourceRefModeLabels[task.source_ref_mode]
  }
  if (value == null || value === '') return '—'
  if (name === 'chips') {
    const chips = parseChips(value)
    if (chips.length) return chips.join(', ')
  }
  return String(value)
}

function renderInput(input: ImageBuildWorkflowInput, chipOptions: string[]) {
  if (chipOptions.length) {
    return <Checkbox.Group options={chipOptions.map((chip) => ({ value: chip, label: chip.toUpperCase() }))} />
  }
  switch (input.type) {
    case 'boolean':
      return <Switch />
    case 'choice':
    case 'environment':
      return (
        <Select
          showSearch
          disabled={input.type === 'environment' && input.options.length === 0}
          placeholder={input.type === 'environment' ? '选择 GitHub 环境' : undefined}
          options={input.options.map((option) => ({ value: option, label: option }))}
        />
      )
    case 'number':
      return <InputNumber style={{ width: '100%' }} />
    case 'string':
    default:
      return <Input />
  }
}

function resolveSourceBranch(value: ImageBuildWorkflowInput['default'], fallback: ImageBuildWorkflowInput['default']) {
  const branch = value || fallback
  return typeof branch === 'string' ? branch.replace(/^refs\/heads\//, '') : ''
}

function initialInputValue(input: ImageBuildWorkflowInput, workflowBranch?: string) {
  if (input.name === sourceBranchInput && workflowBranch && (
    input.type === 'string' || (input.type === 'choice' && input.options.includes(workflowBranch))
  )) {
    return workflowBranch
  }
  const value = input.default ?? undefined
  if (input.type === 'boolean') return value ?? false
  if (input.type === 'environment' && (typeof value !== 'string' || !input.options.includes(value))) {
    return undefined
  }
  return value
}

type TriggerFormValues = Pick<ImageBuildTriggerTaskValues, 'cron_expression' | 'inputs'>

interface BuildTriggerFormProps {
  task?: ImageBuildTriggerTask
  canManage: boolean
  saving: boolean
  scheduleConflict?: string
  onScheduleChange: () => void
  onSyncSuccess: (changed: number) => void
  onSave: (values: ImageBuildTriggerTaskValues) => void
}

function BuildTriggerForm({
  task, canManage, saving, scheduleConflict, onScheduleChange, onSyncSuccess, onSave,
}: BuildTriggerFormProps) {
  const [selectedBranch, setSelectedBranch] = useState<string>()
  const [cron, setCron] = useState(task?.cron_expression ?? '')
  const [sourceRefMode, setSourceRefMode] = useState<ImageBuildSourceRefMode>(task?.source_ref_mode ?? 'branch')
  const [sourceBranchSelection, setSourceBranchSelection] = useState<{
    workflowKey: string
    value: ImageBuildWorkflowInput['default']
  }>()
  const workflowsQuery = useQuery({ ...workflowQueryOptions, refetchOnMount: 'always' })
  const [feedback, setFeedback] = useState<{ type: 'warning' | 'error'; text: string }>()
  const syncMutation = useMutation({
    mutationFn: syncImageBuildWorkflows,
    onMutate: () => setFeedback(undefined),
    onSuccess: async (result) => {
      const refreshed = await workflowsQuery.refetch()
      if (refreshed.isError) {
        setFeedback({ type: 'warning', text: '同步已结束，Workflow 列表刷新失败' })
      } else if (result.failed > 0) {
        setFeedback({ type: 'warning', text: `部分分支同步失败，已更新 ${result.changed} 个分支` })
      } else {
        onSyncSuccess(result.changed)
      }
    },
    onError: (error) => {
      setFeedback({ type: 'error', text: requestError(error, 'Workflow 同步失败，请稍后重试') })
    },
  })
  const workflows = workflowsQuery.data ?? []
  const workflowUrl = workflows[0]?.workflow_url
  const requestedBranch = selectedBranch ?? task?.workflow_branch
  const workflow = requestedBranch
    ? workflows.find(({ branch_name }) => branch_name === requestedBranch)
    : workflows.find(({ branch_name }) => branch_name === 'main') ?? workflows[0]
  const workflowKey = workflow ? `${workflow.branch_name}:${workflow.inputs_hash}` : 'no-workflow'
  const inputs = workflow?.inputs ?? []
  const supportsSourceRef = inputs.some((input) => input.name === sourceRefInput && input.type === 'string')
  const savedInputs = workflow && task?.workflow_branch === workflow.branch_name ? task.inputs : undefined
  const initialInputs = Object.fromEntries(inputs.map((input) => {
    if (input.name === sourceBranchInput && (!task || selectedBranch !== undefined)) {
      return [input.name, initialInputValue(input, workflow?.branch_name)]
    }
    const savedValue = savedInputs?.[input.name]
    return [input.name, savedValue === undefined ? initialInputValue(input) : savedValue]
  }))

  const defaultSourceBranch = inputs.find((input) => input.name === sourceBranchInput)?.default
  const selectedSourceBranch = sourceBranchSelection?.workflowKey === workflowKey
    ? sourceBranchSelection.value
    : initialInputs[sourceBranchInput]
  const effectiveSourceRefMode = supportsSourceRef ? sourceRefMode : 'branch'
  const recommendationBranch = resolveSourceBranch(selectedSourceBranch, defaultSourceBranch)
  const recommendationRepository = workflow?.workflow_url?.match(
    /^https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/actions\/workflows\//i,
  )?.[1] ?? ''
  const showRecommendation = effectiveSourceRefMode === 'recommended'
  const canLoadRecommendation = showRecommendation && Boolean(recommendationRepository && recommendationBranch)
  const recommendationQuery = useQuery({
    queryKey: ['daily-pipeline', 'source-ref-recommendation', recommendationRepository, recommendationBranch],
    queryFn: ({ signal }) => getDailySourceRecommendation(recommendationBranch, recommendationRepository, signal),
    enabled: canLoadRecommendation,
    staleTime: 0,
  })
  const recommendation = recommendationQuery.data
  const recommendationReady = Boolean(recommendation?.commit_sha)
    && !recommendationQuery.isError && !recommendationQuery.isFetching
  const canSave = canManage && Boolean(workflow) && !saving && !workflowsQuery.isError && !syncMutation.isPending
    && (!showRecommendation || recommendationReady)

  function refreshForm() {
    void workflowsQuery.refetch()
    if (canLoadRecommendation) void recommendationQuery.refetch()
  }

  return (
    <Form<TriggerFormValues>
      key={workflowKey}
      className="daily-case-config-form"
      layout="horizontal"
      labelAlign="left"
      labelWrap
      labelCol={{ xs: { span: 24 }, sm: { flex: '160px' } }}
      wrapperCol={{ xs: { span: 24 }, sm: { flex: 'auto' } }}
      requiredMark
      initialValues={{ inputs: initialInputs, cron_expression: cron }}
      disabled={saving}
      onValuesChange={(changed) => {
        if (typeof changed.cron_expression === 'string') {
          setCron(changed.cron_expression)
          onScheduleChange()
        } else if (changed.inputs && sourceBranchInput in changed.inputs) {
          const value = changed.inputs[sourceBranchInput]
          setSourceBranchSelection({ workflowKey, value })
          onScheduleChange()
        }
      }}
      onFinish={({ inputs, cron_expression }) => {
        if (workflow && canSave) {
          onSave({
            workflow_branch: workflow.branch_name,
            source_ref_mode: effectiveSourceRefMode,
            inputs: inputs ?? {},
            cron_expression: cron_expression?.trim() ?? '',
          })
        }
      }}
      style={{ maxWidth: 640 }}
    >
      <Form.Item label="GitHub 工作流">
        <Space className="daily-cases-actions" size={8} wrap>
          {workflowUrl ? (
            <Typography.Link href={workflowUrl} target="_blank" rel="noreferrer">
              Nightly Image Build (with base)
            </Typography.Link>
          ) : (
            <Typography.Text>Nightly Image Build (with base)</Typography.Text>
          )}
          {canManage && (
            <Button
              size="small"
              icon={<SyncOutlined />}
              loading={syncMutation.isPending}
              onClick={() => syncMutation.mutate()}
            >
              同步 Workflow
            </Button>
          )}
        </Space>
      </Form.Item>
      {feedback && (
        <Alert
          type={feedback.type}
          message={feedback.text}
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}
      <Form.Item label="Workflow 分支" htmlFor="image-build-workflow-branch" required>
        <Select
          id="image-build-workflow-branch"
          aria-required="true"
          value={requestedBranch ?? workflow?.branch_name}
          onChange={(branch) => {
            setSelectedBranch(branch)
            setSourceBranchSelection(undefined)
            onScheduleChange()
          }}
          loading={workflowsQuery.isPending}
          disabled={!workflows.length}
          placeholder="选择 Workflow 分支"
          showSearch
          options={workflows.map(({ branch_name }) => ({ value: branch_name, label: branch_name }))}
        />
      </Form.Item>
      {workflowsQuery.isError && (
        <Alert
          type="error"
          showIcon
          className="daily-cases-actions"
          message="Workflow 定义加载失败"
          action={<Button size="small" onClick={() => void workflowsQuery.refetch()}>重试</Button>}
          style={{ marginBottom: 16 }}
        />
      )}
      {workflowsQuery.isSuccess && !workflows.length && (
        <Alert type="info" showIcon message="暂无可触发的 Workflow 分支，等待同步" style={{ marginBottom: 16 }} />
      )}
      {workflowsQuery.isSuccess && workflows.length > 0 && requestedBranch && !workflow && (
        <Alert type="warning" showIcon message="当前 Workflow 分支不可用，请重新选择" style={{ marginBottom: 16 }} />
      )}
      {inputs.map((input) => {
        if (input.name === sourceRefInput && input.type === 'string') {
          return (
            <Form.Item key={input.name} label={input.name} required={effectiveSourceRefMode === 'manual'}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Radio.Group
                  aria-label="source_ref 取值方式"
                  optionType="button"
                  buttonStyle="solid"
                  options={sourceRefModeOptions}
                  value={effectiveSourceRefMode}
                  onChange={(event) => setSourceRefMode(event.target.value)}
                />
                {effectiveSourceRefMode === 'manual' && (
                  <Form.Item
                    name={['inputs', sourceRefInput]}
                    rules={[{ required: true, whitespace: true, message: '请输入 source_ref' }]}
                    style={{ marginBottom: 0 }}
                  >
                    <Input aria-label="手动输入 source_ref" placeholder="例如 refs/pull/1234/head" />
                  </Form.Item>
                )}
                {showRecommendation && (
                  !recommendationBranch ? (
                    <Typography.Text type="secondary">请先选择源码分支</Typography.Text>
                  ) : !canLoadRecommendation || recommendationQuery.isError ? (
                    <Typography.Text type="danger">Daily 推荐加载失败，点击「刷新」重试</Typography.Text>
                  ) : recommendationQuery.isPending ? (
                    <Typography.Text type="secondary">正在读取 Daily 推荐…</Typography.Text>
                  ) : recommendation ? (
                    <Descriptions size="small" column={1} items={[
                      {
                        key: 'sha',
                        label: '推荐 SHA',
                        children: (
                          <Typography.Link
                            href={`https://github.com/${recommendationRepository}/commit/${recommendation.commit_sha}`}
                            target="_blank"
                            rel="noreferrer"
                            style={{ overflowWrap: 'anywhere' }}
                          >
                            {recommendation.commit_sha}
                          </Typography.Link>
                        ),
                      },
                      { key: 'time', label: '提交时间', children: formatTimezone(recommendation.committed_at) },
                      { key: 'daily', label: 'Daily 触发 ID', children: recommendation.trigger_history_id },
                      {
                        key: 'sourcePr',
                        label: '推荐来源 PR',
                        children: recommendation.pr_number == null ? '—' : `#${recommendation.pr_number}`,
                      },
                      ...(recommendation.problem_pr_number == null ? [] : [{
                        key: 'problemPr', label: '问题 PR', children: `#${recommendation.problem_pr_number}`,
                      }]),
                    ]} />
                  ) : (
                    <Typography.Text type="secondary">该源码分支暂无可用的 Daily 推荐</Typography.Text>
                  )
                )}
              </Space>
            </Form.Item>
          )
        }
        // The workflow accepts a JSON string for chips. Use its declared array to offer chip choices.
        const chipOptions = input.name === 'chips' && input.type === 'string' ? parseChips(input.default) : []
        return (
          <Form.Item
            key={input.name}
            name={['inputs', input.name]}
            label={input.name}
            valuePropName={input.type === 'boolean' ? 'checked' : 'value'}
            getValueProps={chipOptions.length ? (value) => ({ value: parseChips(value) }) : undefined}
            normalize={chipOptions.length ? (value: string[]) => JSON.stringify(value) : undefined}
            rules={input.required ? [{ required: true, message: `请填写${input.name}` }] : undefined}
          >
            {renderInput(input, chipOptions)}
          </Form.Item>
        )
      })}
      <Form.Item
        name="cron_expression"
        label="Cron 表达式"
        validateTrigger="onBlur"
        validateStatus={scheduleConflict ? 'error' : undefined}
        help={scheduleConflict}
        rules={[{
          transform: (value?: string) => value?.trim(),
          pattern: /^[0-9*,/-]+(?:\s+[0-9*,/-]+){4}$/,
          message: '请输入五段 Cron 表达式',
        }]}
      >
        <Input placeholder="留空则仅手动触发" maxLength={120} />
      </Form.Item>
      <Form.Item className="daily-cases-actions">
        <Space size={8}>
          <Button
            color="primary"
            variant="solid"
            htmlType="submit"
            icon={<SaveOutlined />}
            loading={saving}
            disabled={!canSave}
          >
            保存
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={workflowsQuery.isFetching || (canLoadRecommendation && recommendationQuery.isFetching)}
            disabled={saving || syncMutation.isPending}
            onClick={refreshForm}
          >
            刷新
          </Button>
        </Space>
      </Form.Item>
    </Form>
  )
}

function TriggerRecordDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const recordQuery = useQuery({
    queryKey: [...triggerRecordsQueryKey, id],
    queryFn: ({ signal }) => getImageBuildTriggerRecord(id, signal),
    refetchInterval: (query) => query.state.data?.status === 'submitting' ? 3000 : false,
  })
  const record = recordQuery.data
  const snapshot = record?.dispatch_snapshot

  return (
    <Modal
      title="触发详情"
      open
      onCancel={onClose}
      footer={null}
      width={720}
      loading={recordQuery.isPending}
      transitionName=""
      maskTransitionName=""
    >
      {recordQuery.isError && (
        <Alert
          type="error"
          showIcon
          className="daily-cases-actions"
          message={requestError(recordQuery.error, '触发详情加载失败')}
          action={<Button size="small" onClick={() => void recordQuery.refetch()}>重试</Button>}
        />
      )}
      {record && (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
            { key: 'time', label: '触发时间', children: formatTimezone(record.triggered_at) },
            { key: 'user', label: '触发人', children: record.username },
            { key: 'task', label: '任务 ID', children: record.task_id },
            { key: 'branch', label: 'Workflow 分支', children: record.workflow_branch },
            ...(snapshot ? [
              { key: 'repository', label: '仓库', children: snapshot.repository },
              { key: 'workflow', label: 'Workflow 文件', children: snapshot.workflow_path, span: 2 },
              ...(snapshot.source_ref_mode ? [
                { key: 'sourceRefMode', label: 'source_ref 模式', children: sourceRefModeLabels[snapshot.source_ref_mode] },
              ] : []),
              ...(snapshot.source_recommendation ? [
                { key: 'dailyHistory', label: 'Daily 触发 ID', children: snapshot.source_recommendation.trigger_history_id },
                {
                  key: 'dailySourcePR',
                  label: '推荐来源 PR',
                  children: snapshot.source_recommendation.pr_number == null ? '—' : `#${snapshot.source_recommendation.pr_number}`,
                },
              ] : []),
            ] : []),
            {
              key: 'status',
              label: '触发结果',
              children: <Tag color={triggerRecordLabels[record.status].color}>{triggerRecordLabels[record.status].label}</Tag>,
            },
            {
              key: 'http',
              label: 'GitHub 响应状态',
              children: record.github_status_code ?? '—',
            },
            { key: 'message', label: '说明', children: record.message, span: 2 },
            {
              key: 'run',
              label: 'GitHub Run',
              children: record.run_url
                ? <Typography.Link href={record.run_url} target="_blank" rel="noreferrer">{record.run_id}</Typography.Link>
                : '—',
            },
          ]} />
          <Typography.Text strong>Workflow 参数</Typography.Text>
          {snapshot
            ? <pre style={{ margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                {JSON.stringify(snapshot.inputs, null, 2)}
              </pre>
            : <Typography.Text type="secondary">该记录未保存参数快照</Typography.Text>}
        </Space>
      )}
    </Modal>
  )
}

function BuildTriggerTasks() {
  const [editor, setEditor] = useState<{ task?: ImageBuildTriggerTask }>()
  const [scheduleConflict, setScheduleConflict] = useState<string>()
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [recordsOpen, setRecordsOpen] = useState(false)
  const [detailRecordId, setDetailRecordId] = useState<number>()
  const [modal, modalHolder] = Modal.useModal()
  const [messages, messageHolder] = message.useMessage()
  const queryClient = useQueryClient()
  const { data: currentUser } = useCurrentUser()
  const canManage = currentUser?.role === 'admin' || currentUser?.role === 'super_admin'
  const tasksQuery = useQuery({
    queryKey: triggerTasksQueryKey,
    queryFn: ({ signal }) => getImageBuildTriggerTasks(signal),
  })
  const workflowsQuery = useQuery(workflowQueryOptions)
  const recordsQuery = useQuery({
    queryKey: triggerRecordsQueryKey,
    queryFn: ({ signal }) => getImageBuildTriggerRecords(signal),
    enabled: recordsOpen,
    refetchInterval: (query) =>
      query.state.data?.some((record) => record.status === 'submitting') ? 3000 : false,
  })
  const tasks = tasksQuery.data ?? []
  const selectedTasks = tasks.filter((task) => selectedIds.includes(task.id))
  const selectedTask = selectedTasks.length === 1 ? selectedTasks[0] : undefined
  const refresh = () => queryClient.invalidateQueries({ queryKey: triggerTasksQueryKey })

  const saveTask = useMutation({
    mutationFn: ({ id, values }: { id?: number; values: ImageBuildTriggerTaskValues }) =>
      saveImageBuildTriggerTask(id, values),
    onMutate: () => { setScheduleConflict(undefined) },
    onSuccess: async (_, { id }) => {
      await refresh()
      setEditor(undefined)
      setSelectedIds([])
      messages.success(id === undefined ? '任务已新增' : '任务已修改')
    },
    onError: (error) => {
      if (isAxiosError(error) && error.response?.status === 409) {
        setScheduleConflict(requestError(error, '该源码分支的执行时间有冲突，请修改 Cron 表达式'))
      } else {
        messages.error(requestError(error, '保存失败，请重试'))
      }
    },
  })
  const removeTask = useMutation({
    mutationFn: deleteImageBuildTriggerTasks,
    onSuccess: async ({ deleted }, ids) => {
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)))
      await refresh()
      messages.success(`已删除 ${deleted} 条任务`)
    },
    onError: (error) => { messages.error(requestError(error, '删除失败，请重试')) },
  })

  const triggerTask = useMutation({
    mutationFn: triggerImageBuildTask,
    onSuccess: (record) => {
      if (record.status === 'submitted') {
        messages.success(record.message)
      } else if (record.status === 'failed') {
        messages.error(record.message)
      } else {
        messages.warning(record.message)
      }
    },
    onError: (error) => {
      messages.error(requestError(error, '触发结果未确认，请先查看触发记录'))
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: triggerRecordsQueryKey })
    },
  })

  function confirmTrigger(task: ImageBuildTriggerTask) {
    modal.confirm({
      ...buildActionDialogStyle,
      title: '立即触发镜像构建？',
      content: (
        <>
          <div>Workflow 分支：{task.workflow_branch}</div>
          <div>构建源码分支：{displayTaskInput(task, sourceBranchInput)}</div>
        </>
      ),
      okText: '触发',
      cancelText: '取消',
      onOk: () => triggerTask.mutateAsync(task.id),
    })
  }

  function confirmDelete(tasksToDelete: ImageBuildTriggerTask[]) {
    if (!tasksToDelete.length || removeTask.isPending) return
    const task = tasksToDelete.length === 1 ? tasksToDelete[0] : undefined
    modal.confirm({
      ...buildActionDialogStyle,
      title: `删除选中的 ${tasksToDelete.length} 条构建任务？`,
      content: task ? `${task.workflow_branch}（${task.cron_expression || '仅手动'}）` : undefined,
      okText: '删除',
      okButtonProps: { size: 'small', color: 'danger', variant: 'solid' },
      cancelText: '取消',
      onOk: () => removeTask.mutateAsync(tasksToDelete.map(({ id }) => id)),
    })
  }

  const taskBranches = new Set(tasks.map((task) => task.workflow_branch))
  const inputNames = new Set<string>()
  for (const workflow of workflowsQuery.data ?? []) {
    if (!taskBranches.size || taskBranches.has(workflow.branch_name)) {
      for (const input of workflow.inputs) inputNames.add(input.name)
    }
  }
  for (const task of tasks) {
    for (const name of Object.keys(task.inputs)) inputNames.add(name)
    if (task.source_ref_mode === 'recommended') inputNames.add(sourceRefInput)
  }
  inputNames.delete(sourceBranchInput)

  const columns: ProColumns<ImageBuildTriggerTask>[] = [
    ...triggerTaskColumns,
    ...Array.from(inputNames, (name) => ({
      title: name,
      key: `input:${name}`,
      width: 170,
      ellipsis: true,
      render: (_: unknown, task: ImageBuildTriggerTask) => displayTaskInput(task, name),
    })),
    {
      title: '更新时间',
      key: 'update_at',
      width: 175,
      render: (_, task) => formatTimezone(task.update_at),
    },
  ]
  if (canManage) {
    columns.push({
      title: '触发',
      key: 'trigger',
      fixed: 'right',
      width: 88,
      render: (_, task) => (
        <Button
          type="link"
          size="small"
          icon={<PlayCircleOutlined />}
          loading={triggerTask.isPending && triggerTask.variables === task.id}
          disabled={triggerTask.isPending}
          onClick={() => confirmTrigger(task)}
        >
          触发
        </Button>
      ),
    })
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {modalHolder}
      {messageHolder}
      <Drawer
        title="触发记录"
        width={960}
        styles={{ wrapper: { maxWidth: '100vw' } }}
        open={recordsOpen}
        onClose={() => {
          setRecordsOpen(false)
          setDetailRecordId(undefined)
        }}
      >
        {recordsQuery.isError && (
          <Alert
            type="error"
            showIcon
            className="daily-cases-actions"
            message="触发记录加载失败"
            action={<Button size="small" onClick={() => void recordsQuery.refetch()}>重试</Button>}
            style={{ marginBottom: 12 }}
          />
        )}
        <ProTable<ImageBuildTriggerRecord>
          className="daily-cases-pro-table"
          tableClassName="daily-cases-table"
          rowKey="id"
          size="small"
          columns={[...triggerRecordColumns, {
            title: '详情',
            key: 'detail',
            width: 80,
            fixed: 'right',
            render: (_, record) => (
              <Button type="link" size="small" onClick={() => setDetailRecordId(record.id)}>详情</Button>
            ),
          }]}
          dataSource={recordsQuery.data ?? []}
          loading={recordsQuery.isLoading}
          cardProps={false}
          search={false}
          options={false}
          toolBarRender={false}
          pagination={{ defaultPageSize: 10 }}
          scroll={{ x: 'max-content' }}
          locale={{ emptyText: '暂无触发记录' }}
        />
      </Drawer>
      {detailRecordId !== undefined && (
        <TriggerRecordDetail id={detailRecordId} onClose={() => setDetailRecordId(undefined)} />
      )}
      {editor ? (
        <>
          <Space className="daily-cases-actions" size={8}>
            <Button
              icon={<ArrowLeftOutlined />}
              disabled={saveTask.isPending}
              onClick={() => {
                setEditor(undefined)
                setScheduleConflict(undefined)
              }}
            >
              返回列表
            </Button>
          </Space>
          <BuildTriggerForm
            task={editor.task}
            canManage={canManage}
            saving={saveTask.isPending}
            scheduleConflict={scheduleConflict}
            onScheduleChange={() => setScheduleConflict(undefined)}
            onSyncSuccess={(changed) => modal.success({
              ...buildActionDialogStyle,
              title: 'Workflow 同步成功',
              content: `已更新 ${changed} 个分支`,
              okText: '确定',
            })}
            onSave={(values) => saveTask.mutate({ id: editor.task?.id, values })}
          />
        </>
      ) : (
        <>
          <Flex className="daily-cases-actions" align="center" gap={8} wrap>
            {canManage && (
              <Space size={8} wrap>
                <Button color="primary" variant="outlined" icon={<PlusOutlined />} onClick={() => setEditor({})}>新增</Button>
                <Button
                  color="cyan"
                  variant="outlined"
                  icon={<EditOutlined />}
                  disabled={!selectedTask || removeTask.isPending}
                  onClick={() => selectedTask && setEditor({ task: selectedTask })}
                >
                  修改
                </Button>
                <Button
                  color="danger"
                  variant="outlined"
                  icon={<DeleteOutlined />}
                  disabled={!selectedTasks.length || removeTask.isPending}
                  loading={removeTask.isPending}
                  onClick={() => confirmDelete(selectedTasks)}
                >
                  删除
                </Button>
              </Space>
            )}
            <Button
              icon={<HistoryOutlined />}
              style={{ marginInlineStart: 'auto' }}
              onClick={() => setRecordsOpen(true)}
            >
              查看记录
            </Button>
          </Flex>
          {tasksQuery.isError && (
            <Alert
              type="error"
              showIcon
              className="daily-cases-actions"
              message="构建任务加载失败"
              action={<Button size="small" onClick={() => void tasksQuery.refetch()}>重试</Button>}
            />
          )}
          <ProTable<ImageBuildTriggerTask>
            className="daily-cases-pro-table"
            tableClassName="daily-cases-table"
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={tasks}
            cardProps={false}
            search={false}
            headerTitle={false}
            toolBarRender={false}
            tableAlertRender={false}
            {...dailyTableRefresh(
              () => Promise.all([tasksQuery.refetch(), workflowsQuery.refetch()]),
              tasksQuery.isFetching || workflowsQuery.isFetching || removeTask.isPending,
              false,
            )}
            scroll={{ x: 'max-content' }}
            rowSelection={canManage ? {
              type: 'checkbox',
              columnWidth: 48,
              fixed: true,
              selectedRowKeys: selectedTasks.map(({ id }) => id),
              preserveSelectedRowKeys: true,
              onChange: (keys) => setSelectedIds(keys.map(Number)),
              getCheckboxProps: () => ({ disabled: removeTask.isPending }),
            } : undefined}
            locale={{ emptyText: '暂无构建任务' }}
          />
        </>
      )}
    </Space>
  )
}

export default function ImageBuild() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') === 'collection' ? 'collection' : 'trigger'

  const changeTab = (key: string) => {
    const nextParams = new URLSearchParams(searchParams)
    if (key === 'collection') nextParams.set('tab', key)
    else nextParams.delete('tab')
    setSearchParams(nextParams, { replace: true })
  }

  return (
    <DailyCasePage title="镜像构建">
      <Tabs
        className="daily-stable-tabs"
        activeKey={activeTab}
        onChange={changeTab}
        animated={false}
        items={[
          {
            key: 'trigger',
            label: '工作流触发',
            children: (
              <ConfigProvider wave={{ disabled: true }}>
                <BuildTriggerTasks />
              </ConfigProvider>
            ),
          },
          { key: 'collection', label: '数据采集', children: <ImageBuildCollection /> },
        ]}
      />
    </DailyCasePage>
  )
}
