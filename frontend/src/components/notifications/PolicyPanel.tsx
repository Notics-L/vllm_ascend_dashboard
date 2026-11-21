import { useEffect } from 'react'
import {
  Alert, Button, Card, Divider, Form, Input, InputNumber, Select, Space, Switch, message,
} from 'antd'
import { SaveOutlined, SettingOutlined } from '@ant-design/icons'
import { useGroups, usePolicy, useUpdatePolicy } from '../../hooks/useNotifications'
import type { NotificationPolicy } from '../../services/notifications'

export default function PolicyPanel() {
  const { data, isLoading, refetch } = usePolicy()
  const groupsQuery = useGroups()
  const updatePolicy = useUpdatePolicy()
  const [form] = Form.useForm()
  const groups = groupsQuery.data ?? []

  useEffect(() => {
    if (data) form.setFieldsValue(data)
  }, [data, form])

  const save = async () => {
    const values = await form.validateFields()
    try {
      await updatePolicy.mutateAsync(values as Partial<NotificationPolicy>)
      message.success('通知策略已保存')
      await refetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  return (
    <Card
      size="small"
      title={<Space><SettingOutlined /><span>通知策略</span></Space>}
      extra={<Button type="primary" size="small" icon={<SaveOutlined />} loading={updatePolicy.isPending} onClick={save}>保存策略</Button>}
      loading={isLoading}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="策略用于控制静默时段、重复提醒冷却、告警风暴抑制和失败升级。"
      />
      <Form form={form} layout="vertical">
        <Divider orientation="left" plain>静默时段</Divider>
        <Space size={24} wrap>
          <Form.Item name="quiet_hours_enabled" label="启用静默" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="quiet_hours_start" label="开始时间" style={{ marginBottom: 12 }}>
            <Input placeholder="22:00" style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="quiet_hours_end" label="结束时间" style={{ marginBottom: 12 }}>
            <Input placeholder="08:00" style={{ width: 120 }} />
          </Form.Item>
        </Space>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="静默时段内仍发送邮件，但会跳过语音电话和短信。" />

        <Divider orientation="left" plain>冷却与风暴抑制</Divider>
        <Space size={24} wrap>
          <Form.Item name="cooldown_minutes" label="同事件冷却（分钟）" tooltip="相同 dedupe_key 在冷却期内跳过语音/短信" style={{ marginBottom: 12 }}>
            <InputNumber min={0} max={1440} />
          </Form.Item>
          <Form.Item name="storm_window_minutes" label="风暴统计窗口（分钟）" style={{ marginBottom: 12 }}>
            <InputNumber min={1} max={1440} />
          </Form.Item>
          <Form.Item name="storm_threshold" label="风暴阈值（事件数）" style={{ marginBottom: 12 }}>
            <InputNumber min={1} max={1000} />
          </Form.Item>
        </Space>

        <Divider orientation="left" plain>升级链</Divider>
        <Space size={24} wrap>
          <Form.Item name="escalation_enabled" label="启用失败升级" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="oncall_group_id" label="当前值班组" style={{ minWidth: 220, marginBottom: 12 }}>
            <Select allowClear placeholder="选择联系人组" options={groups.map((g) => ({ value: g.id, label: g.name }))} />
          </Form.Item>
        </Space>
        <Form.Item name="escalation_group_ids" label="升级联系人组" extra="主渠道全部失败后，使用这些联系人组继续尝试语音/短信/邮件">
          <Select mode="multiple" placeholder="选择升级组" options={groups.map((g) => ({ value: g.id, label: g.name }))} />
        </Form.Item>
      </Form>
    </Card>
  )
}
