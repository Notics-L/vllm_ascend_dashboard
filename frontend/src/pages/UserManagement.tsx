import { useState } from 'react'
import {
  Table,
  Card,
  Button,
  Tag,
  Space,
  Modal,
  Form,
  Input,
  Select,
  Switch,
  message,
  Popconfirm,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  UserOutlined,
  LockOutlined,
} from '@ant-design/icons'
import type { FormProps } from 'antd'
import { useUsers, useCreateUser, useUpdateUser, useDeleteUser } from '../hooks/useUsers'
import type { User, UserCreate, UserUpdate } from '../services/users'
import { formatTimezone } from '../utils/timezone'
import ResetPasswordModal from '../components/ResetPasswordModal'
import { useCurrentUser } from '../hooks/useCurrentUser'

type FieldType = {
  username?: string
  email?: string
  password?: string
  role?: string
  is_active?: boolean
}

function UserManagement() {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [resetPasswordModalOpen, setResetPasswordModalOpen] = useState(false)
  const [resetPasswordUserId, setResetPasswordUserId] = useState<number | null>(null)
  const [resetPasswordUsername, setResetPasswordUsername] = useState<string>('')
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [form] = Form.useForm()

  const { data: users, isLoading } = useUsers()
  const { data: currentUser } = useCurrentUser()
  const createMutation = useCreateUser()
  const updateMutation = useUpdateUser()
  const deleteMutation = useDeleteUser()

  const isSuperAdmin = currentUser?.role === 'super_admin'

  // 打开新建用户弹窗
  const handleCreate = () => {
    setEditingUser(null)
    form.resetFields()
    setIsModalOpen(true)
  }

  // 打开编辑用户弹窗
  const handleEdit = (user: User) => {
    setEditingUser(user)
    form.setFieldsValue({
      username: user.username,
      email: user.email,
      role: user.role,
      is_active: user.is_active,
    })
    setIsModalOpen(true)
  }

  // 打开重置密码弹窗
  const handleResetPassword = (user: User) => {
    setResetPasswordUserId(user.id)
    setResetPasswordUsername(user.username)
    setResetPasswordModalOpen(true)
  }

  // 删除用户
  const handleDelete = (userId: number) => {
    deleteMutation.mutate(userId, {
      onSuccess: (_data) => {
        message.success('用户已删除')
      },
      onError: (error: any) => {
        console.error('Delete error:', error)
        const errorMsg = error.response?.data?.detail || error.message || '删除失败'
        message.error(errorMsg)
      },
    })
  }

  // 提交表单
  const onFinish: FormProps<FieldType>['onFinish'] = (values) => {
    if (editingUser) {
      // 更新用户
      const updateData: UserUpdate = {
        email: values.email,
        role: values.role,
        is_active: values.is_active,
      }
      updateMutation.mutate(
        { userId: editingUser.id, data: updateData },
        {
          onSuccess: () => {
            message.success('用户已更新')
            setIsModalOpen(false)
            form.resetFields()
          },
          onError: (error: any) => {
            message.error(error.response?.data?.detail || '更新失败')
          },
        }
      )
    } else {
      // 创建用户
      const createData: UserCreate = {
        username: values.username!,
        email: values.email,
        password: values.password!,
        role: values.role,
      }
      createMutation.mutate(createData, {
        onSuccess: () => {
          message.success('用户已创建')
          setIsModalOpen(false)
          form.resetFields()
        },
        onError: (error: any) => {
          message.error(error.response?.data?.detail || '创建失败')
        },
      })
    }
  }

  // 新建用户表单字段
  const CreateUserFields = () => (
    <>
      <Form.Item<FieldType>
        label="用户名"
        name="username"
        rules={[
          { required: true, message: '请输入用户名' },
          { min: 3, message: '用户名至少 3 个字符' },
        ]}
      >
        <Input placeholder="请输入用户名" />
      </Form.Item>

      <Form.Item<FieldType>
        label="密码"
        name="password"
        rules={[
          { required: true, message: '请输入密码' },
          { min: 6, message: '密码至少 6 个字符' },
        ]}
      >
        <Input.Password placeholder="请输入密码" />
      </Form.Item>
    </>
  )

  // 角色标签
  const renderRoleTag = (role: string) => {
    const roleMap: Record<string, { color: string; text: string }> = {
      super_admin: { color: 'red', text: '超级管理员' },
      admin: { color: 'orange', text: '管理员' },
      user: { color: 'blue', text: '普通用户' },
    }
    const config = roleMap[role] || { color: 'default', text: role }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  // 状态标签
  const renderStatusTag = (isActive: boolean) => {
    return isActive ? (
      <Tag color="success">正常</Tag>
    ) : (
      <Tag color="default">禁用</Tag>
    )
  }

  // 表格列定义
  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (text: string) => (
        <span>
          <UserOutlined style={{ marginRight: 8 }} />
          {text}
        </span>
      ),
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (email: string | null) => email || '-',
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      render: renderRoleTag,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: renderStatusTag,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (date: string) => formatTimezone(date),
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_: unknown, record: User) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
            disabled={!isSuperAdmin && record.role === 'super_admin'}
          >
            编辑
            {!isSuperAdmin && record.role === 'super_admin' && ' (无权限)'}
          </Button>
          {/* 超级管理员之间不能互相重置密码 */}
          {record.role !== 'super_admin' && (
            <Button
              type="link"
              size="small"
              icon={<LockOutlined />}
              onClick={() => handleResetPassword(record)}
            >
              重置密码
            </Button>
          )}
          {record.role !== 'super_admin' && (
            <Popconfirm
              title="确定要删除此用户吗？"
              onConfirm={() => handleDelete(record.id)}
              okText="确定"
              cancelText="取消"
              okButtonProps={{ loading: deleteMutation.isPending }}
            >
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deleteMutation.isPending}
              >
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="stripe-page-container">
      {/* 页面标题 */}
      <div className="stripe-page-header">
        <h1 className="stripe-page-title" style={{ margin: 0, fontSize: 24, fontWeight: 400 }}>用户管理</h1>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={handleCreate}
          className="stripe-btn-primary"
        >
          新建用户
        </Button>
      </div>

      {/* 用户列表 */}
      <Card>
        <Table
          columns={columns}
          dataSource={users}
          loading={isLoading}
          rowKey="id"
          pagination={{
            pageSize: 20,
            showSizeChanger: false,
          }}
        />
      </Card>

      {/* 新建/编辑用户弹窗 */}
      <Modal
        title={editingUser ? '编辑用户' : '新建用户'}
        open={isModalOpen}
        onCancel={() => {
          setIsModalOpen(false)
          form.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={onFinish}
          autoComplete="off"
        >
          {!editingUser && <CreateUserFields />}

          <Form.Item<FieldType>
            label="邮箱"
            name="email"
            rules={[
              { type: 'email', message: '请输入有效的邮箱地址' },
            ]}
          >
            <Input placeholder="请输入邮箱（可选）" />
          </Form.Item>

          <Form.Item<FieldType>
            label="角色"
            name="role"
            rules={[{ required: true, message: '请选择角色' }]}
            initialValue="user"
          >
            <Select disabled={!isSuperAdmin && editingUser?.role === 'super_admin'}>
              <Select.Option value="user">普通用户</Select.Option>
              <Select.Option value="admin">管理员</Select.Option>
              {isSuperAdmin && <Select.Option value="super_admin">超级管理员</Select.Option>}
            </Select>
          </Form.Item>

          <Form.Item<FieldType>
            label="状态"
            name="is_active"
            valuePropName="checked"
            initialValue={true}
          >
            <Switch checkedChildren="正常" unCheckedChildren="禁用" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button
                onClick={() => {
                  setIsModalOpen(false)
                  form.resetFields()
                }}
              >
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={createMutation.isPending || updateMutation.isPending}>
                {editingUser ? '保存' : '创建'}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码弹窗 */}
      <ResetPasswordModal
        open={resetPasswordModalOpen}
        userId={resetPasswordUserId}
        username={resetPasswordUsername}
        onClose={() => setResetPasswordModalOpen(false)}
        onSuccess={() => {}}
      />
    </div>
  )
}

export default UserManagement
