import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
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
  StopOutlined,
  SwapOutlined,
} from '@ant-design/icons'

import {
  getThirdPartyOverview,
  getVersionHistory,
  getBaselines,
  createOrUpdateBaseline,
  deleteBaseline,
  getThirdPartyRuns,
  getThirdPartyRunSnapshot,
  getThirdPartyRunDiff,
  type ThirdPartyComponent,
  type ThirdPartyBaseline,
  type ThirdPartyVersionHistory,
  type ThirdPartyRunItem,
  type ThirdPartySnapshot,
  type ThirdPartyRunDiff,
  type ThirdPartyDiffItem,
} from '@/services/releaseManagement.ts'
import { formatTimezone } from '@/utils/timezone'
import type { Workflow } from '@/services/releaseManagement'

const { Text } = Typography
const { Option } = Select

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

/* ===================== 版本变化标签 ===================== */

function ChangeTypeTag({ type }: { type: ThirdPartyDiffItem['change_type'] }) {
  const map: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
    unchanged: { color: 'default', label: '不变', icon: null },
    changed: { color: 'error', label: '变更', icon: <SwapOutlined /> },
    added: { color: 'success', label: '新增', icon: <PlusOutlined /> },
    removed: { color: 'warning', label: '移除', icon: <DeleteOutlined /> },
  }
  const info = map[type] || map.unchanged
  return (
    <Tag color={info.color}>
      {info.icon} {info.label}
    </Tag>
  )
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
    <Modal
      title={<span><SafetyOutlined style={{ marginRight: 6, color: '#1890ff' }} />版本历史 — {componentName}</span>}
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
    >
      <Table
        dataSource={history}
        rowKey={(r) => `${r.run_id}-${r.version}`}
        size="small"
        loading={loading}
        scroll={{ y: 400 }}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: '版本', dataIndex: 'version', width: 120, render: (v: string) => <Tag>{v}</Tag> },
          { title: 'Run ID', dataIndex: 'run_id', width: 80 },
          { title: '分支', dataIndex: 'branch', ellipsis: true },
          { title: '时间', dataIndex: 'used_at', width: 160, render: (v: string) => formatTimezone(v) },
        ]}
      />
    </Modal>
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
        <Form.Item name="component_name" label="组件名称" rules={[{ required: true, message: '请输入组件名称' }]}>
          <Input placeholder="例如: torch" readOnly={!!editingBaseline} />
        </Form.Item>
        <Form.Item name="approved_versions" label="批准版本（逗号分隔）" rules={[{ required: true, message: '请输入批准版本' }]}>
          <Input placeholder="例如: 2.0.0, 2.1.0" />
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

/* ===================== 主组件 ===================== */

interface ThirdPartyTabProps {
  selectedIds: number[] | null
  workflows?: Workflow[]
}

