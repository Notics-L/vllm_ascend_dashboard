import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Divider, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography, message,
} from 'antd'
import { PhoneOutlined, SaveOutlined } from '@ant-design/icons'
import { useIvrActions, useTestVoice, useUpdateVoiceConfig, useVoiceConfig } from '../../hooks/useNotifications'
import type { VoiceConfigUpdate } from '../../services/notifications'

const { Text, Paragraph } = Typography

export default function VoicePanel() {
  const { data, isLoading, refetch } = useVoiceConfig()
  const updateConfig = useUpdateVoiceConfig()
  const testVoice = useTestVoice()
  const actionsQuery = useIvrActions()
  const [form] = Form.useForm()
  const [testPhones, setTestPhones] = useState('')
  const [testResult, setTestResult] = useState<string | null>(null)

  useEffect(() => {
    if (data) {
      form.setFieldsValue({
        ...data,
        access_key_secret: '',
        test_phones_text: (data.test_phones || []).join(', '),
      })
    }
  }, [data, form])

  const save = async () => {
    const values = await form.validateFields()
    const payload: VoiceConfigUpdate = {
      ...values,
      test_phones: String(values.test_phones_text || '')
        .split(/[,;\\s]+/)
        .map((item: string) => item.trim())
        .filter(Boolean),
    }
    delete (payload as Record<string, unknown>).test_phones_text
    try {
      await updateConfig.mutateAsync(payload)
      message.success('语音配置已保存')
      await refetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  const sendTest = async () => {
    const phones = testPhones.split(/[,;\\s]+/).map((item) => item.trim()).filter(Boolean)
    if (!phones.length) {
      message.error('请填写测试手机号')
      return
    }
    setTestResult(null)
    try {
      const result = await testVoice.mutateAsync({ phones, text: 'vLLM Ascend 通知服务语音测试' })
      setTestResult(result.success ? '测试语音已发起' : `测试失败：${result.error || '未知错误'}`)
      message.success(result.success ? '测试语音已发起' : '语音呼叫失败')
    } catch (e: any) {
      setTestResult(`测试失败：${e?.response?.data?.detail || e?.message || '未知错误'}`)
    }
  }

  return (
    <Card
      size="small"
      title={<Space><PhoneOutlined /><span>语音电话通道</span></Space>}
      extra={<Button type="primary" size="small" icon={<SaveOutlined />} loading={updateConfig.isPending} onClick={save}>保存配置</Button>}
      loading={isLoading}
    >
      <Alert
        type={data?.enabled ? 'success' : 'warning'}
        showIcon
        style={{ marginBottom: 16 }}
        message={data?.enabled ? '语音通道已启用' : '语音通道未启用'}
        description={
          data?.enabled
            ? `服务商：${data.provider}｜主叫：${data.called_show_number || '未配置'}｜TTS：${data.tts_code || '未配置'}`
            : '启用后 critical 告警将优先拨打电话播报，失败后自动降级短信/邮件。'
        }
      />
      <Form form={form} layout="vertical">
        <Space size={24} wrap>
          <Form.Item name="enabled" label="启用语音" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="fallback_enabled" label="启用降级" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="apply_to" label="适用级别" style={{ marginBottom: 12, minWidth: 160 }}>
            <Select options={[{ value: 'critical', label: '仅 critical' }, { value: 'all', label: 'critical + warning + info' }]} />
          </Form.Item>
          <Form.Item name="play_times" label="重复播报次数" style={{ marginBottom: 12 }}>
            <InputNumber min={1} max={5} />
          </Form.Item>
        </Space>
        <Divider orientation="left" plain>阿里云语音通知配置</Divider>
        <Form.Item name="provider" label="服务商">
          <Select
            options={[
              { value: 'aliyun', label: '阿里云语音通知' },
              { value: 'mock', label: 'Mock（本地联调，不真实呼叫）' },
            ]}
          />
        </Form.Item>
        <Space style={{ width: '100%' }} size="middle" wrap>
          <Form.Item name="access_key_id" label="AccessKey ID" style={{ minWidth: 260 }}>
            <Input placeholder="LTAI..." />
          </Form.Item>
          <Form.Item
            name="access_key_secret"
            label={<Space><span>AccessKey Secret</span>{data?.access_key_secret_set && <Tag color="green">已设置</Tag>}</Space>}
            extra="留空则不修改"
            style={{ minWidth: 320 }}
          >
            <Input.Password placeholder="输入 Secret" visibilityToggle />
          </Form.Item>
        </Space>
        <Space style={{ width: '100%' }} size="middle" wrap>
          <Form.Item name="called_show_number" label="主叫号码" style={{ minWidth: 200 }}>
            <Input placeholder="0571xxxxxxx" />
          </Form.Item>
          <Form.Item name="tts_code" label="TTS 模板 CODE" style={{ minWidth: 200 }}>
            <Input placeholder="TTS_xxxxxxx" />
          </Form.Item>
          <Form.Item name="tts_param_key" label="模板变量名" tooltip="模板中接收告警文本的变量，例如 content">
            <Input placeholder="content" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="region_id" label="Region">
            <Input placeholder="cn-hangzhou" style={{ width: 160 }} />
          </Form.Item>
        </Space>
        <Form.Item name="test_phones_text" label="默认测试手机号" extra="多个手机号用逗号或空格分隔">
          <Input placeholder="+8613800000000" />
        </Form.Item>
        <Divider orientation="left" plain>IVR 按键操作</Divider>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="默认按键：1 重试失败 Job，2 取消运行，3 确认告警，9 忽略告警"
          description="重试/取消需要再次按 1 确认、按 0 取消；仅允许配置的来源事件执行高风险动作。"
        />
        <Space size={24} wrap>
          <Form.Item name="ivr_enabled" label="启用 IVR" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="callback_token" label="回调 Token" style={{ minWidth: 320, marginBottom: 12 }}>
            <Input.Password placeholder="自定义回调校验 token" visibilityToggle />
          </Form.Item>
        </Space>
        <Form.Item name="ivr_allowed_sources" label="允许操作的来源">
          <Select mode="tags" placeholder="image_build_monitor" options={[
            { value: 'image_build_monitor', label: '镜像构建监控' },
            { value: 'alert_rules', label: '资源告警' },
            { value: 'daily_report', label: '每日报告' },
          ]} />
        </Form.Item>
        <Form.Item name="ivr_allowed_phones" label="允许操作的手机号" extra="留空表示不限制；建议配置值班手机号白名单">
          <Select mode="tags" placeholder="+8613800000000" />
        </Form.Item>
        {data?.callback_token && (
          <Form.Item label="服务商回调地址">
            <Paragraph copyable style={{ marginBottom: 0, wordBreak: 'break-all' }}>
              {`/api/v1/notifications/voice/callback?token=${data.callback_token}`}
            </Paragraph>
          </Form.Item>
        )}
      </Form>

      <Divider orientation="left" plain>发起测试呼叫</Divider>
      <Space.Compact style={{ width: '100%' }}>
        <Input value={testPhones} onChange={(e) => setTestPhones(e.target.value)} placeholder="输入测试手机号" />
        <Button icon={<PhoneOutlined />} loading={testVoice.isPending} onClick={sendTest}>发起语音测试</Button>
      </Space.Compact>
      {testResult && (
        <Text type={testResult.includes('已发起') ? 'success' : 'danger'} style={{ display: 'block', marginTop: 8 }}>
          {testResult}
        </Text>
      )}

      <Divider orientation="left" plain>最近 IVR 操作</Divider>
      <Table
        rowKey="id"
        size="small"
        loading={actionsQuery.isLoading}
        dataSource={actionsQuery.data ?? []}
        pagination={false}
        columns={[
          { title: '事件', dataIndex: 'event_title', ellipsis: true },
          { title: '按键', dataIndex: 'digit', width: 60 },
          {
            title: '动作', dataIndex: 'action', width: 90,
            render: (v: string) => ({ retry: '重试', abort: '取消', ack: '确认', ignore: '忽略' }[v] || v),
          },
          {
            title: '状态', dataIndex: 'status', width: 100,
            render: (v: string) => <Tag color={v === 'success' ? 'green' : v === 'failed' ? 'red' : 'gold'}>{v}</Tag>,
          },
          { title: '时间', dataIndex: 'created_at', width: 165, render: (v: string) => v ? v.replace('T', ' ').slice(0, 19) : '-' },
        ]}
      />
    </Card>
  )
}
