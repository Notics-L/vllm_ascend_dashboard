import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Divider, Form, Input, Select, Space, Switch, Tag, Typography, message,
} from 'antd'
import { ExperimentOutlined, SaveOutlined } from '@ant-design/icons'
import { useSmsConfig, useTestSms, useUpdateSmsConfig } from '../../hooks/useNotifications'
import type { SmsConfigUpdate } from '../../services/notifications'

const { Text } = Typography

export default function SmsPanel() {
  const { data, isLoading, refetch } = useSmsConfig()
  const updateConfig = useUpdateSmsConfig()
  const testSms = useTestSms()
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
    const payload: SmsConfigUpdate = {
      ...values,
      test_phones: String(values.test_phones_text || '')
        .split(/[,;\\s]+/)
        .map((item: string) => item.trim())
        .filter(Boolean),
    }
    delete (payload as Record<string, unknown>).test_phones_text
    try {
      await updateConfig.mutateAsync(payload)
      message.success('短信配置已保存')
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
      const result = await testSms.mutateAsync({ phones, text: 'vLLM Ascend 通知服务短信测试' })
      setTestResult(result.success ? '测试短信发送成功' : `测试失败：${result.error || '未知错误'}`)
      message.success(result.success ? '测试短信已发送' : '短信发送失败')
    } catch (e: any) {
      setTestResult(`测试失败：${e?.response?.data?.detail || e?.message || '未知错误'}`)
    }
  }

  return (
    <Card
      size="small"
      title="短信通道"
      extra={<Button type="primary" size="small" icon={<SaveOutlined />} loading={updateConfig.isPending} onClick={save}>保存配置</Button>}
      loading={isLoading}
    >
      <Alert
        type={data?.enabled ? 'success' : 'warning'}
        showIcon
        style={{ marginBottom: 16 }}
        message={data?.enabled ? '短信通道已启用' : '短信通道未启用'}
        description={
          data?.enabled
            ? `服务商：${data.provider}｜签名：${data.sign_name || '未配置'}｜模板：${data.template_code || '未配置'}`
            : '启用后可对 critical 告警发送短信，并在短信失败时自动降级到邮件。'
        }
      />
      <Form form={form} layout="vertical">
        <Space size={24} wrap>
          <Form.Item name="enabled" label="启用短信" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="fallback_enabled" label="启用降级" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Switch />
          </Form.Item>
          <Form.Item name="apply_to" label="适用级别" style={{ marginBottom: 12, minWidth: 160 }}>
            <Select options={[{ value: 'critical', label: '仅 critical' }, { value: 'all', label: 'critical + warning + info' }]} />
          </Form.Item>
        </Space>
        <Divider orientation="left" plain>阿里云短信配置</Divider>
        <Form.Item name="provider" label="服务商">
          <Select
            options={[
              { value: 'aliyun', label: '阿里云短信' },
              { value: 'mock', label: 'Mock（本地联调，不真实发送）' },
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
          <Form.Item name="sign_name" label="短信签名" style={{ minWidth: 200 }}>
            <Input placeholder="vLLM Ascend" />
          </Form.Item>
          <Form.Item name="template_code" label="模板 CODE" style={{ minWidth: 200 }}>
            <Input placeholder="SMS_xxxxxxx" />
          </Form.Item>
          <Form.Item name="template_param_key" label="模板变量名" tooltip="模板中接收告警文本的变量，例如 content">
            <Input placeholder="content" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="region_id" label="Region">
            <Input placeholder="cn-hangzhou" style={{ width: 160 }} />
          </Form.Item>
        </Space>
        <Form.Item name="test_phones_text" label="默认测试手机号" extra="多个手机号用逗号或空格分隔">
          <Input placeholder="+8613800000000" />
        </Form.Item>
      </Form>

      <Divider orientation="left" plain>发送测试</Divider>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          value={testPhones}
          onChange={(e) => setTestPhones(e.target.value)}
          placeholder="输入测试手机号，多个用逗号分隔"
        />
        <Button icon={<ExperimentOutlined />} loading={testSms.isPending} onClick={sendTest}>发送测试短信</Button>
      </Space.Compact>
      {testResult && (
        <Text type={testResult.includes('成功') ? 'success' : 'danger'} style={{ display: 'block', marginTop: 8 }}>
          {testResult}
        </Text>
      )}
    </Card>
  )
}
