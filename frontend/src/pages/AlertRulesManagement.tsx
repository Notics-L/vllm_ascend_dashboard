import { useState } from 'react'
import {
  Button, Card, Empty, Form, Input, InputNumber, Modal, Popconfirm, Select, Space,
  Switch, Table, Tag, Typography, message, Divider, Radio,
} from 'antd'
import { DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, MinusCircleOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  AlertRule, AlertRuleCreate, AlertRuleUpdate, AlertCondition, AlertHistory,
  METRIC_FIELD_OPTIONS, OPERATOR_OPTIONS,
  getAlertRules, getAlertRuleHistory, createAlertRule, updateAlertRule, deleteAlertRule,
} from '../services/alertRules'
import { getEnabledResourceClusters, getClusterSummary, ResourceNodeInfo } from '../services/resourceDashboard'

const { Text, Title } = Typography

const newCondition = (): AlertCondition => ({ metric_field: 'npu_utilization', operator: '>', threshold: 0, is_exclude: false })

function AlertRulesManagement() {
  const [form] = Form.useForm()
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null)
  const [historyModalOpen, setHistoryModalOpen] = useState(false)
  const [historyRuleId, setHistoryRuleId] = useState<number | null>(null)
  const [clusterNodes, setClusterNodes] = useState<ResourceNodeInfo[]>([])
  const [nodesLoading, setNodesLoading] = useState(false)

  // Condition groups local state
  const [groups, setGroups] = useState<{ logic: string; conditions: AlertCondition[] }[]>([
    { logic: 'AND', conditions: [newCondition()] },
  ])

  const { data: rules = [], isLoading: rulesLoading } = useQuery({ queryKey: ['alert-rules'], queryFn: getAlertRules })
  const { data: clusters = [] } = useQuery({ queryKey: ['resource-clusters-enabled'], queryFn: getEnabledResourceClusters })
  const { data: ruleHistories = [] } = useQuery({
    queryKey: ['alert-rule-history', historyRuleId],
    queryFn: () => (historyRuleId ? getAlertRuleHistory(historyRuleId) : Promise.resolve([])),
    enabled: !!historyRuleId,
  })

  const createMutation = useMutation({
    mutationFn: createAlertRule,
    onSuccess: () => { message.success('已创建'); setModalOpen(false); form.resetFields(); setGroups([{ logic: 'AND', conditions: [newCondition()] }]) },
    onError: (e: any) => message.error(e?.response?.data?.detail || '创建失败'),
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: AlertRuleUpdate }) => updateAlertRule(id, data),
    onSuccess: () => { message.success('已更新'); setModalOpen(false); setEditingRule(null); form.resetFields() },
    onError: (e: any) => message.error(e?.response?.data?.detail || '更新失败'),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteAlertRule,
    onSuccess: () => message.success('已删除'),
    onError: () => message.error('删除失败'),
  })

  const loadClusterNodes = async (clusterId: number | null) => {
    if (!clusterId) { setClusterNodes([]); return }
    setNodesLoading(true)
    try { const s = await getClusterSummary(clusterId); setClusterNodes(s.node_resources || []) }
    catch { setClusterNodes([]) }
    finally { setNodesLoading(false) }
  }

  const openCreateModal = () => {
    setEditingRule(null)
    form.resetFields()
    form.setFieldsValue({ enabled: true, notify_email: true })
    setGroups([{ logic: 'AND', conditions: [newCondition()] }])
    setModalOpen(true)
  }

  const openEditModal = (rule: AlertRule) => {
    setEditingRule(rule)
    form.setFieldsValue({
      name: rule.name, cluster_id: rule.cluster_id, node_name: rule.node_name,
      enabled: rule.enabled, notify_email: rule.notify_email, notification_email: rule.notification_email,
    })
    setGroups(rule.groups.map(g => ({ logic: g.logic, conditions: g.conditions.map(c => ({ ...c })) })))
    loadClusterNodes(rule.cluster_id ?? null)
    setModalOpen(true)
  }

  const openHistoryModal = (ruleId: number) => { setHistoryRuleId(ruleId); setHistoryModalOpen(true) }

  const submitForm = async () => {
    const values = await form.validateFields()
    // Clean empty conditions
    const cleaned = groups.map(g => ({ ...g, conditions: g.conditions.filter(c => c.metric_field && c.threshold !== undefined) })).filter(g => g.conditions.length > 0)
    if (cleaned.length === 0) { message.error('至少需要一个条件'); return }
    if (editingRule) {
      updateMutation.mutate({ id: editingRule.id, data: { ...values, groups: cleaned } })
    } else {
      createMutation.mutate({ ...values, groups: cleaned } as AlertRuleCreate)
    }
  }

  const fm = (field: string) => METRIC_FIELD_OPTIONS.find(o => o.value === field)?.label || field

  const rulesColumns: ColumnsType<AlertRule> = [
    { title: '名称', dataIndex: 'name', width: 160 },
    { title: '条件', width: 280, render: (_, r) => (
      <Space size={[2, 2]} wrap>
        {(r.groups || []).map((g, gi) => (
          <Tag key={gi} color="blue" style={{ marginBottom: 2 }}>
            {gi > 0 && <Text type="secondary" style={{ fontSize: 10 }}>AND </Text>}
            {g.conditions.map((c, ci) => (
              <span key={ci}>
                {ci > 0 && <Text style={{ fontSize: 10 }}>{g.logic === 'OR' ? ' OR ' : ' AND '}</Text>}
                {c.is_exclude ? <Text type="danger">NOT </Text> : null}
                {fm(c.metric_field)}{c.operator}{c.threshold}
              </span>
            ))}
          </Tag>
        ))}
      </Space>
    )},
    { title: '集群', dataIndex: 'cluster_id', width: 110, render: v => v ? <Tag color="green">{clusters.find(c => c.id === v)?.name || v}</Tag> : <Tag color="blue">全部</Tag> },
    { title: '节点', dataIndex: 'node_name', width: 150, render: v => v ? <Tag color="purple">{v}</Tag> : <Text type="secondary">集群级</Text> },
    { title: '启用', dataIndex: 'enabled', width: 60, render: v => <Tag color={v ? 'green' : 'default'}>{v ? '是' : '否'}</Tag> },
    { title: '上次触发', dataIndex: 'last_triggered_at', width: 130, render: v => v ? dayjs(v).format('MM-DD HH:mm') : <Text type="secondary">-</Text> },
    {
      title: '操作', width: 200, render: (_, r) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEditModal(r)}>编辑</Button>
          <Button size="small" icon={<HistoryOutlined />} onClick={() => openHistoryModal(r.id)}>历史</Button>
          <Popconfirm title="确认删除？" onConfirm={() => deleteMutation.mutate(r.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const historyColumns: ColumnsType<AlertHistory> = [
    { title: '规则', dataIndex: 'rule_name', width: 120 },
    { title: '实际值', dataIndex: 'actual_value', width: 100, render: v => <Text style={{ color: '#ff4d4f', fontWeight: 'bold' }}>{v}</Text> },
    { title: '集群', dataIndex: 'cluster_name', width: 120, render: v => v || '-' },
    { title: '节点', dataIndex: 'node_name', width: 140, render: v => v || '-' },
    { title: '触发时间', dataIndex: 'triggered_at', width: 160, render: v => dayjs(v).format('YYYY-MM-DD HH:mm:ss') },
    { title: '通知', dataIndex: 'notification_sent', width: 70, render: (v, r) => v ? <Tag color="green">已发</Tag> : <Tag color="red" title={r.notification_error || ''}>失败</Tag> },
  ]

  return (
    <div>
      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div><Title level={4}>告警规则</Title><Text type="secondary">条件组: 组间 AND，组内 AND/OR，支持 NOT 排除</Text></div>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>新增规则</Button>
        </div>
        <Table<AlertRule> rowKey="id" loading={rulesLoading} dataSource={rules} columns={rulesColumns} scroll={{ x: 1200 }} locale={{ emptyText: <Empty description="暂无告警规则" /> }} />
      </Card>

      {/* Create/Edit Modal */}
      <Modal title={editingRule ? '编辑告警规则' : '新增告警规则'} open={modalOpen}
        onCancel={() => { setModalOpen(false); setEditingRule(null); form.resetFields() }}
        onOk={submitForm} confirmLoading={createMutation.isPending || updateMutation.isPending}
        width={800} destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}><Input placeholder="例如：节点高负载告警" /></Form.Item>
          <Space style={{ width: '100%' }} size="middle">
            <Form.Item name="cluster_id" label="集群" style={{ width: 220 }}>
              <Select allowClear placeholder="全部集群" options={clusters.map(c => ({ label: c.name, value: c.id }))} onChange={loadClusterNodes} />
            </Form.Item>
            <Form.Item name="node_name" label="节点" style={{ width: 200 }}>
              <Select allowClear placeholder="全部节点" loading={nodesLoading}
                options={clusterNodes.filter(n => n.total.npu > 0).map(n => ({ label: n.node_name, value: n.node_name }))} />
            </Form.Item>
          </Space>
          <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="notify_email" label="邮件通知" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="notification_email" label="通知邮箱" extra="留空使用账号邮箱"><Input placeholder="user@example.com" /></Form.Item>

          <Divider orientation="left" plain>条件组（组间 AND）</Divider>
          {groups.map((group, gi) => (
            <Card key={gi} size="small" style={{ marginBottom: 8, background: '#fafafa' }}
              title={<Space>
                <Text strong>组 {gi + 1}</Text>
                <Radio.Group size="small" value={group.logic}
                  onChange={e => { const gs = [...groups]; gs[gi].logic = e.target.value; setGroups(gs) }}>
                  <Radio.Button value="AND">AND（全部满足）</Radio.Button>
                  <Radio.Button value="OR">OR（任一满足）</Radio.Button>
                </Radio.Group>
              </Space>}
              extra={groups.length > 1 && <Button size="small" danger icon={<MinusCircleOutlined />}
                onClick={() => setGroups(groups.filter((_, i) => i !== gi))}>删除组</Button>}
            >
              {group.conditions.map((cond, ci) => (
                <Space key={ci} style={{ width: '100%', marginBottom: 4 }} align="start" wrap>
                  {cond.is_exclude && <Tag color="red" style={{ marginTop: 4 }}>NOT</Tag>}
                  <Select style={{ width: 170 }} value={cond.metric_field}
                    onChange={v => { const gs = [...groups]; gs[gi].conditions[ci].metric_field = v; setGroups(gs) }}
                    options={METRIC_FIELD_OPTIONS} />
                  <Select style={{ width: 120 }} value={cond.operator}
                    onChange={v => { const gs = [...groups]; gs[gi].conditions[ci].operator = v; setGroups(gs) }}
                    options={OPERATOR_OPTIONS} />
                  <InputNumber style={{ width: 100 }} value={cond.threshold}
                    onChange={v => { const gs = [...groups]; gs[gi].conditions[ci].threshold = v ?? 0; setGroups(gs) }} />
                  <Button size="small" type={cond.is_exclude ? 'primary' : 'default'} danger={cond.is_exclude}
                    onClick={() => { const gs = [...groups]; gs[gi].conditions[ci].is_exclude = !cond.is_exclude; setGroups(gs) }}>
                    {cond.is_exclude ? '排除中' : '排除'}
                  </Button>
                  {group.conditions.length > 1 && <Button size="small" danger icon={<MinusCircleOutlined />}
                    onClick={() => {
                      const gs = [...groups];
                      gs[gi].conditions = gs[gi].conditions.filter((_, i) => i !== ci);
                      setGroups(gs);
                    }} />}
                </Space>
              ))}
              <Button type="dashed" size="small" icon={<PlusOutlined />}
                onClick={() => { const gs = [...groups]; gs[gi].conditions.push(newCondition()); setGroups(gs) }}>
                添加条件
              </Button>
            </Card>
          ))}
          <Button type="dashed" icon={<PlusOutlined />}
            onClick={() => setGroups([...groups, { logic: 'AND', conditions: [newCondition()] }])}>
            添加条件组
          </Button>
        </Form>
      </Modal>

      {/* History Modal */}
      <Modal title="告警触发历史" open={historyModalOpen} onCancel={() => { setHistoryModalOpen(false); setHistoryRuleId(null) }} footer={null} width={960}>
        <Table<AlertHistory> rowKey="id" dataSource={ruleHistories} columns={historyColumns} pagination={{ pageSize: 10 }} scroll={{ x: 700 }} locale={{ emptyText: <Empty description="暂无触发记录" /> }} />
      </Modal>
    </div>
  )
}

export default AlertRulesManagement
