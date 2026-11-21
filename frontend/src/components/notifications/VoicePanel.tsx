import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Switch, Table, Tag, Typography, message,
} from 'antd'
import {
  CloudOutlined, ExperimentOutlined, KeyOutlined, PhoneOutlined, PlusOutlined, SaveOutlined,
  SettingOutlined, SoundOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useIvrActions, useTestVoice, useUpdateVoiceConfig, useVoiceConfig } from '../../hooks/useNotifications'
import type { VoiceConfigUpdate, VoiceProvider } from '../../services/notifications'
import { PanelHeader, SectionCard, ToggleRow } from './ui'

const { Text, Paragraph } = Typography

function emptyProvider(): VoiceProvider {
  return {
    id: `v_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
    name: '新建配置',
    provider: 'aliyun',
    access_key_id: '',
    called_show_number: '',
    tts_code: '',
    tts_param_key: 'content',
    region_id: 'cn-hangzhou',
  }
}

function splitPhones(text: string): string[] {
  return text.split(/[,;\s]+/).map((item) => item.trim()).filter(Boolean)
}

export default function VoicePanel() {
  const { data, isLoading, refetch } = useVoiceConfig()
  const updateConfig = useUpdateVoiceConfig()
  const testVoice = useTestVoice()
  const actionsQuery = useIvrActions()
  const [form] = Form.useForm()
  const [providers, setProviders] = useState<VoiceProvider[]>([])
  const [activeId, setActiveId] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [testOpen, setTestOpen] = useState(false)
  const [testPhones, setTestPhones] = useState('')
  const [testResult, setTestResult] = useState<string | null>(null)

  const applyProviderToForm = (p: VoiceProvider) => {
    form.setFieldsValue({
      name: p.name,
      provider: p.provider,
      access_key_id: p.access_key_id,
      called_show_number: p.called_show_number,
      tts_code: p.tts_code,
      tts_param_key: p.tts_param_key,
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
      play_times: data.play_times,
      test_phones_text: (data.test_phones || []).join(', '),
      ivr_enabled: data.ivr_enabled,
      callback_token: data.callback_token,
      ivr_allowed_sources: data.ivr_allowed_sources,
      ivr_allowed_phones: data.ivr_allowed_phones,
    })
    applyProviderToForm(current)
  }, [data, form])

  const persistCurrentProvider = (): VoiceProvider[] => {
    const values = form.getFieldsValue()
    const merged = providers.map((p) => {
      if (p.id !== selectedId) return p
      const secret = (values.access_key_secret || '').trim()
      return {
        ...p,
        name: values.name || p.name,
        provider: values.provider || p.provider,
        access_key_id: values.access_key_id || '',
        called_show_number: values.called_show_number || '',
        tts_code: values.tts_code || '',
        tts_param_key: values.tts_param_key || 'content',
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
    setProviders([...merged, next])
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
    const payload: VoiceConfigUpdate = {
      enabled: values.enabled,
      fallback_enabled: values.fallback_enabled,
      apply_to: values.apply_to,
      play_times: values.play_times,
      test_phones: splitPhones(String(values.test_phones_text || '')),
      ivr_enabled: values.ivr_enabled,
      callback_token: values.callback_token,
      ivr_actions: data?.ivr_actions,
      ivr_allowed_sources: values.ivr_allowed_sources,
      ivr_allowed_phones: values.ivr_allowed_phones,
      active_provider_id: activeId,
      providers: merged,
    }
    try {
      await updateConfig.mutateAsync(payload)
      message.success('语音配置已保存')
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
      const result = await testVoice.mutateAsync({ phones, text: 'vLLM Ascend 通知服务语音测试' })
      setTestResult(result.success ? '测试语音已发起' : `测试失败：${result.error || '未知错误'}`)
    } catch (e: any) {
      setTestResult(`测试失败：${e?.response?.data?.detail || e?.message || '未知错误'}`)
    }
  }

  const activeProvider = providers.find((p) => p.id === activeId)

  return (
    <Card className="nc-panel" styles={{ body: { padding: 20 } }} loading={isLoading}>
      <PanelHeader
        icon={<PhoneOutlined />}
        title="语音电话通道"
        description="critical 告警优先拨打电话播报，未接通时自动降级短信或邮件。"
        actions={
          <>
            <Button icon={<ExperimentOutlined />} onClick={() => { setTestOpen(true); setTestResult(null) }}>
              测试语音
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
        message={data?.enabled ? '语音通道已启用' : '语音通道未启用'}
        description={
          data?.enabled
            ? `启用配置：${activeProvider?.name || '未选择'}｜服务商：${activeProvider?.provider || '-'}｜主叫：${activeProvider?.called_show_number || '未配置'}`
            : '填写语音参数并启用后，critical 告警会优先拨打电话。'
        }
      />

      <Form form={form} layout="vertical">
        <SectionCard
          icon={<SettingOutlined />}
          title="启用与降级策略"
          description="控制语音是否参与告警链路、适用级别与播报次数。"
        >
          <div className="nc-form-grid">
            <div className="nc-col-8">
              <ToggleRow
                label="启用语音通道"
                description="关闭后所有通知跳过语音"
                control={<Form.Item name="enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
              <ToggleRow
                label="启用失败降级"
                description="未接通或呼叫失败后继续尝试短信/邮件"
                control={<Form.Item name="fallback_enabled" valuePropName="checked" noStyle><Switch /></Form.Item>}
              />
            </div>
            <div className="nc-col-4">
              <Form.Item name="apply_to" label="适用级别" style={{ marginBottom: 14 }}>
                <Select
                  options={[
                    { value: 'critical', label: '仅 critical' },
                    { value: 'all', label: '全部级别' },
                  ]}
                />
              </Form.Item>
              <Form.Item name="play_times" label="重复播报次数" style={{ marginBottom: 0 }}>
                <InputNumber min={1} max={5} style={{ width: '100%' }} />
              </Form.Item>
            </div>
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
                  <PhoneOutlined />
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                </div>
                <div className="nc-provider-card-meta">
                  {p.provider === 'mock' ? 'Mock 联调' : '阿里云语音通知'}
                  <br />
                  {p.called_show_number || '未设置主叫号码'}
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
                <Input placeholder="如：生产语音 / 备用语音" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="provider" label="服务商" style={{ marginBottom: 14 }}>
                <Select
                  options={[
                    { value: 'aliyun', label: '阿里云语音通知' },
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
              <Form.Item name="called_show_number" label="主叫号码" style={{ marginBottom: 14 }}>
                <Input placeholder="0571xxxxxxx" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="tts_code" label="TTS 模板 CODE" style={{ marginBottom: 14 }}>
                <Input placeholder="TTS_xxxxxxx" />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="tts_param_key" label="模板变量名" tooltip="模板中接收告警文本的变量" style={{ marginBottom: 14 }}>
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

        <SectionCard
          icon={<KeyOutlined />}
          title="IVR 按键操作"
          description="默认按键：1 重试失败 Job，2 取消运行，3 确认告警，9 忽略告警。"
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 14 }}
            message="重试/取消需要二次确认"
            description="按 1 确认操作，按 0 取消。高风险动作仅允许指定来源事件执行。"
          />
          <div className="nc-form-grid">
            <div className="nc-col-4">
              <Form.Item name="ivr_enabled" label="启用 IVR" valuePropName="checked" style={{ marginBottom: 14 }}>
                <Switch checkedChildren="开启" unCheckedChildren="关闭" />
              </Form.Item>
            </div>
            <div className="nc-col-8">
              <Form.Item name="callback_token" label="回调 Token" style={{ marginBottom: 14 }}>
                <Input.Password placeholder="自定义回调校验 token" visibilityToggle />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="ivr_allowed_sources" label="允许操作的来源" style={{ marginBottom: 14 }}>
                <Select mode="tags" placeholder="image_build_monitor" options={[
                  { value: 'image_build_monitor', label: '镜像构建监控' },
                  { value: 'alert_rules', label: '资源告警' },
                  { value: 'daily_report', label: '每日报告' },
                ]} />
              </Form.Item>
            </div>
            <div className="nc-col-6">
              <Form.Item name="ivr_allowed_phones" label="允许操作的手机号" extra="留空表示不限制" style={{ marginBottom: 14 }}>
                <Select mode="tags" placeholder="+8613800000000" />
              </Form.Item>
            </div>
          </div>
          {data?.callback_token && (
            <Form.Item label="服务商回调地址" style={{ marginBottom: 0 }}>
              <Paragraph copyable style={{ marginBottom: 0, wordBreak: 'break-all', fontSize: 12 }}>
                {`/api/v1/notifications/voice/callback?token=${data.callback_token}`}
              </Paragraph>
            </Form.Item>
          )}
        </SectionCard>

        <SectionCard
          icon={<SoundOutlined />}
          title="最近 IVR 操作"
          description="记录按键、动作、执行状态与时间，便于审计。"
        >
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
        </SectionCard>
      </Form>

      <Modal
        title="测试语音通道"
        open={testOpen}
        onCancel={() => setTestOpen(false)}
        onOk={sendTest}
        okText="发起测试"
        cancelText="取消"
        confirmLoading={testVoice.isPending}
        destroyOnClose
      >
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
          将使用当前启用配置发起一次测试呼叫。
        </Text>
        <Input
          value={testPhones}
          onChange={(e) => setTestPhones(e.target.value)}
          placeholder="输入测试手机号，多个用逗号或空格分隔"
        />
        {testResult && (
          <div className={`nc-test-result ${testResult.includes('已发起') ? 'nc-test-ok' : 'nc-test-fail'}`}>
            {testResult}
          </div>
        )}
      </Modal>
    </Card>
  )
}
