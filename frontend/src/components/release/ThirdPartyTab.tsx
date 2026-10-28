import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Row,
  Space,
  Statistic,
  Switch,
  Tabs,
  Table,
  Tag,
  Tooltip,
} from 'antd'
import type { TabsProps } from 'antd'
import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from '@ant-design/icons'

import {
  getThirdPartyOverview,
  getVersionHistory,
  getBaselines,
  createOrUpdateBaseline,
  deleteBaseline,
  type ThirdPartyComponent,
  type ThirdPartyBaseline,
  type ThirdPartyVersionHistory,
} from '@/services/releaseManagement.ts'
import { formatTimezone } from '@/utils/timezone'

/* ===================== KPI 面板 ===================== */

interface StatCardProps {
  title: string
  value: number
  color: string
  icon: React.ReactNode
  trend?: string
}

const STAT_COLORS: Record<string, { bg: string; border: string }> = {
  blue: { bg: '#e6f4ff', border: '#91caff' },
  green: { bg: '#f6ffed', border: '#b7eb8f' },
  red: { bg: '#fff2f0', border: '#ffccc7' },
  gray: { bg: '#fafafa', border: '#d9d9d9' },
}

function StatCard({ title, value, color, icon, trend }: StatCardProps) {
  const scheme = STAT_COLORS[color] || STAT_COLORS.gray
  return (
    <div
      style={{
        background: '#fff',
        borderRadius: 12,
        border: `1px solid ${scheme.border}`,
        padding: '20px 24px',
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        transition: 'box-shadow 0.2s',
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 12,
          background: scheme.bg,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 22, color }}>{icon}</span>
      </div>
      <div>
        <div style={{ fontSize: 12, color: '#8c8c8c', fontWeight: 500, marginBottom: 2 }}>
          {title}
        </div>
        <div style={{ fontSize: 28, fontWeight: 700, color, lineHeight: 1 }}>
          {value}
        </div>
        {trend && (
          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4 }}>{trend}</div>
        )}
      </div>
    </div>
  )
}

/* ===================== 基线状态标签 ===================== */

function BaselineStatusTag({ status }: { status?: string }) {
  const map: Record<string, { color: string; label: string }> = {
    compliant: { color: 'success', label: '合规' },
    violation: { color: 'error', label: '违规' },
    no_baseline: { color: 'default', label: '无基线' },
  }
  const info = map[status || ''] || map.no_baseline
  return <Tag color={info.color}>{info.label}</Tag>
}

/* ===================== 版本历史 Drawer ===================== */

function VersionHistoryDrawer({
  open, onClose, componentName,
}: {
  open: boolean
  onClose: () => void
  componentName: string | null
}) {
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<ThirdPartyVersionHistory[]>([])

  useEffect(() => {
    if (!open || !componentName) return
    setLoading(true)
    getVersionHistory(componentName, 50)
      .then((data) => setHistory(data))
      .catch(() => message.error('加载版本历史失败'))
      .finally(() => setLoading(false))
  }, [open, componentName])

  return (
    <Drawer
      title={`版本历史 — ${componentName}`}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
    >
      <Table
        dataSource={history}
        rowKey={(r) => `${r.run_id}-${r.version}`}
        size="small"
        loading={loading}
        scroll={{ y: 480 }}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: '版本', dataIndex: 'version', width: 120, render: (v: string) => <Tag>{v}</Tag> },
          { title: 'Run ID', dataIndex: 'run_id', width: 80 },
          { title: '分支', dataIndex: 'branch', ellipsis: true },
          {
            title: '使用日期', dataIndex: 'used_at', width: 160,
            render: (v: string) => formatTimezone(v),
          },
        ]}
      />
    </Drawer>
  )
}

/* ===================== 基线管理 Modal ===================== */

