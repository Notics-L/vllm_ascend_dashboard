import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Descriptions, Divider, Form, InputNumber, message, Modal, Radio, Select, Space, Spin, Switch, Tabs, Tag, Table, Typography } from 'antd'
import {
  ClockCircleOutlined, DeleteOutlined, RocketOutlined, SaveOutlined,
} from '@ant-design/icons'

import api from '../../services/api'
import { formatTimezone } from '@/utils/timezone'

const { Text } = Typography

interface ScheduleItem {
  id: number
  workflow_id: number
  workflow_name: string
  sync_mode: string
  recent_days: number
  cron_expression: string
  is_active: boolean
  last_triggered_at: string | null
  updated_at: string | null
}

const cronToText = (expr: string) => {
  const parts = expr.split(' ')
  if (parts.length !== 5) return expr
  const [min, hour, day, _month, dow] = parts
  if (dow !== '*') {
    const dayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    const days = dow.split(',').map(n => parseInt(n)).map(n => dayNames[n] || n).join('、')
    return `每周${days} ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
  }
  if (day !== '*') return `每月${day}日 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
  return `每天 ${parseInt(hour)}:${parseInt(min).toString().padStart(2, '0')}`
}

const modeLabels: Record<string, string> = {
  full: '全量',
  incremental: '增量',
  range: '范围',
  recent_days: '最近N天',
}

const cardStyle = {
  background: '#fff',
  borderRadius: 12,
  boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
  border: '1px solid #f0f0f0',
}

/* ------------------------------------------------------------------ */
/*                  CRON 编辑弹窗（参考贡献者分析）                      */
/* ------------------------------------------------------------------ */

function CronModal({ open, cron, onChange, onClose, title }: {
  open: boolean; cron: string; onChange: (v: string) => void; onClose: () => void; title?: string
}) {
  const [mode, setMode] = useState<'preset' | 'custom' | 'weekly'>('preset')
  const [customHour, setCustomHour] = useState(2)
  const [customMinute, setCustomMinute] = useState(0)
  const [weekDays, setWeekDays] = useState<number[]>([0, 1, 2, 3, 4])
  const [selectedPreset, setSelectedPreset] = useState('0 2 * * *')

  useEffect(() => {
    const parts = cron.split(' ')
    if (parts.length === 5) {
      setCustomMinute(parseInt(parts[0]) || 0)
      setCustomHour(parseInt(parts[1]) || 0)
      setSelectedPreset(cron)
    }
    if (cron.startsWith('0 2 * * *')) {
      setMode('preset')
    } else if (parts.length === 5 && parts[4] !== '*') {
      setMode('weekly')
      const days = parts[4].split(',').map(Number).filter(n => !isNaN(n))
      if (days.length) setWeekDays(days)
    } else {
      setMode('custom')
    }
  }, [cron, open])

  const presetGroups = [
    { title: '每天', items: [
      { label: '02:00', value: '0 2 * * *' }, { label: '03:00', value: '0 3 * * *' },
      { label: '06:00', value: '0 6 * * *' }, { label: '12:00', value: '0 12 * * *' },
    ]},
    { title: '每周', items: [
      { label: '周日', value: '0 2 * * 0' }, { label: '周一', value: '0 2 * * 1' },
      { label: '周五', value: '0 2 * * 5' }, { label: '周六', value: '0 2 * * 6' },
    ]},
    { title: '每月', items: [
      { label: '1号', value: '0 2 1 * *' }, { label: '15号', value: '0 2 15 * *' },
    ]},
  ]

  const weekDayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

  const toggleWeekDay = (day: number) => {
    setWeekDays(prev => prev.includes(day) ? prev.filter(d => d !== day).sort() : [...prev, day].sort())
  }

  const handleSave = () => {
    if (mode === 'weekly' && weekDays.length === 0) {
      message.warning('请至少选择一个星期几'); return
    }
    let newCron = cron
    if (mode === 'preset') newCron = selectedPreset
    else if (mode === 'weekly') newCron = `${customMinute} ${customHour} * * ${weekDays.join(',')}`
    else if (mode === 'custom') newCron = `${customMinute} ${customHour} * * *`
    onChange(newCron)
    message.success(`已设置为 ${cronToText(newCron)}`)
    onClose()
  }

  const presetCardStyle = (val: string) => ({
    padding: '8px 14px', borderRadius: 8,
    border: `1.5px solid ${val === selectedPreset ? '#5b46f5' : '#f0f0f0'}`,
    background: val === selectedPreset ? '#f5f3ff' : '#fafafa',
    color: val === selectedPreset ? '#5b46f5' : '#666',
    fontWeight: val === selectedPreset ? 600 : 400,
    cursor: 'pointer', textAlign: 'center' as const, fontSize: 13,
    transition: 'all 0.15s', userSelect: 'none' as const,
  })

  return (
    <Modal
      title={title || '设置同步时间'}
      open={open} onCancel={onClose}
      destroyOnClose={false} maskClosable width={480}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="save" type="primary" onClick={handleSave}>确定</Button>,
      ]}
    >
      <Alert
        message={cronToText(cron)}
        description={<Text code style={{ fontSize: 11 }}>{cron}</Text>}
        type="info" showIcon style={{ marginBottom: 16, fontSize: 13 }}
      />

      <Tabs defaultActiveKey="preset" onChange={key => setMode(key as typeof mode)} items={[
        {
          key: 'preset', label: '快速选择',
          children: (
            <div>
              {presetGroups.map(group => (
                <div key={group.title} style={{ marginBottom: 14 }}>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{group.title}</Text>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {group.items.map(p => (
                      <div key={p.value} style={presetCardStyle(p.value)} onClick={() => setSelectedPreset(p.value)}>{p.label}</div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ),
        },
        {
          key: 'custom', label: '自定义',
          children: (
            <div>
              <Text type="secondary" style={{ display: 'block', marginBottom: 16, fontSize: 13 }}>
                每天在固定时间执行同步
              </Text>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
                <Select value={customHour} onChange={v => setCustomHour(v)} style={{ width: 100 }}
                  options={Array.from({ length: 24 }, (_, i) => ({ label: `${i.toString().padStart(2, '0')}`, value: i }))} />
                <Text style={{ fontSize: 20, fontWeight: 600 }}>:</Text>
                <Select value={customMinute} onChange={v => setCustomMinute(v)} style={{ width: 100 }}
                  options={[{ label: '00', value: 0 }, { label: '15', value: 15 }, { label: '30', value: 30 }, { label: '45', value: 45 }]} />
              </div>
              <div style={{ textAlign: 'center', marginTop: 12 }}>
                <Tag color="blue">{cronToText(`${customMinute} ${customHour} * * *`)}</Tag>
              </div>
            </div>
          ),
        },
        {
          key: 'weekly', label: '每周指定日',
          children: (
            <div>
              <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 13 }}>
                选择星期几，设定执行时间
              </Text>
              <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 16 }}>
                {weekDayLabels.map((label, idx) => (
                  <div key={idx} onClick={() => toggleWeekDay(idx)} style={{
                    width: 32, height: 32, borderRadius: '50%',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 12, cursor: 'pointer', userSelect: 'none',
                    background: weekDays.includes(idx) ? '#5b46f5' : '#f5f5f5',
                    color: weekDays.includes(idx) ? '#fff' : '#999',
                    fontWeight: weekDays.includes(idx) ? 600 : 400,
                    transition: 'all 0.15s',
                  }}>{label.slice(1)}</div>
                ))}
              </div>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'center' }}>
                <Select value={customHour} onChange={v => setCustomHour(v)} style={{ width: 100 }}
                  options={Array.from({ length: 24 }, (_, i) => ({ label: `${i.toString().padStart(2, '0')}`, value: i }))} />
                <Text style={{ fontSize: 20, fontWeight: 600 }}>:</Text>
                <Select value={customMinute} onChange={v => setCustomMinute(v)} style={{ width: 100 }}
                  options={[{ label: '00', value: 0 }, { label: '15', value: 15 }, { label: '30', value: 30 }, { label: '45', value: 45 }]} />
              </div>
              <div style={{ textAlign: 'center', marginTop: 12 }}>
                <Tag color="blue">{weekDays.length ? `每周${weekDays.map(d => weekDayLabels[d]).join('、')} ${customHour}:${customMinute.toString().padStart(2, '0')}` : '请选择星期几'}</Tag>
              </div>
            </div>
          ),
        },
      ]} />
    </Modal>
  )
}