export default function ThirdPartyTab({ selectedIds, workflows: externalWorkflows }: ThirdPartyTabProps) {
  /* ─── 工作流 & 执行选择 ─── */
  const [wfId, setWfId] = useState<number | undefined>()
  const [runs, setRuns] = useState<ThirdPartyRunItem[]>([])
  const [runsLoading, setRunsLoading] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<number | undefined>()

  /* ─── 快照 & 差异 ─── */
  const [snapshot, setSnapshot] = useState<ThirdPartySnapshot | null>(null)
  const [snapshotLoading, setSnapshotLoading] = useState(false)
  const [diff, setDiff] = useState<ThirdPartyRunDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  /* ─── 基线 ─── */
  const [baselines, setBaselines] = useState<ThirdPartyBaseline[]>([])
  const [baselineModalOpen, setBaselineModalOpen] = useState(false)
  const [editingBaseline, setEditingBaseline] = useState<ThirdPartyBaseline | null>(null)
  const [overviewComponents, setOverviewComponents] = useState<ThirdPartyComponent[]>([])

  /* ─── 版本历史 ─── */
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false)
  const [historyComponent, setHistoryComponent] = useState<string | null>(null)

  /* ─── 工作流列表 ─── */
  const wfOptions = useMemo(() => {
    if (externalWorkflows) return externalWorkflows
    if (selectedIds && selectedIds.length > 0) return []
    return []
  }, [externalWorkflows, selectedIds])

  /* ─── 加载执行列表 ─── */
  const loadRuns = useCallback(async (wf: number | undefined) => {
    setRunsLoading(true)
    setRuns([])
    setSelectedRunId(undefined)
    setSnapshot(null)
    setDiff(null)
    try {
      const data = await getThirdPartyRuns(wf, 50)
      setRuns(data)
      if (data.length > 0) {
        setSelectedRunId(data[0].id)
      }
    } catch {
      message.error('加载执行列表失败')
    } finally {
      setRunsLoading(false)
    }
  }, [])

  /* ─── 加载快照 ─── */
  const loadSnapshot = useCallback(async (runId: number) => {
    setSnapshotLoading(true)
    setSnapshot(null)
    setDiff(null)
    try {
      const [snap, diffData] = await Promise.all([
        getThirdPartyRunSnapshot(runId),
        getThirdPartyRunDiff(runId).catch(() => null),
      ])
      setSnapshot(snap)
      setDiff(diffData)
    } catch {
      message.error('加载版本快照失败')
    } finally {
      setSnapshotLoading(false)
    }
  }, [])

  /* ─── 加载基线 ─── */
  const loadBaselines = useCallback(() => {
    getBaselines().then(setBaselines).catch(() => {})
  }, [])

  /* ─── 加载概览 ─── */
  const loadOverview = useCallback(() => {
    const ids = selectedIds && selectedIds.length > 0 ? selectedIds : (wfId ? [wfId] : null)
    getThirdPartyOverview(ids).then(setOverviewComponents).catch(() => {})
  }, [selectedIds, wfId])

  /* ─── 初始化 ─── */
  const initialized = useRef(false)
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    loadBaselines()
    loadOverview()
    if (selectedIds && selectedIds.length > 0) {
      setWfId(selectedIds[0])
      loadRuns(selectedIds[0])
    }
  }, [])

  /* ─── 工作流切换 ─── */
  useEffect(() => {
    if (wfId) loadRuns(wfId)
  }, [wfId, loadRuns])

  /* ─── 执行切换 ─── */
  useEffect(() => {
    if (selectedRunId) loadSnapshot(selectedRunId)
  }, [selectedRunId, loadSnapshot])

  /* ─── 组件列 ─── */
  const componentColumns = [
    {
      title: '组件',
      dataIndex: 'component_name',
      width: 200,
      render: (name: string, r: ThirdPartySnapshot['components'][0]) => (
        <Space>
          <a onClick={() => { setHistoryComponent(name); setHistoryDrawerOpen(true); }}>
            <SafetyOutlined style={{ marginRight: 4, color: '#1890ff' }} />
            {name}
          </a>
          {r.repository_url && (
            <a href={r.repository_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: '#999' }}>
              仓库 →
            </a>
          )}
        </Space>
      ),
    },
    {
      title: '版本',
      dataIndex: 'component_version',
      width: 160,
      render: (v: string, r: ThirdPartySnapshot['components'][0]) => {
        const changed = diff?.diffs.find(d => d.component_name === r.component_name)
        const isChanged = changed && changed.change_type === 'changed'
        return (
          <Space>
            <Tag color={isChanged ? 'red' : 'blue'}>{v}</Tag>
            {changed && changed.previous_version && (
              <Text type="secondary" style={{ fontSize: 11, textDecoration: 'line-through' }}>
                {changed.previous_version}
              </Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '基线',
      dataIndex: 'baseline_status',
      width: 80,
      render: (status: string) => <BaselineStatusTag status={status} />,
    },
    {
      title: '批准版本',
      dataIndex: 'approved_versions',
      render: (v: string[]) => v.length > 0 ? (
        <Space wrap size={4}>
          {v.map(ver => <Tag key={ver} color="green" style={{ fontSize: 11 }}>{ver}</Tag>)}
        </Space>
      ) : <Text type="secondary">—</Text>,
    },
  ]

  /* ─── 差异对比列 ─── */
  const diffColumns = [
    { title: '组件', dataIndex: 'component_name', width: 180, render: (v: string) => <strong>{v}</strong> },
    {
      title: '上次版本',
      dataIndex: 'previous_version',
      width: 140,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '当前版本',
      dataIndex: 'current_version',
      width: 140,
      render: (v: string | null) => v ? <Tag color="blue">{v}</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '变化',
      dataIndex: 'change_type',
      width: 100,
      render: (type: string) => <ChangeTypeTag type={type as ThirdPartyDiffItem['change_type']} />,
    },
  ]

  /* ─── 基线列 ─── */
  const baselineColumns = [
    { title: '组件', dataIndex: 'component_name', width: 140, render: (v: string) => <strong>{v}</strong> },
    { title: '分类', dataIndex: 'component_category', width: 100, render: (v: string) => v ? <Tag color="cyan">{v}</Tag> : '-' },
    {
      title: '批准版本',
      dataIndex: 'approved_versions',
      render: (v: string[]) => (
        <Space wrap>{v.map(ver => <Tag key={ver} color="green">{ver}</Tag>)}</Space>
      ),
    },
    { title: '版本范围', width: 180, render: (_: unknown, r: ThirdPartyBaseline) => `${r.min_version || '-'} ~ ${r.max_version || '-'}` },
    { title: '状态', dataIndex: 'is_active', width: 70, render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '停用'}</Tag> },
    {
      title: '操作',
      width: 120,
      fixed: 'right' as const,
      render: (_: unknown, record: ThirdPartyBaseline) => (
        <Space>
          <a onClick={() => { setEditingBaseline(record); setBaselineModalOpen(true); }}><EditOutlined /> 编辑</a>
          <Popconfirm
            title="确认删除此基线？"
            onConfirm={async () => { await deleteBaseline(record.id); message.success('删除成功'); loadBaselines() }}
            okText="确认" cancelText="取消"
          >
            <a style={{ color: '#ff4d4f' }}><DeleteOutlined /> 删除</a>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  /* ─── 概览组件列 ─── */
  const overviewColumns = [
    {
      title: '组件名称',
      dataIndex: 'component_name',
      width: 160,
      render: (name: string) => (
        <a onClick={() => { setHistoryComponent(name); setHistoryDrawerOpen(true); }}>
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
        <Space wrap>{versions.map(v => <Tooltip key={v.version} title={`使用 ${v.usage_count} 次`}><Tag>{v.version}</Tag></Tooltip>)}</Space>
      ),
    },
    { title: '分类', dataIndex: 'component_category', width: 120, render: (v: string) => v ? <Tag color="cyan">{v}</Tag> : '-' },
    { title: '基线状态', dataIndex: 'baseline_status', width: 100, render: (status?: string) => <BaselineStatusTag status={status} /> },
  ]

  /* ─── Tab 1: 版本快照 ─── */
  const snapshotTab = (
    <Card bordered={false}>
      {/* 选择器 */}
      <Row gutter={[16, 12]} style={{ marginBottom: 16 }} align="middle">
        <Col>
          <Select
            value={wfId}
            onChange={(v) => { setWfId(v); setSelectedRunId(undefined) }}
            style={{ width: 240 }}
            placeholder="选择工作流"
            showSearch
            filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            options={wfOptions.map(w => ({ label: w.workflow_name, value: w.id }))}
          />
        </Col>
        <Col>
          <Select
            value={selectedRunId}
            onChange={setSelectedRunId}
            style={{ width: 320 }}
            placeholder="选择构建执行"
            loading={runsLoading}
            showSearch
            filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            options={runs.map(r => ({
              label: `#${r.github_run_id} — ${r.branch || 'unknown'} (${r.commit_sha || ''}) [${r.component_count}个组件]`,
              value: r.id,
            }))}
          />
        </Col>
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => { if (selectedRunId) loadSnapshot(selectedRunId); if (wfId) loadRuns(wfId) }}>
            刷新
          </Button>
        </Col>
      </Row>

      {/* 执行信息 */}
      {snapshot && (
        <Descriptions
          size="small"
          column={4}
          bordered
          style={{ marginBottom: 16 }}
          items={[
            { key: '1', label: 'Run #', children: <Tag color="blue">#{snapshot.github_run_id}</Tag> },
            { key: '2', label: '状态', children: (
              <Tag color={snapshot.status === 'success' ? 'green' : snapshot.status === 'failure' ? 'red' : 'orange'}>
                {snapshot.status}
              </Tag>
            )},
            { key: '3', label: '分支', children: snapshot.branch || '—' },
            { key: '4', label: 'Commit', children: <code style={{ fontSize: 12 }}>{(snapshot.commit_sha || '').slice(0, 8)}</code> },
            { key: '5', label: '时间', children: snapshot.created_at ? formatTimezone(snapshot.created_at) : '—' },
            { key: '6', label: '组件数', children: <Tag>{snapshot.total_components}</Tag> },
          ]}
        />
      )}

      {/* KPI 统计 */}
      {snapshot && diff && (
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
          <Col xs={12} md={6}>
            <Card size="small" style={{ background: '#e6f4ff', border: '1px solid #91caff' }}>
              <Statistic title="组件总数" value={snapshot.total_components} valueStyle={{ color: '#1890ff' }} />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small" style={{ background: '#fff2f0', border: '1px solid #ffccc7' }}>
              <Statistic
                title="与上次对比 — 变更数"
                value={diff.changed_count}
                valueStyle={{ color: '#ff4d4f' }}
                prefix={<SwapOutlined />}
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small" style={{ background: '#f6ffed', border: '1px solid #b7eb8f' }}>
              <Statistic
                title="基线合规"
                value={snapshot.components.filter(c => c.baseline_status === 'compliant').length}
                valueStyle={{ color: '#52c41a' }}
                prefix={<CheckCircleOutlined />}
              />
            </Card>
          </Col>
          <Col xs={12} md={6}>
            <Card size="small" style={{ background: '#fff2f0', border: '1px solid #ffccc7' }}>
              <Statistic
                title="基线违规"
                value={snapshot.components.filter(c => c.baseline_status === 'violation').length}
                valueStyle={{ color: '#ff4d4f' }}
                prefix={<ExclamationCircleOutlined />}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 版本快照 — 按分类分组 */}
      {snapshotLoading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      ) : snapshot ? (
        <Collapse
          defaultActiveKey={snapshot.category_order || []}
          style={{ marginBottom: 16 }}
          items={(snapshot.category_order || Object.keys(snapshot.components_by_category || {})).flatMap((cat) => {
            const items = snapshot.components_by_category?.[cat] || []
            if (items.length === 0) return []
            const label = snapshot.category_labels?.[cat] || cat
            const changedInCat = items.filter(c => {
              const d = diff?.diffs.find(dd => dd.component_name === c.component_name)
              return d && d.change_type !== 'unchanged'
            }).length
            return [{
              key: cat,
              label: (
                <Space>
                  <strong>{label}</strong>
                  <Tag>{items.length}</Tag>
                  {changedInCat > 0 && <Tag color="red">{changedInCat} 变更</Tag>}
                </Space>
              ),
              children: (
                <Table
                  dataSource={items}
                  rowKey={(r) => `${r.component_name}-${r.component_version}`}
                  size="small"
                  pagination={false}
                  scroll={{ x: 800 }}
                  columns={componentColumns}
                />
              ),
            }]
          })}
        />
      ) : (
        <Empty description={wfId ? '请选择一次构建执行' : '请先选择工作流'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}

      {/* 差异对比 */}
      {diff && diff.diffs.length > 0 && (
        <Card
          title={
            <Space>
              <SwapOutlined />
              版本差异对比
              {diff.previous_run ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  上次: #{diff.previous_run.github_run_id} ({diff.previous_run.branch || '-'}) {diff.previous_run.created_at ? formatTimezone(diff.previous_run.created_at) : ''}
                </Text>
              ) : (
                <Text type="secondary" style={{ fontSize: 12 }}>首次检测，无历史对比</Text>
              )}
            </Space>
          }
          size="small"
          style={{ marginTop: 16 }}
        >
          {diff.previous_run ? (
            <Table
              dataSource={diff.diffs}
              rowKey="component_name"
              size="small"
              pagination={{ pageSize: 15 }}
              columns={diffColumns}
              rowClassName={(r) => r.change_type !== 'unchanged' ? 'diff-row-changed' : ''}
            />
          ) : (
            <Alert message="这是该工作流首次检测到三方件数据的构建执行，无历史对比数据。" type="info" showIcon />
          )}
        </Card>
      )}
    </Card>
  )

  /* ─── Tab 2: 组件概览 ─── */
  const overviewTab = (
    <Card
      bordered={false}
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={loadOverview}>刷新</Button>
      }
    >
      {overviewComponents.length > 0 ? (
        <Table
          dataSource={overviewComponents}
          rowKey="component_name"
          size="small"
          scroll={{ x: 900 }}
          pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 个组件` }}
          columns={overviewColumns}
        />
      ) : (
        <Empty description="暂无三方件数据，请先选择工作流并同步数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </Card>
  )

  /* ─── Tab 3: 基线管理 ─── */
  const baselineTab = (
    <Card
      bordered={false}
      extra={
        <Space>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { setEditingBaseline(null); setBaselineModalOpen(true) }}>
            新增基线
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={loadBaselines}>刷新</Button>
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
  )

  const tabItems: TabsProps['items'] = [
    {
      key: 'snapshot',
      label: <span><SwapOutlined /> 版本快照</span>,
      children: snapshotTab,
    },
    {
      key: 'overview',
      label: <span><FolderOpenOutlined /> 组件概览 {overviewComponents.length > 0 && <Tag style={{ marginLeft: 8 }}>{overviewComponents.length}</Tag>}</span>,
      children: overviewTab,
    },
    {
      key: 'baselines',
      label: <span><CheckCircleOutlined /> 基线管理 {baselines.length > 0 && <Tag color="green" style={{ marginLeft: 8 }}>{baselines.length}</Tag>}</span>,
      children: baselineTab,
    },
  ]

  return (
    <div>
      <Tabs items={tabItems} size="large" />

      {/* 版本历史 Modal */}
      <VersionHistoryDrawer
        open={historyDrawerOpen}
        onClose={() => setHistoryDrawerOpen(false)}
        componentName={historyComponent}
      />

      {/* 基线 Modal */}
      <BaselineModal
        open={baselineModalOpen}
        onClose={() => { setBaselineModalOpen(false); setEditingBaseline(null) }}
        editingBaseline={editingBaseline}
        refreshBaselines={loadBaselines}
      />

      {/* 差异行高亮样式 */}
      <style>{`
        .diff-row-changed td { background-color: #fff2f0 !important; }
      `}</style>
    </div>
  )
}
