import { useState } from 'react'
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons'
import {
  useContacts, useDeleteContact, useDeleteGroup, useGroups, useSaveContact, useSaveGroup,
} from '../../hooks/useNotifications'
import type { ContactPayload, GroupPayload, NotificationContact, NotificationGroup } from '../../services/notifications'

const { Text } = Typography

export default function ContactsPanel() {
  const contactsQuery = useContacts()
  const groupsQuery = useGroups()
  const saveContact = useSaveContact()
  const deleteContact = useDeleteContact()
  const saveGroup = useSaveGroup()
  const deleteGroup = useDeleteGroup()
  const [contactOpen, setContactOpen] = useState(false)
  const [editingContact, setEditingContact] = useState<NotificationContact | null>(null)
  const [groupOpen, setGroupOpen] = useState(false)
  const [editingGroup, setEditingGroup] = useState<NotificationGroup | null>(null)
  const [contactForm] = Form.useForm()
  const [groupForm] = Form.useForm()

  const contacts = contactsQuery.data ?? []
  const groups = groupsQuery.data ?? []

  const openContact = (row?: NotificationContact) => {
    setEditingContact(row ?? null)
    contactForm.resetFields()
    contactForm.setFieldsValue(row ? {
      name: row.name, email: row.email, phone: row.phone, group_ids: row.group_ids, enabled: row.enabled,
    } : { enabled: true, group_ids: [] })
    setContactOpen(true)
  }

  const submitContact = async () => {
    const values = await contactForm.validateFields()
    if (!values.email && !values.phone) {
      message.error('邮箱和手机号至少填写一个')
      return
    }
    const payload: ContactPayload = { ...values, email: values.email || null, phone: values.phone || null }
    try {
      await saveContact.mutateAsync({ id: editingContact?.id, data: payload })
      message.success(editingContact ? '联系人已更新' : '联系人已创建')
      setContactOpen(false)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  const openGroup = (row?: NotificationGroup) => {
    setEditingGroup(row ?? null)
    groupForm.resetFields()
    groupForm.setFieldsValue(row ? {
      name: row.name, description: row.description, contact_ids: row.contact_ids, enabled: row.enabled,
    } : { enabled: true, contact_ids: [] })
    setGroupOpen(true)
  }

  const submitGroup = async () => {
    const values = await groupForm.validateFields()
    const payload: GroupPayload = { ...values, description: values.description || null }
    try {
      await saveGroup.mutateAsync({ id: editingGroup?.id, data: payload })
      message.success(editingGroup ? '联系人组已更新' : '联系人组已创建')
      setGroupOpen(false)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  return (
    <div>
      <Card
        size="small"
        title={<Space><UserOutlined /><span>联系人</span></Space>}
        extra={<Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => openContact()}>新增联系人</Button>}
        style={{ marginBottom: 16 }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="联系人用于后续短信与语音电话通知，请至少填写邮箱或手机号。"
        />
        <Table
          rowKey="id"
          size="small"
          loading={contactsQuery.isLoading}
          dataSource={contacts}
          pagination={false}
          columns={[
            { title: '姓名', dataIndex: 'name', width: 140, render: (v) => <Text strong>{v}</Text> },
            { title: '邮箱', dataIndex: 'email', render: (v) => v || '-' },
            { title: '手机号', dataIndex: 'phone', width: 150, render: (v) => v || '-' },
            {
              title: '所属组', dataIndex: 'group_ids', width: 220,
              render: (ids: number[]) => (
                <Space size={4} wrap>
                  {(ids || []).map((id) => {
                    const group = groups.find((g) => g.id === id)
                    return <Tag key={id}>{group?.name || id}</Tag>
                  })}
                  {!ids?.length && <Text type="secondary">-</Text>}
                </Space>
              ),
            },
            {
              title: '状态', dataIndex: 'enabled', width: 80,
              render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag>,
            },
            {
              title: '操作', key: 'actions', width: 140,
              render: (_, row) => (
                <Space size={2}>
                  <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openContact(row)}>编辑</Button>
                  <Popconfirm title="确认删除该联系人？" onConfirm={async () => {
                    await deleteContact.mutateAsync(row.id)
                    message.success('联系人已删除')
                  }}>
                    <Button size="small" type="link" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card
        size="small"
        title={<Space><TeamOutlined /><span>联系人组</span></Space>}
        extra={<Button size="small" icon={<PlusOutlined />} onClick={() => openGroup()}>新增联系人组</Button>}
      >
        <Table
          rowKey="id"
          size="small"
          loading={groupsQuery.isLoading}
          dataSource={groups}
          pagination={false}
          columns={[
            { title: '组名', dataIndex: 'name', width: 180, render: (v) => <Text strong>{v}</Text> },
            { title: '描述', dataIndex: 'description', render: (v) => v || '-' },
            {
              title: '成员', dataIndex: 'contacts', width: 260,
              render: (members: NotificationGroup['contacts']) => (
                <Space size={4} wrap>
                  {members.length ? members.map((m) => <Tag key={m.id}>{m.name}</Tag>) : <Text type="secondary">暂无成员</Text>}
                </Space>
              ),
            },
            {
              title: '状态', dataIndex: 'enabled', width: 80,
              render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag>,
            },
            {
              title: '操作', key: 'actions', width: 140,
              render: (_, row) => (
                <Space size={2}>
                  <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openGroup(row)}>编辑</Button>
                  <Popconfirm title="确认删除该联系人组？" onConfirm={async () => {
                    await deleteGroup.mutateAsync(row.id)
                    message.success('联系人组已删除')
                  }}>
                    <Button size="small" type="link" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editingContact ? '编辑联系人' : '新增联系人'}
        open={contactOpen}
        onCancel={() => setContactOpen(false)}
        onOk={submitContact}
        confirmLoading={saveContact.isPending}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={contactForm} layout="vertical">
          <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
            <Input placeholder="例如：张三" />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
            <Input placeholder="owner@example.com" />
          </Form.Item>
          <Form.Item name="phone" label="手机号" extra="建议使用 E.164 格式，例如 +8613800000000">
            <Input placeholder="+8613800000000" />
          </Form.Item>
          <Form.Item name="group_ids" label="所属联系人组">
            <Select mode="multiple" placeholder="可选" options={groups.map((g) => ({ value: g.id, label: g.name }))} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingGroup ? '编辑联系人组' : '新增联系人组'}
        open={groupOpen}
        onCancel={() => setGroupOpen(false)}
        onOk={submitGroup}
        confirmLoading={saveGroup.isPending}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={groupForm} layout="vertical">
          <Form.Item name="name" label="组名" rules={[{ required: true, message: '请输入组名' }]}>
            <Input placeholder="例如：镜像构建值班组" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="用途说明" />
          </Form.Item>
          <Form.Item name="contact_ids" label="成员">
            <Select
              mode="multiple"
              placeholder="选择联系人"
              options={contacts.map((c) => ({ value: c.id, label: `${c.name}${c.phone ? ` · ${c.phone}` : ''}` }))}
            />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