/* ------------------------------------------------------------------ */
/*                      定时任务设置面板                               */
/* ------------------------------------------------------------------ */

export default function ScheduleSettingsPanel({ workflows }: { workflows: { id: number; workflow_name: string }[] }) {
  const [loading, setLoading] = useState(true)
  const [schedules, setSchedules] = useState<ScheduleItem[]>([])
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()
  const [selectedWfId, setSelectedWfId] = useState<number | null>(null)
  const [cronModalOpen, setCronModalOpen] = useState(false)
  const [cronField, setCronField] = useState<'cron' | 'incremental_cron'>('cron')
  const watchedMode = Form.useWatch('sync_mode', form)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.get('/release-management/sync/schedules')
      setSchedules(data)
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const selectedSchedule = useMemo(() =>
    schedules.find(s => s.workflow_id === selectedWfId),
    [schedules, selectedWfId]
  )
  const currentMode = (watchedMode || 'incremental') as string

  // 切换工作流时初始化表单
  useEffect(() => {
    if (selectedWfId != null) {
      const existing = schedules.find(s => s.workflow_id === selectedWfId)
      const mode = existing ? existing.sync_mode : 'incremental'
      form.setFieldsValue(existing ? {
        sync_mode: mode,
        is_active: existing.is_active,
        cron: existing.cron_expression,
        incremental_cron: existing.cron_expression,
        recent_days: existing.recent_days,
        range_days: existing.recent_days,
      } : {
        sync_mode: 'incremental',
        is_active: false,
        cron: '0 2 * * *',
        incremental_cron: '0 6 * * *',
        recent_days: 30,
        range_days: 30,
      })
    } else {
      form.resetFields()
    }
  }, [selectedWfId, schedules, form])

  const handleSave = async () => {
    if (selectedWfId == null) { message.warning('请先选择工作流'); return }
    try {
      setSaving(true)
      const v = form.getFieldsValue(['sync_mode', 'is_active', 'cron', 'incremental_cron', 'recent_days', 'range_days'])
      await api.post('/release-management/sync/schedules', {
        workflow_id: selectedWfId,
        sync_mode: v.sync_mode || 'incremental',
        is_active: v.is_active ?? false,
        cron_expression: v.sync_mode === 'full' ? (v.cron || '0 2 * * *') : (v.incremental_cron || '0 6 * * *'),
        recent_days: v.sync_mode === 'range' ? (v.range_days ?? 30) : (v.recent_days ?? 30),
        extra: { full_cron: v.cron, incremental_cron: v.incremental_cron, range_days: v.range_days },
      })
      message.success('定时设置已保存')
      load()
    } catch { message.error('保存失败') }
    finally { setSaving(false) }
  }

  const handleCronSave = (newCron: string) => {
    form.setFieldValue(cronField, newCron)
  }

  const currentCron = currentMode === 'full'
    ? (form.getFieldValue('cron') || '0 2 * * *')
    : (form.getFieldValue('incremental_cron') || '0 6 * * *')

  return (
    <Spin spinning={loading}>
      <Card size="small" style={cardStyle} bodyStyle={{ padding: '24px 32px' }}
        title={
          <Space>
            <ClockCircleOutlined style={{ color: '#722ed1', fontSize: 18 }} />
            <span style={{ fontWeight: 600 }}>定时同步设置</span>
          </Space>
        }
      >
        {/* WorkFlow selector */}
        <div style={{ marginBottom: 20 }}>
          <Text type="secondary" style={{ fontSize: 12, marginBottom: 6, display: 'block' }}>
            选择工作流进行独立定时配置
          </Text>
          <Select
            value={selectedWfId} onChange={setSelectedWfId}
            style={{ width: 300 }} placeholder="选择工作流"
            showSearch allowClear
            filterOption={(input, option) =>
              String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
            }
            options={workflows.map(w => ({ label: w.workflow_name, value: w.id }))}
          />
        </div>

        {selectedWfId != null ? (
          <>
            {/* 同步模式 */}
            <Descriptions bordered size="small" column={1} style={{ marginBottom: 0 }}
              labelStyle={{ width: 250, textAlign: 'left' }}>
              <Descriptions.Item label={
                <Space><RocketOutlined style={{ color: '#fa8c16' }} /><span>同步模式</span></Space>
              }>
                <Form form={form} style={{ marginBottom: 0 }}>
                  <Form.Item name="sync_mode" style={{ marginBottom: 0 }}>
                    <Radio.Group>
                      <Radio.Button value="full">全量</Radio.Button>
                      <Radio.Button value="incremental">增量</Radio.Button>
                      <Radio.Button value="range">范围</Radio.Button>
                    </Radio.Group>
                  </Form.Item>
                </Form>
              </Descriptions.Item>

              {/* 全量：显示触发行 */}
              {currentMode === 'full' && (
                <Descriptions.Item label={
                  <Space><RocketOutlined style={{ color: '#fa8c16' }} /><span>全量同步触发时间</span></Space>
                }>
                  <Space>
                    <Tag color="purple" style={{ padding: '4px 14px', fontSize: 13 }}>
                      {cronToText(form.getFieldValue('cron') || '0 2 * * *')}
                    </Tag>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {form.getFieldValue('cron') || '0 2 * * *'}
                    </Text>
                    <Button type="link" size="small" onClick={() => { setCronField('cron'); setCronModalOpen(true) }}>修改</Button>
                  </Space>
                </Descriptions.Item>
              )}

              {/* 增量：显示 CRON + 间隔 */}
              {currentMode === 'incremental' && (
                <>
                  <Descriptions.Item label={
                    <Space><RocketOutlined style={{ color: '#722ed1' }} /><span>触发时间</span></Space>
                  }>
                    <Space>
                      <Tag color="blue" style={{ padding: '4px 14px', fontSize: 13 }}>
                        {cronToText(form.getFieldValue('incremental_cron') || '0 6 * * *')}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {form.getFieldValue('incremental_cron') || '0 6 * * *'}
                      </Text>
                      <Button type="link" size="small" onClick={() => { setCronField('incremental_cron'); setCronModalOpen(true) }}>修改</Button>
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label={
                    <Space><RocketOutlined style={{ color: '#13c2c2' }} /><span>回溯天数</span></Space>
                  }>
                    <Space>
                      <Form form={form}>
                        <Form.Item name="recent_days" style={{ marginBottom: 0, display: 'inline-block' }}>
                          <InputNumber min={1} max={365} step={1} style={{ width: 120 }} addonAfter="天" />
                        </Form.Item>
                      </Form>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        每次同步最近 {form.getFieldValue('recent_days') ?? 30} 天的构建记录
                      </Text>
                    </Space>
                  </Descriptions.Item>
                </>
              )}

              {/* 范围：显示范围天数 */}
              {currentMode === 'range' && (
                <>
                  <Descriptions.Item label={
                    <Space><RocketOutlined style={{ color: '#13c2c2' }} /><span>触发时间</span></Space>
                  }>
                    <Space>
                      <Tag color="blue" style={{ padding: '4px 14px', fontSize: 13 }}>
                        {cronToText(form.getFieldValue('incremental_cron') || '0 6 * * *')}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {form.getFieldValue('incremental_cron') || '0 6 * * *'}
                      </Text>
                      <Button type="link" size="small" onClick={() => { setCronField('incremental_cron'); setCronModalOpen(true) }}>修改</Button>
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label={
                    <Space><RocketOutlined style={{ color: '#52c41a' }} /><span>每次同步天数</span></Space>
                  }>
                    <Space>
                      <Form form={form}>
                        <Form.Item name="range_days" style={{ marginBottom: 0, display: 'inline-block' }}>
                          <InputNumber min={1} max={365} step={1} style={{ width: 120 }} addonAfter="天" />
                        </Form.Item>
                      </Form>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        每次触发后同步过去 {form.getFieldValue('range_days') ?? 30} 天内的构建
                      </Text>
                    </Space>
                  </Descriptions.Item>
                </>
              )}
            </Descriptions>

            <Divider style={{ margin: '16px 0' }} />

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Space>
                <Form form={form}>
                  <Form.Item name="is_active" valuePropName="checked" style={{ marginBottom: 0 }}>
                    <Switch size="small" checkedChildren="开" unCheckedChildren="关" />
                  </Form.Item>
                </Form>
                <Text type="secondary" style={{ fontSize: 12 }}>启用定时同步</Text>
              </Space>
              <Space>
                {selectedSchedule?.last_triggered_at && (
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    上次触发: {formatTimezone(selectedSchedule!.last_triggered_at, 'YYYY-MM-DD HH:mm')}
                  </Text>
                )}
                <Button type="primary" loading={saving} icon={<SaveOutlined />} onClick={handleSave}>
                  保存设置
                </Button>
              </Space>
            </div>
            <Text type="secondary" style={{ fontSize: 11, display: 'block', marginTop: 8 }}>
              修改后需保存，定时任务将在下一次 Scheduler 调度时生效
            </Text>
          </>
        ) : (
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#bfbfbf' }}>
            请先选择工作流以进行定时同步配置
          </div>
        )}
      </Card>

      <CronModal
        open={cronModalOpen}
        cron={currentCron}
        onChange={handleCronSave}
        onClose={() => setCronModalOpen(false)}
        title="设置同步触发时间"
      />

      {/* 已配置的定时任务列表 */}
      {schedules.length > 0 && (
        <Card size="small" style={{ ...cardStyle, marginTop: 16 }}
          title={
            <Space>
              <ClockCircleOutlined style={{ color: '#722ed1', fontSize: 14 }} />
              <span style={{ fontWeight: 600 }}>已配置的定时任务</span>
            </Space>
          }
          bodyStyle={{ padding: '12px 16px' }}
        >
          <Table
            dataSource={schedules}
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              {
                title: '工作流', dataIndex: 'workflow_name', width: 220, render: (v: string, r: ScheduleItem) => (
                  <Space>
                    <Text strong style={{ fontSize: 13 }}>{v}</Text>
                    {r.workflow_id === selectedWfId && <Tag color="blue" style={{ fontSize: 11 }}>当前</Tag>}
                  </Space>
                ),
              },
              {
                title: '模式', dataIndex: 'sync_mode', width: 80,
                render: (v: string) => <Tag color="cyan">{modeLabels[v] || v}</Tag>,
              },
              {
                title: '触发时间', dataIndex: 'cron_expression', width: 160,
                render: (v: string) => <Tag color="purple" style={{ fontSize: 12 }}>{cronToText(v)}</Tag>,
              },
              {
                title: '范围', width: 100, render: (_: any, r: ScheduleItem) => {
                  if (r.sync_mode === 'full') return <Text style={{ fontSize: 12 }}>全部历史</Text>
                  return <Text style={{ fontSize: 12 }}>{r.recent_days}天</Text>
                },
              },
              {
                title: '状态', dataIndex: 'is_active', width: 80,
                render: (v: boolean) => v ? <Tag color="green" style={{ fontSize: 11 }}>启用</Tag> : <Tag color="default" style={{ fontSize: 11 }}>停用</Tag>,
              },
              {
                title: '操作', key: 'action', width: 120,
                render: (_: any, r: ScheduleItem) => (
                  <Space size="middle">
                    <Button
                      type="link" size="middle"
                      onClick={() => setSelectedWfId(r.workflow_id)}
                    >
                      编辑
                    </Button>
                    <Button
                      type="link" size="middle" danger
                      icon={<DeleteOutlined />}
                      onClick={async () => {
                        try {
                          await api.delete(`/release-management/sync/schedules/${r.id}`)
                          message.success('已删除')
                          load()
                        } catch { message.error('删除失败') }
                      }}
                    >
                      删除
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      )}
    </Spin>
  )
}
