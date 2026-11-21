import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Switch,
  Table,
  Tag,
  message,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import {
  createAdminPermission,
  getAdminPermissions,
  getWorkflowActionError,
  updateUserPermissions,
  type WorkflowActionPermission,
} from '../services/workflowActions'
import { formatTimezone } from '../utils/timezone'
import WorkflowActionCredential from './WorkflowActionCredential'

interface PermissionForm {
  usernames?: string
  paths?: string
  schedulePermission: boolean
}

const entries = (text = '', separator = /\r?\n/) => [
  ...new Set(
    text
      .split(separator)
      .map((entry) => entry.trim())
      .filter(Boolean),
  ),
]

function WorkflowActionPermissions() {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<PermissionForm>()
  const [editing, setEditing] = useState<
    WorkflowActionPermission | 'batch' | null
  >(null)
  const permissions = useQuery({
    queryKey: ['workflow-action-admin-permissions'],
    queryFn: getAdminPermissions,
  })
  const save = useMutation({
    mutationFn: async ({
      usernames,
      paths,
      schedulePermission,
    }: PermissionForm) => {
      const workflowPaths = entries(paths)
      if (editing === 'batch') {
        const result = await createAdminPermission({
          usernames: entries(usernames, /[\r\n,]+/),
          workflow_paths: workflowPaths,
          schedule_permission: schedulePermission,
        })
        return `新增 ${result.created_count} 条工作流授权，${result.schedule_created_count} 人获得定时权限`
      }
      if (editing)
        await updateUserPermissions(
          editing.username,
          workflowPaths,
          schedulePermission,
        )
      return '用户授权已更新'
    },
    onSuccess: (notice) => {
      message.success(notice)
      setEditing(null)
      for (const key of [
        'workflow-action-admin-permissions',
        'workflow-action-permissions',
        'workflow-action-schedule-permission',
      ]) {
        void queryClient.invalidateQueries({ queryKey: [key] })
      }
    },
  })

  const open = (record: WorkflowActionPermission | 'batch') => {
    save.reset()
    form.setFieldsValue({
      usernames: '',
      paths: record === 'batch' ? '' : record.workflow_paths.join('\n'),
      schedulePermission: record !== 'batch' && record.schedule_permission,
    })
    setEditing(record)
  }

  return (
    <>
      <WorkflowActionCredential />
      <Card
        title="Actions 权限"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => open('batch')}
          >
            批量授权
          </Button>
        }
      >
        {permissions.isError && (
          <Alert
            type="error"
            showIcon
            message="授权列表加载失败"
            action={<Button onClick={() => permissions.refetch()}>重试</Button>}
          />
        )}
        <Table
          rowKey="username"
          loading={permissions.isLoading}
          dataSource={permissions.data ?? []}
          columns={[
            { title: '用户', dataIndex: 'username' },
            {
              title: '工作流路径',
              dataIndex: 'workflow_paths',
              render: (paths: string[]) =>
                paths.length
                  ? paths.map((path) => (
                      <div key={path} style={{ overflowWrap: 'anywhere' }}>
                        {path}
                      </div>
                    ))
                  : '—',
            },
            {
              title: '全仓库定时权限',
              dataIndex: 'schedule_permission',
              render: (allowed: boolean) => (
                <Tag color={allowed ? 'blue' : undefined}>
                  {allowed ? '已授权' : '未授权'}
                </Tag>
              ),
            },
            { title: '授权人', dataIndex: 'granted_by' },
            {
              title: '最近授权时间',
              dataIndex: 'created_at',
              render: (value: string) => formatTimezone(value),
            },
            {
              title: '操作',
              render: (_, record) => (
                <Button type="link" onClick={() => open(record)}>
                  编辑
                </Button>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        title={
          editing === 'batch'
            ? '批量授权'
            : `编辑 ${editing?.username ?? ''} 的授权`
        }
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        okText={editing === 'batch' ? '添加授权' : '保存'}
        cancelText="取消"
        forceRender
      >
        {save.isError && (
          <Alert
            type="error"
            showIcon
            message={getWorkflowActionError(save.error)}
          />
        )}
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => save.mutate(values)}
        >
          {editing === 'batch' && (
            <Form.Item
              name="usernames"
              label="用户名"
              rules={[{ required: true, whitespace: true }]}
            >
              <Input.TextArea
                rows={3}
                placeholder="每行一个用户名，也可用逗号分隔"
              />
            </Form.Item>
          )}
          <Form.Item
            name="paths"
            label="手动操作的工作流路径"
            extra={
              editing === 'batch'
                ? '每行填写一个路径。'
                : '每行填写一个路径。清空后保存将撤销全部手动操作授权。'
            }
          >
            <Input.TextArea
              rows={4}
              placeholder=".github/workflows/example.yaml"
            />
          </Form.Item>
          <Form.Item
            name="schedulePermission"
            label="全仓库定时权限"
            valuePropName="checked"
            extra="可为仓库内任意工作流创建定时任务，也可管理和立即触发已有任务。撤销后，已启用的任务会继续运行。"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

export default WorkflowActionPermissions