function BaselineModal({
  open, onClose, editingBaseline, refreshBaselines,
}: {
  open: boolean
  onClose: () => void
  editingBaseline: ThirdPartyBaseline | null
  refreshBaselines: () => void
}) {
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (editingBaseline) {
      form.setFieldsValue({
        component_name: editingBaseline.component_name,
        component_category: editingBaseline.component_category,
        approved_versions: editingBaseline.approved_versions.join(', '),
        min_version: editingBaseline.min_version,
        max_version: editingBaseline.max_version,
        notes: editingBaseline.notes,
        is_active: editingBaseline.is_active,
      })
    } else {
      form.resetFields()
    }
  }, [editingBaseline, open, form])

  const handleSubmit = async () => {
    const values = await form.validateFields()
    setSubmitting(true)
    try {
      await createOrUpdateBaseline({
        component_name: values.component_name,
        component_category: editingBaseline?.component_category,
        approved_versions: values.approved_versions
          .split(',')
          .map((v: string) => v.trim())
          .filter(Boolean),
        min_version: values.min_version || null,
        max_version: values.max_version || null,
        notes: values.notes || null,
        is_active: values.is_active ?? true,
      })
      message.success('基线保存成功')
      form.resetFields()
      onClose()
      refreshBaselines()
    } catch {
      message.error('保存失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title={editingBaseline ? '编辑基线' : '新增基线'}
      open={open}
      onCancel={onClose}
      destroyOnClose
      maskClosable={false}
      onOk={handleSubmit}
      confirmLoading={submitting}
      width={600}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="component_name"
          label="组件名称"
          rules={[{ required: true, message: '请输入组件名称' }]}
        >
          <Input placeholder="例如: torch" readOnly={!!editingBaseline} />
        </Form.Item>
        <Form.Item name="approved_versions" label="批准版本（逗号分隔）" rules={[{ required: true, message: '请输入批准版本' }]}>
          <Input placeholder="例如: 2.0.0, 2.1.0, 2.2.0" />
        </Form.Item>
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item name="min_version" label="最低版本">
              <Input placeholder="例如: 2.0.0" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="max_version" label="最高版本">
              <Input placeholder="例如: 2.5.0" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={2} placeholder="可选说明" />
        </Form.Item>
        <Form.Item name="is_active" label="启用" valuePropName="checked" initialValue={true}>
          <Switch checkedChildren="启用" unCheckedChildren="停用" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

/* ===================== 安全评分仪表 ===================== */

function ComplianceMeter({ compliant, total }: { compliant: number; total: number }) {
  const rate = total > 0 ? Math.round(compliant / total * 100) : 0
  let color = '#d9d9d9'
  if (rate >= 80) color = '#52c41a'
  else if (rate >= 60) color = '#faad14'
  else if (rate > 0) color = '#ff4d4f'

  return (
    <div style={{ textAlign: 'center' }}>
      <div
        style={{
          width: 80,
          height: 80,
          borderRadius: '50%',
          border: `6px solid ${color}`,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: 20, fontWeight: 700, color }}>{rate}%</span>
      </div>
      <div style={{ fontSize: 12, color: '#8c8c8c' }}>合规率</div>
    </div>
  )
}

/* ===================== 主组件 ===================== */

interface ThirdPartyTabProps {
  selectedIds: number[] | null
}

export default function ThirdPartyTab({ selectedIds }: ThirdPartyTabProps) {
  const [loading, setLoading] = useState(false)
  const [components, setComponents] = useState<ThirdPartyComponent[]>([])
  const [baselines, setBaselines] = useState<ThirdPartyBaseline[]>([])

  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false)
  const [historyComponent, setHistoryComponent] = useState<string | null>(null)
  const [baselineModalOpen, setBaselineModalOpen] = useState(false)
  const [editingBaseline, setEditingBaseline] = useState<ThirdPartyBaseline | null>(null)

  const [initialized, setInitialized] = useState(false)

  const loadComponents = useCallback((ids: number[] | null) => {
    if (!ids || ids.length === 0) {
      setComponents([])
      return
    }
    setLoading(true)
    getThirdPartyOverview(ids)
      .then(setComponents)
      .catch(() => message.error('加载三方件数据失败'))
      .finally(() => setLoading(false))
  }, [])

  const loadBaselines = useCallback(() => {
    getBaselines()
      .then(setBaselines)
      .catch(() => message.error('加载基线数据失败'))
  }, [])

  /* Lazy-load: only fetch when first rendered with valid selectedIds */
  useEffect(() => {
    if (initialized || !selectedIds || selectedIds.length === 0) return
    setInitialized(true)
    loadComponents(selectedIds)
    loadBaselines()
  }, [selectedIds, initialized, loadComponents, loadBaselines])

  /* Reload when sync completes (listen for parent event) */
  useEffect(() => {
    const handler = () => {
      if (selectedIds && selectedIds.length > 0) {
        loadComponents(selectedIds)
      }
    }
    window.addEventListener('release-sync-completed', handler)
    return () => window.removeEventListener('release-sync-completed', handler)
  }, [selectedIds, loadComponents])

  const kpiData = useMemo(() => {
    const total = components.length
    const compliant = components.filter((c) => c.baseline_status === 'compliant').length
    const violations = components.filter((c) => c.baseline_status === 'violation').length
    const noBaseline = components.filter((c) => !c.baseline_status || c.baseline_status === 'no_baseline').length
    return { total, compliant, violations, noBaseline }
  }, [components])

  /* ---------- 三方件版本列 ---------- */
  const componentColumns = [
    {
      title: '组件名称',
      dataIndex: 'component_name',
      width: 160,
      render: (name: string) => (
        <a onClick={(e) => { e.stopPropagation(); setHistoryComponent(name); setHistoryDrawerOpen(true); }}>
          <SafetyOutlined style={{ marginRight: 4, color: '#1890ff' }} />
          {name}
        </a>
      ),
    },
    { title: '最新检测版本', dataIndex: 'latest_version', width: 130, render: (v: string) => v ? <Tag color="blue">{v}</Tag> : '-' },
    {
      title: '所有版本',
      dataIndex: 'versions',
      render: (versions: { version: string; usage_count: number }[]) => (
        <Space wrap>
          {versions.map((v) => (
            <Tooltip key={v.version} title={`使用 ${v.usage_count} 次`}>
              <Tag>{v.version}</Tag>
            </Tooltip>
          ))}
        </Space>
      ),
    },
    { title: '分类', dataIndex: 'component_category', width: 120, render: (v: string) => v ? <Tag color="cyan">{v}</Tag> : '-' },
    {
      title: '基线状态',
      dataIndex: 'baseline_status',
      width: 100,
      render: (status?: string) => <BaselineStatusTag status={status} />,
    },
  ]

  /* ---------- 基线列 ---------- */
  const baselineColumns = [
    { title: '组件', dataIndex: 'component_name', width: 140, render: (v: string) => <strong>{v}</strong> },
    { title: '分类', dataIndex: 'component_category', width: 100, render: (v: string) => v ? <Tag color="cyan">{v}</Tag> : '-' },
    {
      title: '批准版本',
      dataIndex: 'approved_versions',
      render: (v: string[]) => (
        <Space wrap>
          {v.map((ver) => (
            <Tag key={ver} color="green">{ver}</Tag>
          ))}
        </Space>
      ),
    },
    { title: '版本范围', width: 180, render: (_: unknown, r: ThirdPartyBaseline) => `${r.min_version || '-'} ~ ${r.max_version || '-'}` },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 70,
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag>,
    },
    {
      title: '操作',
      width: 120,
      fixed: 'right' as const,
      render: (_: unknown, record: ThirdPartyBaseline) => (
        <Space>
          <a onClick={() => { setEditingBaseline(record); setBaselineModalOpen(true); }}>
            <EditOutlined /> 编辑
          </a>
          <Popconfirm
            title="确认删除此基线？"
            onConfirm={async () => {
              await deleteBaseline(record.id)
              message.success('删除成功')
              loadBaselines()
            }}
            okText="确认"
            cancelText="取消"
          >
            <a style={{ color: '#ff4d4f' }}><DeleteOutlined /> 删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  /* ---------- Tabs ---------- */
  const tabItems: TabsProps['items'] = [
    {
      key: 'overview',
      label: (
        <span>
          <FolderOpenOutlined />
          组件概览
          {components.length > 0 && <Tag style={{ marginLeft: 8 }}>{kpiData.total}</Tag>}
        </span>
      ),
      children: (
        <Card
          bordered={false}
          extra={
            <Button size="small" icon={<ReloadOutlined />} onClick={() => { setInitialized(false); loadComponents(selectedIds); }}>
              刷新
            </Button>
          }
        >
          {loading ? (
            <div style={{ textAlign: 'center', padding: 60 }}>
              <ReloadOutlined style={{ fontSize: 24, color: '#1890ff', animation: 'spin 1s linear infinite' }} />
              <div style={{ marginTop: 8, color: '#8c8c8c' }}>加载中...</div>
            </div>
          ) : components.length > 0 ? (
            <Table
              dataSource={components}
              rowKey="component_name"
              size="small"
              scroll={{ x: 900 }}
              pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 个组件` }}
              columns={componentColumns}
              onRow={() => ({ style: { cursor: 'pointer' } })}
            />
          ) : (
            <Empty
              description={
                <span style={{ color: '#8c8c8c' }}>
                  暂无三方件数据，请先选择工作流并同步数据
                </span>
              }
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
        </Card>
      ),
    },
    {
      key: 'baselines',
      label: (
        <span>
          <CheckCircleOutlined />
          基线管理
          {baselines.length > 0 && <Tag color="green" style={{ marginLeft: 8 }}>{baselines.length}</Tag>}
        </span>
      ),
      children: (
        <Card
          bordered={false}
          extra={
            <Space>
              <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { setEditingBaseline(null); setBaselineModalOpen(true); }}>
                新增基线
              </Button>
              <Button size="small" icon={<ReloadOutlined />} onClick={loadBaselines}>
                刷新
              </Button>
            </Space>
          }
        >
          <Table
            dataSource={baselines}
            rowKey="id"
            size="small"
            scroll={{ x: 900 }}
            pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条基线` }}
            columns={baselineColumns}
          />
        </Card>
      ),
    },
  ]

  return (
    <div>
      {/* KPI Cards + Meter row */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <StatCard title="组件总数" value={kpiData.total} color="#1890ff" icon={<SafetyOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard
            title="合规组件"
            value={kpiData.compliant}
            color="#52c41a"
            icon={<CheckCircleOutlined />}
            trend={components.length ? `${Math.round(kpiData.compliant / components.length * 100)}% 合规率` : undefined}
          />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard title="基线违规" value={kpiData.violations} color="#ff4d4f" icon={<ExclamationCircleOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <StatCard title="无基线组件" value={kpiData.noBaseline} color="#8c8c8c" icon={<QuestionCircleOutlined />} />
        </Col>
      </Row>

      {/* Compliance Meter + Tabs */}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={4} style={{ display: 'flex', justifyContent: 'center' }}>
          <Card bordered={false} style={{ borderRadius: 12, width: '100%' }}>
            <ComplianceMeter compliant={kpiData.compliant} total={kpiData.total} />
          </Card>
        </Col>
        <Col xs={24} md={20}>
          <Tabs items={tabItems} size="large" />
        </Col>
      </Row>

      {/* Version History Drawer */}
      <VersionHistoryDrawer
        open={historyDrawerOpen}
        onClose={() => setHistoryDrawerOpen(false)}
        componentName={historyComponent}
      />

      {/* Baseline Modal */}
      <BaselineModal
        open={baselineModalOpen}
        onClose={() => { setBaselineModalOpen(false); setEditingBaseline(null); }}
        editingBaseline={editingBaseline}
        refreshBaselines={loadBaselines}
      />
    </div>
  )
}
