import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Form, Input, Modal, Select, Switch, Tag, Typography, message,
} from 'antd'
import {
  CloudOutlined, ExperimentOutlined, MessageOutlined, PlusOutlined, SaveOutlined,
  SettingOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useSmsConfig, useTestSms, useUpdateSmsConfig } from '../../hooks/useNotifications'
import type { SmsConfigUpdate, SmsProvider } from '../../services/notifications'
import { PanelHeader, SectionCard, ToggleRow } from './ui'

const { Text } = Typography

function emptyProvider(): SmsProvider {
  return {
    id: `p_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    name: '新建配置',
    provider: 'aliyun',
    access_key_id: '',
    sign_name: '',
    template_code: '',
    template_param_key: 'content',
    region_id: 'cn-hangzhou',
  }
}

function splitPhones(text: string): string[] {
  return text.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean)
}

export default function SmsPanel() {
  const { data, isLoading, refetch } = useSmsConfig()
  const updateConfig = useUpdateSmsConfig()
  const testSms = useTestSms()
  const [form] = Form.useForm()
  const [providers, setProviders] = useState<SmsProvider[]>([])
  const [activeId, setActiveId] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [testOpen, setTestOpen] = useState(false)
  const [testPhones, setTestPhones] = useState('')
  const [testResult, setTestResult] = useState<string | null>(null)

  const applyProviderToForm = (p: SmsProvider) => {
    form.setFieldsValue({
      name: p.name,
      provider: p.provider,
      access_key_id: p.access_key_id,
      sign_name: p.sign_name,
      template_code: p.template_code,
      template_param_key: p.template_param_key,
      region_id: p.region_id,
      access_key_secret: '',
    })
  }

  useEffect(() => {
    if (!data) return
    const list = data.providers?.length ? data.providers : [emptyProvider()]
    const active = data.active_provider_id || list[0].id
    setProviders(list)
    setActiveId(active)
    setSelectedId(active)
    const current = list.find((p) => p.id === active) || list[0]
    form.setFieldsValue({
      enabled: data.enabled,
      fallback_enabled: data.fallback_enabled,
      apply_to: data.apply_to,
      test_phones_text: (data.test_phones || []).join(', '),
    })
    applyProviderToForm(current)
  }, [data, form])

  const persistCurrentProvider = (): SmsProvider[] => {
    const values = form.getFieldsValue()
    const merged = providers.map((p) => {
      if (p.id !== selectedId) return p
      const secret = (values.access_key_secret || '').trim()
      return {
        ...p,
        name: values.name || p.name,
        provider: values.provider || p.provider,
        access_key_id: values.access_key_id || '',
        sign_name: values.sign_name || '',
        template_code: values.template_code || '',
        template_param_key: values.template_param_key || 'content',
        region_id: values.region_id || 'cn-hangzhou',
        access_key_secret: secret || p.access_key_secret,
        access_key_secret_set: secret ? true : p.access_key_secret_set,
      }
    })
    setProviders(merged)
    return merged
  }

  const selectProvider = (id: string) => {
    const merged = persistCurrentProvider()
    const target = merged.find((p) => p.id === id)
    if (!target) return
    setSelectedId(id)
    applyProviderToForm(target)
  }

  const addProvider = () => {
    const merged = persistCurrentProvider()
    const next = emptyProvider()
    const list = [...merged, next]
    setProviders(list)
    setSelectedId(next.id)
    applyProviderToForm(next)
  }

  const removeProvider = () => {
    if (providers.length <= 1) {
      message.warning('至少保留一套配置')
      return
    }
    const merged = providers.filter((p) => p.id !== selectedId)
    const nextActive = merged.some((p) => p.id === activeId) ? activeId : merged[0].id
    setProviders(merged)
    setActiveId(nextActive)
    setSelectedId(merged[0].id)
    applyProviderToForm(merged[0])
  }

  const activateProvider = () => {
    persistCurrentProvider()
    setActiveId(selectedId)
    message.success('已切换启用配置，保存后生效')
  }

  const save = async () => {
    const merged = persistCurrentProvider()
    const values = await form.validateFields()
    const payload: SmsConfigUpdate = {
      enabled: values.enabled,
      fallback_enabled: values.fallback_enabled,
      apply_to: values.apply_to,
      test_phones: splitPhones(String(values.test_phones_text || '')),
      active_provider_id: activeId,
      providers: merged,
    }
    try {
      await updateConfig.mutateAsync(payload)
      message.success('短信配置已保存')
      await refetch()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    }
  }

  const sendTest = async () => {
    const phones = splitPhones(testPhones)
    if (!phones.length) {
      message.error('请填写测试手机号')
      return
    }
    setTestResult(null)
    try {
      const result = await testSms.mutateAsync({ phones, text: 'vLLM Ascend 通知服务短信测试' })
      setTestResult(result.success ? '测试短信发送成功' : `测试失败：${result.error || '未知错误'}`)
    } catch (e: any) {
      setTestResult(`测试失败：${e?.response?.data?.detail || e?.message || '未知错误'}`)
    }
  }

  const activeProvider = providers.find((p) => p.id === activeId)

  return (
    <Card className="nc-panel" styles={{ body: { padding: 20 } }} loading={isLoading}>
      <PanelHeader
        icon={<MessageOutlined />}
        title="短信通道"
        description="critical 告警优先短信触达，失败后自动降级邮件。"
        actions={
          <>
            <Button icon={<ExperimentOutlined />} onClick={() => { setTestOpen(true); setTestResult(null) }}>
              测试短信
            </Button>
            <Button type="primary" icon={<SaveOutlined />} loading={updateConfig.isPending} onClick={save}>
              保存配置
            </Button>
          </>
        }
      />

      <Alert
        type={data?.enabled ? 'success' : 'warning'}
        showIcon
        style={{ marginBottom: 16, maxWidth: 820 }}
        message={data?.enabled ? '短信通道已启用' : '短信通道未启用'}
        description={
          data?.enabled
            ? `启用配置：${activeProvider?.name || '未选择'}｜服务商：${activeProvider?.provider || '-'}｜签名：${activeProvider?.sign_name || '未配置'}`
            : '填写服务商参数并启用后，critical 告警会优先发送短信。'
        }
      />

      <Form form={form} layout="vertical">
        <SectionCard
          icon={<SettingOutlined />}
          title="启用与适用范围"
          description="控制短信是否参与告警链路，以及覆盖的告警级别。"
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 240px)', gap: 20 }}>
            <div>
              <ToggleRow
                label="启用短信通道"
                description="关闭后所有通知跳过短信"
                control={<Form.Item name="enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
              <ToggleRow
                label="启用失败降级"
                description="短信失败后继续尝试邮件"
                control={<Form.Item name="fallback_enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
            </div>
            <Form.Item name="apply_to" label="适用级别" style={{ marginBottom: 0 }}>
              <Select
                options={[
                  { value: 'critical', label: '仅 critical' },
                  { value: 'all', label: '全部级别（critical / warning / info）' },
                ]}
              />
            </Form.Item>
          </div>
        </SectionCard>

        <SectionCard
          icon={<CloudOutlined />}
          title="服务商配置"
          description="可维护多套服务商配置，选择其中一套启用。AccessKey Secret 加密存储，不回显明文。"
          tone="purple"
          wide
        >
          <div className="nc-provider-list">
            {providers.map((p) => (
              <div
                key={p.id}
                className={`nc-provider-card${p.id === selectedId ? ' nc-provider-selected' : ''}${p.id === activeId ? ' nc-provider-active' : ''}`}
                onClick={() => selectProvider(p.id)}
              >
                <div className="nc-provider-card-title">
                  <CloudOutlined />
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                </div>
                <div className="nc-provider-card-meta">
                  {p.provider === 'mock' ? 'Mock 联调' : '阿里云短信'}
                  <br />
                  {p.sign_name || '未设置签名'}
                </div>
                <div className="nc-provider-card-tags">
                  {p.id === activeId && <Tag color="blue">启用中</Tag>}
                  {p.access_key_secret_set ? <Tag color="green">密钥已设置</Tag> : <Tag>未设密钥</Tag>}
                </div>
              </div>
            ))}
            <div className="nc-provider-add" onClick={addProvider}>
              <PlusOutlined style={{ marginRight: 6 }} />新增配置
            </div>
          </div>

          <div className="nc-actions-row" style={{ marginBottom: 16 }}>
            <Button
              type={selectedId === activeId ? 'default' : 'primary'}
              icon={<ThunderboltOutlined />}
              disabled={selectedId === activeId}
              onClick={activateProvider}
            >
              {selectedId === activeId ? '当前启用配置' : '启用此配置'}
            </Button>
            <Button danger onClick={removeProvider} disabled={providers.length <= 1}>删除此配置</Button>
          </div>

          <div className="nc-form-grid">
            <div className="nc-col-6">
              <Form.Item name="name" label="配置名称" style={{ marginBottom: 14 }}>
                <Input placeholder="如：生产短信 / 备用短信" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="provider" label="服务商" style={{ marginBottom: 14 }}>
                <Select
                  options={[
                    { value: 'aliyun', label: '阿里云短信' },
                    { value: 'mock', label: 'Mock（仅本地联调）' },
                  ]}
                />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="region_id" label="Region" style={{ marginBottom: 14 }}>
                <Input placeholder="cn-hangzhou" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="access_key_id" label="AccessKey ID" style={{ marginBottom: 14 }}>
                <Input placeholder="LTAI..." />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="access_key_secret" label="AccessKey Secret" extra="留空则不修改" style={{ marginBottom: 14 }}>
                <Input.Password placeholder="输入 Secret" visibilityToggle />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="sign_name" label="短信签名" style={{ marginBottom: 14 }}>
                <Input placeholder="vLLM Ascend" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="template_code" label="模板 CODE" style={{ marginBottom: 14 }}>
                <Input placeholder="SMS_xxxxxxx" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="template_param_key" label="模板变量名" tooltip="模板中接收告警文本的变量" style={{ marginBottom: 14 }}>
                <Input placeholder="content" />
              </Form.Item>
            </div>
            <div className="nc-col-12 nc-col-span-all">
              <Form.Item name="test_phones_text" label="默认测试手机号" extra="多个手机号用逗号或空格分隔" style={{ marginBottom: 0 }}>
                <Input placeholder="+8613800000000" style={{ maxWidth: 420 }} />
              </Form.Item>
            </div>
          </div>
        </SectionCard>
      </Form>

      <Modal
        title="测试短信通道"
        open={testOpen}
        onCancel={() => setTestOpen(false)}
        onOk={sendTest}
        okText="发送测试"
        cancelText="取消"
        confirmLoading={testSms.isPending}
        destroyOnClose
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          将使用当前启用配置发送一条测试短信。
        </Text>
        <Input
          value={testPhones}
          onChange={(e) => setTestPhones(e.target.value)}
          placeholder="输入测试手机号，多个用逗号或空格分隔"
        />
        {testResult && (
          <div className={`nc-test-result ${testResult.includes('成功') ? 'nc-test-ok' : 'nc-test-fail'}`}>
            {testResult}
          </div>
        )}
      </Modal>
    </Card>
  )
}
