import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Form, Input, InputNumber, Modal, Space, Spin, Switch, Tabs, Tag,
} from 'antd'
import {
  BarChartOutlined, ExperimentOutlined, HistoryOutlined, InboxOutlined, InfoCircleOutlined, MailOutlined,
  MessageOutlined, NotificationOutlined, PhoneOutlined, SendOutlined, SettingOutlined, TeamOutlined,
} from '@ant-design/icons'
import { useSmtpConfig, useUpdateSmtpConfig } from '../hooks/useSmtpConfig'
import type { SmtpConfigUpdate, SmtpTestResult } from '../services/smtpConfig'
import { testSmtpConnection } from '../services/smtpConfig'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useSmsConfig, useVoiceConfig } from '../hooks/useNotifications'
import ContactsPanel from '../components/notifications/ContactsPanel'
import DeliveriesPanel from '../components/notifications/DeliveriesPanel'
import SmsPanel from '../components/notifications/SmsPanel'
import VoicePanel from '../components/notifications/VoicePanel'
import PolicyPanel from '../components/notifications/PolicyPanel'
import MetricsPanel from '../components/notifications/MetricsPanel'
import InboxPanel from '../components/notifications/InboxPanel'
import { SectionCard } from '../components/notifications/ui'
import './NotificationCenter.css'

