import { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Switch, Button, Space, Tag, Alert,
  Typography, Divider, Modal, Spin, Tabs,
} from 'antd'
import {
  MailOutlined, KeyOutlined, InfoCircleOutlined, SendOutlined, ExperimentOutlined, NotificationOutlined,
} from '@ant-design/icons'
import { useSmtpConfig, useUpdateSmtpConfig } from '../hooks/useSmtpConfig'
import type { SmtpConfigUpdate } from '../services/smtpConfig'
import { testSmtpConnection } from '../services/smtpConfig'
import type { SmtpTestResult } from '../services/smtpConfig'
import { useCurrentUser } from '../hooks/useCurrentUser'

const { Text, Title } = Typography

function SmtpConfigPage() {
  const { data: currentUser } = useCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'
  const isAdmin = currentUser?.role === 'admin' || isSuperAdmin

  const { data: config, isLoading } = useSmtpConfig()
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<SmtpTestResult | null>(null)
  const updateConfig = useUpdateSmtpConfig()

  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [activeTab, setActiveTab] = useState('email')

  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        smtp_host: config.smtp_host,
        smtp_port: config.smtp_port,
        smtp_username: config.smtp_username,
        smtp_use_tls: config.smtp_use_tls,
        from_email: config.from_email,
      })
    }
  }, [config, form])

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const update: SmtpConfigUpdate = {}
      if (values.smtp_host !== config?.smtp_host) update.smtp_host = values.smtp_host
      if (values.smtp_port !== config?.smtp_port) update.smtp_port = values.smtp_port
      if (values.smtp_username !== config?.smtp_username) update.smtp_username = values.smtp_username
      if (values.smtp_password) update.smtp_password = values.smtp_password
      if (values.smtp_use_tls !== config?.smtp_use_tls) update.smtp_use_tls = values.smtp_use_tls
      if (values.from_email !== config?.from_email) update.from_email = values.from_email

      if (Object.keys(update).length === 0) {
        Modal.info({ title: '无变更', content: '配置没有变化，无需保存' })
        setSaving(false)
        return
      }
      await updateConfig.mutateAsync(update)
      Modal.success({ title: '保存成功', content: 'SMTP 配置已更新' })
      form.setFieldValue('smtp_password', '')
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || '未知错误'
      Modal.error({ title: '保存失败', content: msg })
    } finally {
      setSaving(false)
    }
  }

  if (!isAdmin) {
    return (
      <Card>
        <Alert type="error" message="权限不足" description="仅管理员可访问 SMTP 配置" showIcon />
      </Card>
    )
  }

  return (
    <div>
      <Card style={{ marginBottom: 16 }}>
        <Title level={4} style={{ marginBottom: 4 }}>
          <NotificationOutlined style={{ marginRight: 8 }} />
          通知服务
        </Title>
        <Text type="secondary">
          统一管理邮件、短信与语音电话通知。当前阶段启用邮件通道，短信与语音将在后续阶段接入。
        </Text>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          style={{ marginTop: 16, marginBottom: -16 }}
          items={[
            { key: 'email', label: '邮件' },
            { key: 'sms', label: '短信' },
            { key: 'voice', label: '语音电话' },
          ]}
        />
      </Card>
      {activeTab === 'email' ? (
      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            <Title level={4}>
              <MailOutlined style={{ marginRight: 8 }} />
              邮件通道（SMTP）
            </Title>
            <Text type="secondary">
              邮件通道供每日运行报告和告警通知共用。修改后立即生效。
            </Text>
          </div>
          {isSuperAdmin && (
            <Space>
              <Button
                icon={<ExperimentOutlined />}
                loading={testing}
                onClick={async () => {
                  const formValues = form.getFieldsValue();
                  if (!formValues.smtp_host && !config?.smtp_host) {
                    Modal.warning({ title: '请先填写并保存 SMTP 主机' });
                    return;
                  }
                  setTesting(true);
                  setTestResult(null);
                  try {
                    // 先保存再测试，确保后端拿到最新配置
                    if (!config?.smtp_host || formValues.smtp_host !== config.smtp_host
                      || formValues.smtp_port !== config.smtp_port
                      || formValues.smtp_username !== config.smtp_username
                      || formValues.from_email !== config.from_email
                      || formValues.smtp_password) {
                      await handleSave();
                    }
                    const result = await testSmtpConnection();
                    setTestResult(result);
                  } finally {
                    setTesting(false);
                  }
                }}
              >
                测试连通性
              </Button>
              <Button type="primary" icon={<SendOutlined />} loading={saving} onClick={handleSave}>
                保存配置
              </Button>
            </Space>
          )}
        </div>

        {!isSuperAdmin && (
          <Alert
            type="warning"
            message="仅超级管理员可修改 SMTP 配置"
            showIcon
            style={{ marginBottom: 16 }}
          />
        )}

        {isLoading ? (
          <Spin />
        ) : (
          <>
            <Alert
              type={config?.smtp_host ? 'success' : 'warning'}
              message={config?.smtp_host ? 'SMTP 已配置' : 'SMTP 未配置'}
              description={
                config?.smtp_host
                  ? `${config.smtp_host}:${config.smtp_port} (${config.smtp_username || '无认证'}) — 每日报告和告警可正常发送`
                  : '请配置 SMTP 服务器以启用每日报告邮件推送和告警通知'
              }
              icon={config?.smtp_host ? <InfoCircleOutlined /> : undefined}
              showIcon
              style={{ marginBottom: 24 }}
            />

            {testResult && (
              <Alert
                type={testResult.success ? 'success' : 'error'}
                message={testResult.success ? '连通性测试通过' : '连通性测试失败'}
                description={
                  <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                    {testResult.steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                }
                showIcon
                closable
                onClose={() => setTestResult(null)}
                style={{ marginBottom: 16 }}
              />
            )}

            <Divider orientation="left" plain>
              <Space>
                <KeyOutlined />
                <span>SMTP 服务器设置</span>
              </Space>
            </Divider>

            <Form form={form} layout="vertical" disabled={!isSuperAdmin}>
              <Form.Item name="smtp_host" label="SMTP 服务器地址" rules={[{ required: true, message: '请输入 SMTP 地址' }]}>
                <Input placeholder="smtp.example.com" />
              </Form.Item>

              <Space style={{ width: '100%' }} size="middle">
                <Form.Item name="smtp_port" label="端口" rules={[{ required: true }]}>
                  <InputNumber min={1} max={65535} style={{ width: 120 }} />
                </Form.Item>
                <Form.Item name="smtp_use_tls" label="TLS 加密" valuePropName="checked" style={{ marginTop: 30 }}>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              </Space>

              <Form.Item name="smtp_username" label="用户名">
                <Input placeholder="noreply@example.com" />
              </Form.Item>

              <Form.Item
                name="smtp_password"
                label={
                  <Space>
                    <span>密码</span>
                    {config?.smtp_password_set && <Tag color="green">已设置</Tag>}
                  </Space>
                }
                extra="已设置密码时不显示原文；留空则不修改"
              >
                <Input.Password
                  visibilityToggle
                  placeholder={config?.smtp_password_set ? '已设置，留空则不修改' : '输入 SMTP 密码'}
                />
              </Form.Item>

              <Form.Item name="from_email" label="发件人邮箱" rules={[{ type: 'email', message: '请输入有效邮箱' }]}>
                <Input placeholder="dashboard@vllm-ascend.local" />
              </Form.Item>
            </Form>
          </>
        )}
      </Card>
      ) : (
        <Card>
          <Alert
            type="info"
            showIcon
            message={activeTab === 'sms' ? '短信通道规划中' : '语音电话通道规划中'}
            description={
              activeTab === 'sms'
                ? '后续阶段将支持短信服务商配置、模板管理、测试发送与失败降级。'
                : '后续阶段将支持语音播报、按键确认与未接听降级短信。'
            }
          />
        </Card>
      )}
    </div>
  )
}

export default SmtpConfigPage
