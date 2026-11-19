import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
} from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'
import {
  executeWorkflowAction,
  getMyPermissions,
  getWorkflowActionLogs,
  getWorkflowActionError,
  type WorkflowAction,
} from '../services/workflowActions'
import { formatTimezone } from '../utils/timezone'
import WorkflowActionSchedules from '../components/WorkflowActionSchedules'
import YamlInputsEditor from '../components/YamlInputsEditor'

const actionOptions = [
  { value: 'dispatch', label: '触发工作流' },
  { value: 'rerun', label: '重跑全部 Job' },
  { value: 'rerun-failed', label: '重跑失败 Job' },
  { value: 'cancel', label: '普通 Cancel' },
  { value: 'force-cancel', label: '强制 Cancel' },
]

function WorkflowActions() {
  const queryClient = useQueryClient()
  const [action, setAction] = useState<WorkflowAction>('dispatch')
  const [workflowPath, setWorkflowPath] = useState<string>()
  const [ref, setRef] = useState('main')
  const [runId, setRunId] = useState<number>()
  const [inputs, setInputs] = useState('')
  const [output, setOutput] = useState('')
  const [outputError, setOutputError] = useState(false)
  const [runUrl, setRunUrl] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  const permissionsQuery = useQuery({
    queryKey: ['workflow-action-permissions'],
    queryFn: getMyPermissions,
  })
  const logsQuery = useQuery({
    queryKey: ['workflow-action-logs', page],
    queryFn: () => getWorkflowActionLogs(page),
  })
  const executeMutation = useMutation({
    mutationFn: executeWorkflowAction,
    onSuccess: (result) => {
      setOutput(result.output)
      setOutputError(!result.success)
      setRunUrl(result.run_url)
      void queryClient.invalidateQueries({
        queryKey: ['workflow-action-logs'],
      })
    },
    onError: (error) => {
      setOutput(getWorkflowActionError(error))
      setOutputError(true)
      setRunUrl(null)
    },
  })

  const execute = () => {
    if (
      !workflowPath ||
      (action === 'dispatch' && !ref.trim()) ||
      (action !== 'dispatch' && !runId)
    ) {
      return
    }
    executeMutation.mutate({
      action,
      workflow_path: workflowPath,
      ref,
      inputs_yaml: inputs,
      run_id: action === 'dispatch' ? undefined : runId,
    })
  }

  const handleExecute = () => {
    if (action === 'force-cancel') {
      Modal.confirm({
        title: '确认强制取消该 Run？',
        content: '强制取消可能绕过 always() 等后续步骤，请确认 Run ID 无误。',
        okText: '强制取消',
        okButtonProps: { danger: true },
        cancelText: '返回',
        onOk: execute,
      })
      return
    }
    execute()
  }

  const hasPermissions = (permissionsQuery.data?.length || 0) > 0
  const canSubmit =
    hasPermissions &&
    !!workflowPath &&
    (action === 'dispatch' ? !!ref.trim() : !!runId)

  return (
    <div className="stripe-page-container">
      <div className="stripe-page-header">
        <div>
          <h1 className="stripe-page-title">Actions 操作</h1>
          <p className="stripe-page-subtitle">
            提交工作流命令并查看本次 GitHub 返回结果
          </p>
        </div>
      </div>

      <Card title="执行命令" style={{ marginBottom: 24 }}>
        {permissionsQuery.isError && (
          <Alert
            type="error"
            showIcon
            message="工作流权限加载失败"
            action={
              <Button size="small" onClick={() => permissionsQuery.refetch()}>
                重试
              </Button>
            }
            style={{ marginBottom: 16 }}
          />
        )}
        {!hasPermissions &&
        !permissionsQuery.isLoading &&
        !permissionsQuery.isError ? (
          <Alert
            type="warning"
            showIcon
            message="未获手动操作授权"
            description="你没有任何工作流的操作权限，请联系管理员授权。"
          />
        ) : (
          <Form layout="vertical" onFinish={handleExecute}>
            <Space align="start" size="large" wrap style={{ width: '100%' }}>
              <Form.Item label="操作" required style={{ minWidth: 220 }}>
                <Select
                  value={action}
                  options={actionOptions}
                  onChange={(value) => setAction(value)}
                />
              </Form.Item>
              <Form.Item
                label="已授权工作流"
                required
                style={{ minWidth: 420 }}
              >
                <Select
                  value={workflowPath}
                  placeholder="选择工作流"
                  loading={permissionsQuery.isLoading}
                  options={(permissionsQuery.data || []).map((path) => ({
                    value: path,
                    label: path,
                  }))}
                  onChange={(value) => {
                    setWorkflowPath(value)
                    setInputs('')
                  }}
                />
              </Form.Item>
              {action === 'dispatch' ? (
                <Form.Item
                  label="Ref（分支或 Tag）"
                  required
                  style={{ minWidth: 220 }}
                >
                  <Input
                    value={ref}
                    onChange={(event) => setRef(event.target.value)}
                  />
                </Form.Item>
              ) : (
                <Form.Item label="Run ID" required style={{ minWidth: 220 }}>
                  <InputNumber
                    min={1}
                    precision={0}
                    value={runId}
                    onChange={(value) => setRunId(value || undefined)}
                    style={{ width: '100%' }}
                    placeholder="从 GitHub Actions 页面获取"
                  />
                </Form.Item>
              )}
            </Space>
            {action === 'dispatch' && workflowPath && (
              <details key={workflowPath} style={{ marginBottom: 16 }}>
                <summary>Inputs YAML（可选）</summary>
                <Form.Item extra="填写参数的 default 值。留空时使用工作流的默认参数。">
                  <YamlInputsEditor value={inputs} onChange={setInputs} />
                </Form.Item>
              </details>
            )}
            <Button
              type="primary"
              htmlType="submit"
              icon={<PlayCircleOutlined />}
              loading={executeMutation.isPending}
              disabled={!canSubmit}
            >
              执行
            </Button>
          </Form>
        )}

        {output && (
          <div style={{ marginTop: 24 }}>
            <Alert
              type={outputError ? 'error' : 'success'}
              showIcon
              message="本次返回"
              style={{ marginBottom: 8 }}
            />
            <pre
              style={{
                marginTop: 8,
                padding: 16,
                background: '#f6f8fc',
                borderRadius: 8,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {output}
            </pre>
            {runUrl && (
              <a href={runUrl} target="_blank" rel="noreferrer">
                在 GitHub 中查看 Run
              </a>
            )}
          </div>
        )}
      </Card>

      <WorkflowActionSchedules />

      <Card title="公共操作日志">
        <Table
          rowKey="id"
          loading={logsQuery.isLoading}
          dataSource={logsQuery.data?.items || []}
          columns={[
            { title: '触发者', dataIndex: 'username', width: 160 },
            {
              title: '触发时间',
              dataIndex: 'created_at',
              width: 200,
              render: (value: string) => formatTimezone(value),
            },
            {
              title: '执行的命令',
              dataIndex: 'command_text',
              render: (value: string) => (
                <code
                  style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                >
                  {value}
                </code>
              ),
            },
          ]}
          pagination={{
            current: page,
            pageSize: 20,
            total: logsQuery.data?.total || 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
        />
      </Card>
    </div>
  )
}

export default WorkflowActions
