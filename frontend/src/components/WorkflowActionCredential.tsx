import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Input,
  Popconfirm,
  Space,
  Tag,
  message,
} from 'antd'
import {
  clearActionsCredential,
  getActionsCredentialStatus,
  getWorkflowActionError,
  saveActionsCredential,
  testActionsCredential,
} from '../services/workflowActions'
import { formatTimezone } from '../utils/timezone'

function WorkflowActionCredential() {
  const queryClient = useQueryClient()
  const [token, setToken] = useState('')
  const [output, setOutput] = useState('')
  const [outputError, setOutputError] = useState(false)
  const statusQuery = useQuery({
    queryKey: ['workflow-action-credential'],
    queryFn: getActionsCredentialStatus,
  })
  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: ['workflow-action-credential'],
    })
  }
  const saveMutation = useMutation({
    mutationFn: saveActionsCredential,
    onSuccess: () => {
      setToken('')
      setOutput('')
      setOutputError(false)
      message.success('Actions 专用 Token 已加密保存')
      invalidate()
    },
    onError: (error) => {
      setOutput(getWorkflowActionError(error))
      setOutputError(true)
    },
  })
  const clearMutation = useMutation({
    mutationFn: clearActionsCredential,
    onSuccess: () => {
      setOutput('')
      setOutputError(false)
      message.success('Actions 专用 Token 已清除')
      invalidate()
    },
    onError: (error) => {
      setOutput(getWorkflowActionError(error))
      setOutputError(true)
    },
  })
  const testMutation = useMutation({
    mutationFn: testActionsCredential,
    onSuccess: (result) => {
      setOutput(result.output)
      setOutputError(!result.success)
    },
    onError: (error) => {
      setOutput(getWorkflowActionError(error))
      setOutputError(true)
    },
  })
  const configured = !!statusQuery.data?.configured

  return (
    <Card title="Actions GitHub Token" style={{ marginBottom: 24 }}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {statusQuery.isError && (
          <Alert
            type="error"
            showIcon
            message="Token 配置状态加载失败"
            action={
              <Button size="small" onClick={() => statusQuery.refetch()}>
                重试
              </Button>
            }
          />
        )}
        <Space wrap>
          {!statusQuery.isError &&
            (configured ? (
              <Tag color="success">已配置</Tag>
            ) : (
              <Tag>未配置</Tag>
            ))}
          {statusQuery.data?.updated_at && (
            <span style={{ color: '#52627a' }}>
              更新时间：{formatTimezone(statusQuery.data.updated_at)}
            </span>
          )}
        </Space>
        <Space align="start" wrap>
          <Input.Password
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={
              configured
                ? '输入新 Token 可替换现有配置'
                : '输入 Actions 专用 GitHub Token'
            }
            autoComplete="new-password"
            style={{ width: 480, maxWidth: '100%' }}
          />
          <Button
            type="primary"
            loading={saveMutation.isPending}
            disabled={token.trim().length < 10}
            onClick={() => saveMutation.mutate(token.trim())}
          >
            {configured ? '替换 Token' : '保存 Token'}
          </Button>
          <Button
            loading={testMutation.isPending}
            disabled={!configured}
            onClick={() => testMutation.mutate()}
          >
            测试连接
          </Button>
          <Popconfirm
            title="确定清除 Actions 专用 Token？"
            okText="清除"
            cancelText="取消"
            onConfirm={() => clearMutation.mutate()}
          >
            <Button
              danger
              disabled={!configured}
              loading={clearMutation.isPending}
            >
              清除
            </Button>
          </Popconfirm>
        </Space>
        <span style={{ color: '#52627a' }}>
          Token 仅用于 Actions 操作，加密后存入
          MySQL；页面不会读取已保存的明文或密文。
        </span>
        {output && (
          <Alert
            type={outputError ? 'error' : 'info'}
            showIcon
            message={<span style={{ whiteSpace: 'pre-wrap' }}>{output}</span>}
          />
        )}
      </Space>
    </Card>
  )
}

export default WorkflowActionCredential