function SmtpConfigPage() {
  const { data: currentUser } = useCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'
  const isAdmin = currentUser?.role === 'admin' || isSuperAdmin

  const { data: config, isLoading } = useSmtpConfig()
  const { data: smsConfig } = useSmsConfig()
  const { data: voiceConfig } = useVoiceConfig()
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
        <Alert type="error" message="权限不足" description="仅管理员可访问通知服务" showIcon />
      </Card>
    )
  }

  const statusTag = (label: string, ok: boolean, okText = '已配置', offText = '未配置') => (
    <Tag color={ok ? 'success' : 'default'} style={{ borderRadius: 12, paddingInline: 10 }}>
      {label} · {ok ? okText : offText}
    </Tag>
  )

  return (
    <div className="notification-center">
      <div className="nc-page-header">
        <div>
          <h1 className="nc-page-title">
            <span className="nc-page-icon"><NotificationOutlined /></span>
            通知服务
          </h1>
          <div className="nc-page-subtitle">
            统一管理邮件、短信、语音电话、联系人与通知策略。告警按语音 → 短信 → 邮件降级，所有发送与 IVR 操作均可追溯。
          </div>
        </div>
        <div className="nc-status-strip">
          {statusTag('邮件', !!config?.smtp_host)}
          {statusTag('短信', !!smsConfig?.enabled, '已启用', '未启用')}
          {statusTag('语音', !!voiceConfig?.enabled, '已启用', '未启用')}
        </div>
      </div>

      <div className="nc-shell">
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            { key: 'email', label: <Space><MailOutlined />邮件</Space> },
            { key: 'contacts', label: <Space><TeamOutlined />联系人</Space> },
            { key: 'deliveries', label: <Space><HistoryOutlined />发送记录</Space> },
            { key: 'policy', label: <Space><SettingOutlined />通知策略</Space> },
            { key: 'metrics', label: <Space><BarChartOutlined />运营指标</Space> },
            { key: 'sms', label: <Space><MessageOutlined />短信</Space> },
            { key: 'voice', label: <Space><PhoneOutlined />语音电话</Space> },
            { key: 'mock-inbox', label: <Space><InboxOutlined />收件箱</Space> },
          ]}
        />
      </div>

      <div style={{ marginTop: 16 }}>
        {activeTab === 'email' && (
          <Card className="nc-panel" styles={{ body: { padding: 24 } }}>
            <div className="nc-panel-header">
              <div>
                <h3 className="nc-panel-title"><MailOutlined />邮件通道</h3>
                <div className="nc-panel-desc">SMTP 配置供每日运行报告和告警通知共用，修改后立即生效。</div>
              </div>
              {isSuperAdmin && (
                <div className="nc-panel-actions">
                  <Button
                    icon={<ExperimentOutlined />}
                    loading={testing}
                    onClick={async () => {
                      const formValues = form.getFieldsValue()
                      if (!formValues.smtp_host && !config?.smtp_host) {
                        Modal.warning({ title: '请先填写并保存 SMTP 主机' })
                        return
                      }
                      setTesting(true)
                      setTestResult(null)
                      try {
                        if (!config?.smtp_host || formValues.smtp_host !== config.smtp_host
                          || formValues.smtp_port !== config.smtp_port
                          || formValues.smtp_username !== config.smtp_username
                          || formValues.from_email !== config.from_email
                          || formValues.smtp_password) {
                          await handleSave()
                        }
                        const result = await testSmtpConnection()
                        setTestResult(result)
                      } finally {
                        setTesting(false)
                      }
                    }}
                  >
                    测试连通性
                  </Button>
                  <Button type="primary" icon={<SendOutlined />} loading={saving} onClick={handleSave}>
                    保存配置
                  </Button>
                </div>
              )}
            </div>

            {!isSuperAdmin && (
              <Alert type="warning" message="仅超级管理员可修改 SMTP 配置" showIcon className="nc-callout" />
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
                      ? `${config.smtp_host}:${config.smtp_port}（${config.smtp_username || '无认证'}）`
                      : '请配置 SMTP 服务器以启用邮件通知'
                  }
                  icon={config?.smtp_host ? <InfoCircleOutlined /> : undefined}
                  showIcon
                  className="nc-callout"
                />

                {testResult && (
                  <Alert
                    type={testResult.success ? 'success' : 'error'}
                    message={testResult.success ? '连通性测试通过' : '连通性测试失败'}
                    description={
                      <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
                        {testResult.steps.map((step, index) => <li key={index}>{step}</li>)}
                      </ul>
                    }
                    showIcon
                    closable
                    onClose={() => setTestResult(null)}
                    className="nc-callout"
                  />
                )}

                <Form form={form} layout="vertical" disabled={!isSuperAdmin}>
                  <SectionCard
                    icon={<MailOutlined />}
                    title="连接参数"
                    description="SMTP 服务地址、端口与认证信息。"
                  >
                    <div className="nc-form-grid">
                      <div className="nc-col-8">
                        <Form.Item name="smtp_host" label="服务器地址" rules={[{ required: true, message: '请输入 SMTP 地址' }]} style={{ marginBottom: 14 }}>
                          <Input placeholder="smtp.example.com" />
                        </Form.Item>
                      </div>
                      <div className="nc-col-4">
                        <Form.Item name="smtp_port" label="端口" rules={[{ required: true }]} style={{ marginBottom: 14 }}>
                          <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                        </Form.Item>
                      </div>
                      <div className="nc-col-6">
                        <Form.Item name="smtp_username" label="用户名" style={{ marginBottom: 0 }}>
                          <Input placeholder="noreply@example.com" />
                        </Form.Item>
                      </div>
                      <div className="nc-col-6">
                        <Form.Item
                          name="smtp_password"
                          label={<Space><span>密码</span>{config?.smtp_password_set && <Tag color="green">已设置</Tag>}</Space>}
                          extra="留空则不修改"
                          style={{ marginBottom: 0 }}
                        >
                          <Input.Password
                            visibilityToggle
                            placeholder={config?.smtp_password_set ? '已设置，留空则不修改' : '输入 SMTP 密码'}
                          />
                        </Form.Item>
                      </div>
                    </div>
                  </SectionCard>
                  <SectionCard
                    icon={<SendOutlined />}
                    title="发件身份"
                    description="对外展示的发件人地址与传输加密方式。"
                    tone="purple"
                  >
                    <div className="nc-form-grid">
                      <div className="nc-col-8">
                        <Form.Item name="from_email" label="发件人邮箱" rules={[{ type: 'email', message: '请输入有效邮箱' }]} style={{ marginBottom: 0 }}>
                          <Input placeholder="dashboard@vllm-ascend.local" />
                        </Form.Item>
                      </div>
                      <div className="nc-col-4">
                        <Form.Item name="smtp_use_tls" label="TLS 加密" valuePropName="checked" style={{ marginBottom: 0 }}>
                          <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                        </Form.Item>
                      </div>
                    </div>
                  </SectionCard>
                </Form>
              </>
            )}
          </Card>
        )}
        {activeTab === 'contacts' && <ContactsPanel />}
        {activeTab === 'deliveries' && <DeliveriesPanel />}
        {activeTab === 'policy' && <PolicyPanel />}
        {activeTab === 'metrics' && <MetricsPanel />}
        {activeTab === 'sms' && <SmsPanel />}
        {activeTab === 'voice' && <VoicePanel />}
        {activeTab === 'mock-inbox' && <InboxPanel />}
      </div>
    </div>
  )
}

export default SmtpConfigPage
