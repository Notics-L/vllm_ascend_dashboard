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

const COUNTRY_CODES = [
  { value: '+86', label: '🇨🇳 +86' },
  { value: '+852', label: '🇭🇰 +852' },
  { value: '+853', label: '🇲🇴 +853' },
  { value: '+886', label: '🇹🇼 +886' },
  { value: '+1', label: '🇺🇸 +1' },
  { value: '+81', label: '🇯🇵 +81' },
  { value: '+82', label: '🇰🇷 +82' },
  { value: '+65', label: '🇸🇬 +65' },
  { value: '+44', label: '🇬🇧 +44' },
  { value: '+49', label: '🇩🇪 +49' },
  { value: '+33', label: '🇫🇷 +33' },
  { value: '+61', label: '🇦🇺 +61' },
  { value: '+91', label: '🇮🇳 +91' },
]

const DIAL_CODES = COUNTRY_CODES.map((c) => c.value).sort((a, b) => b.length - a.length)

function splitPhone(phone?: string | null): { code: string; number: string } {
  if (!phone) return { code: '+86', number: '' }
  const code = DIAL_CODES.find((item) => phone.startsWith(item)) || '+86'
  return { code, number: phone.slice(code.length) }
}

function formatPhoneText(phone?: string | null): string {
  if (!phone) return '-'
  const { code, number } = splitPhone(phone)
  if (code === '+86' && number.length === 11) {
    return `${code} ${number.slice(0, 3)} ${number.slice(3, 7)} ${number.slice(7)}`
  }
  return `${code} ${number.replace(/(\d{4})(?=\d)/g, '$1 ')}`.trim()
}

function PhoneCell({ phone }: { phone?: string | null }) {
  if (!phone) return <Text type="secondary">-</Text>
  const { code, number } = splitPhone(phone)
  const formatted = code === '+86' && number.length === 11
    ? `${number.slice(0, 3)} ${number.slice(3, 7)} ${number.slice(7)}`
    : number.replace(/(\d{4})(?=\d)/g, '$1 ')
  return (
    <span className="nc-phone">
      <span className="nc-phone-code">{code}</span>
      <span className="nc-phone-number">{formatted}</span>
    </span>
  )
}

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
    const phoneParts = splitPhone(row?.phone)
    contactForm.setFieldsValue(row ? {
      name: row.name,
      email: row.email,
      phone_country_code: phoneParts.code,
      phone_number: phoneParts.number,
      group_ids: row.group_ids,
      enabled: row.enabled,
    } : { enabled: true, group_ids: [], phone_country_code: '+86', phone_number: '' })
    setContactOpen(true)
  }

  const submitContact = async () => {
    const values = await contactForm.validateFields()
    const phoneNumber = (values.phone_number || '').replace(/[\s-]/g, '')
    const phone = phoneNumber ? `${values.phone_country_code || '+86'}${phoneNumber}` : null
    if (!values.email && !phone) {
      message.error('邮箱和手机号至少填写一个')
      return
    }
    const payload: ContactPayload = {
      name: values.name,
      email: values.email || null,
      phone,
      group_ids: values.group_ids || [],
      enabled: values.enabled,
    }
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
        className="nc-panel"
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
            { title: '手机号', dataIndex: 'phone', width: 200, render: (v) => <PhoneCell phone={v} /> },
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
        className="nc-panel"
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
          <Form.Item label="手机号" extra="选择国家/地区代码后填写手机号，系统自动保存为 E.164 格式">
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="phone_country_code" noStyle>
                <Select
                  style={{ width: 120 }}
                  options={COUNTRY_CODES}
                  showSearch
                  optionFilterProp="label"
                />
              </Form.Item>
              <Form.Item
                name="phone_number"
                noStyle
                rules={[{ pattern: /^[0-9\s-]*$/, message: '手机号只能包含数字' }]}
              >
                <Input placeholder="13800000000" style={{ flex: 1 }} />
              </Form.Item>
            </Space.Compact>
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
              options={contacts.map((c) => ({ value: c.id, label: `${c.name}${c.phone ? ` · ${formatPhoneText(c.phone)}` : ''}` }))}
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
