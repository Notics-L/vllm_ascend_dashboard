import { useEffect } from 'react'
import {
  Alert, Button, Card, Form, InputNumber, Select, Switch, TimePicker, message,
} from 'antd'
import {
  ClockCircleOutlined, SaveOutlined, SettingOutlined, ThunderboltOutlined, TeamOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { useGroups, usePolicy, useUpdatePolicy } from '../../hooks/useNotifications'
import type { NotificationPolicy } from '../../services/notifications'
import { PanelHeader, SectionCard, ToggleRow } from './ui'

const toTime = (value?: string) => (value ? dayjs(`2000-01-01 ${value}`) : null)

export default function PolicyPanel() {
  const { data, isLoading, refetch } = usePolicy()
  const groupsQuery = useGroups()
  const updatePolicy = useUpdatePolicy()
  const [form] = Form.useForm()
  const groups = groupsQuery.data ?? []

  useEffect(() => {
    if (data) {
      form.setFieldsValue({
        ...data,
        quiet_hours_start: toTime(data.quiet_hours_start),
        quiet_hours_end: toTime(data.quiet_hours_end),
      })
    }
  }, [data, form])

  const save = async () => {
    const values = await form.validateFields()
    const payload: Partial<NotificationPolicy> = {
      ...values,
      quiet_hours_start: values.quiet_hours_start ? values.quiet_hours_start.format('HH:mm') : '22:00',
      quiet_hours_end: values.quiet_hours_end ? values.quiet_hours_end.format('HH:mm') : '08:00',
    }
    try {
      await updatePolicy.mutateAsync(payload)
      message.success('通知策略已保存')
      await refetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  return (
    <Card className="nc-panel" styles={{ body: { padding: 20 } }} loading={isLoading}>
      <PanelHeader
        icon={<SettingOutlined />}
        title="通知策略"
        description="控制静默时段、重复提醒冷却、告警风暴抑制和失败升级。"
        actions={
          <Button type="primary" icon={<SaveOutlined />} loading={updatePolicy.isPending} onClick={save}>
            保存策略
          </Button>
        }
      />

      <Form form={form} layout="vertical">
        <SectionCard
          icon={<ClockCircleOutlined />}
          title="静默时段"
          description="静默期内仍发送邮件，但会跳过语音电话和短信。"
        >
          <div className="nc-form-grid">
            <div className="nc-col-4">
              <ToggleRow
                label="启用静默"
                description="按上海时区判断"
                control={<Form.Item name="quiet_hours_enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
            </div>
            <div className="nc-col-4">
              <Form.Item name="quiet_hours_start" label="开始时间" style={{ marginBottom: 0 }}>
                <TimePicker format="HH:mm" style={{ width: 140 }} />
              </Form.Item>
            </div>
            <div className="nc-col-4">
              <Form.Item name="quiet_hours_end" label="结束时间" style={{ marginBottom: 0 }}>
                <TimePicker format="HH:mm" style={{ width: 140 }} />
              </Form.Item>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          icon={<ThunderboltOutlined />}
          title="冷却与风暴抑制"
          description="避免同一事件重复打扰，并在告警风暴时自动降级为邮件。"
          tone="orange"
        >
          <div className="nc-form-grid">
            <div className="nc-col-4">
              <Form.Item
                name="cooldown_minutes"
                label="同事件冷却"
                tooltip="相同 dedupe_key 在冷却期内跳过语音/短信"
                style={{ marginBottom: 0 }}
              >
                <InputNumber min={0} max={1440} addonAfter="分钟" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="nc-col-4">
              <Form.Item name="storm_window_minutes" label="风暴统计窗口" style={{ marginBottom: 0 }}>
                <InputNumber min={1} max={1440} addonAfter="分钟" style={{ width: '100%' }} />
              </Form.Item>
            </div>
            <div className="nc-col-4">
              <Form.Item name="storm_threshold" label="风暴阈值" style={{ marginBottom: 0 }}>
                <InputNumber min={1} max={1000} addonAfter="事件" style={{ width: '100%' }} />
              </Form.Item>
            </div>
          </div>
        </SectionCard>

        <SectionCard
          icon={<TeamOutlined />}
          title="升级链"
          description="主渠道全部失败后，继续尝试升级联系人组。"
          tone="purple"
        >
          <div className="nc-form-grid">
            <div className="nc-col-4">
              <ToggleRow
                label="启用失败升级"
                description="失败后通知备用组"
                control={<Form.Item name="escalation_enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
            </div>
            <div className="nc-col-8">
              <Form.Item name="oncall_group_id" label="当前值班组" style={{ marginBottom: 14 }}>
                <Select allowClear placeholder="选择联系人组" style={{ maxWidth: 320 }} options={groups.map((g) => ({ value: g.id, label: g.name }))} />
              </Form.Item>
              <Form.Item
                name="escalation_group_ids"
                label="升级联系人组"
                extra="主渠道全部失败后，使用这些联系人组继续尝试语音/短信/邮件"
                style={{ marginBottom: 0 }}
              >
                <Select mode="multiple" placeholder="选择升级组" options={groups.map((g) => ({ value: g.id, label: g.name }))} />
              </Form.Item>
            </div>
          </div>
        </SectionCard>

        <Alert
          type="info"
          showIcon
          message="策略按上海时区生效，冷却与风暴抑制只影响语音和短信，邮件始终作为兜底渠道。"
        />
      </Form>
    </Card>
  )
}
