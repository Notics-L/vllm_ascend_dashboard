import { useState } from 'react'
import { Card, Table, Space, Select, Tag, Tooltip, Button, Alert, message, Typography } from 'antd'
import { SyncOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import { getSupportMatrix, getSyncStatus, triggerUpstreamSync } from '../services/models'
import type { SupportMatrixModel, FeatureColumn } from '../types/models'
import dayjs from 'dayjs'

const { Text } = Typography

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  supported: { color: '#52c41a', label: '✅' },
  experimental: { color: '#1890ff', label: '🔵' },
  not_supported: { color: '#ff4d4f', label: '❌' },
  untested: { color: '#faad14', label: '🟡' },
  unmarked: { color: '#d9d9d9', label: '-' },
}

const MODEL_TYPE_LABELS: Record<string, string> = {
  text_generative: '文本生成',
  pooling: '池化',
  multimodal_generative: '多模态生成',
}

const TIER_LABELS: Record<string, string> = {
  core: 'Core',
  extended: 'Extended',
}

function renderFeatureCell(status: string | undefined, verified: boolean | undefined) {
  if (!status || status === 'unmarked') {
    return <span style={{ color: '#d9d9d9' }}>-</span>
  }
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.unmarked
  return (
    <Tooltip title={`${config.label} ${status}${verified ? ' · CI 验证 ✓' : ''}`}>
      <span style={{ fontSize: '14px' }}>
        {config.label}
        {verified && (
          <CheckCircleOutlined style={{ color: '#52c41a', fontSize: '10px', marginLeft: '2px' }} />
        )}
      </span>
    </Tooltip>
  )
}

export default function SupportMatrixTab() {
  const queryClient = useQueryClient()
  const [modelTypeFilter, setModelTypeFilter] = useState<string[]>([])
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  const [tierFilter, setTierFilter] = useState<string[]>([])
  const [syncing, setSyncing] = useState(false)

  const { data: matrixData, isLoading } = useQuery({
    queryKey: ['support-matrix', { modelTypeFilter, statusFilter, tierFilter }],
    queryFn: () => getSupportMatrix({
      model_type: modelTypeFilter.length > 0 ? modelTypeFilter[0] : undefined,
      support_status: statusFilter.length > 0 ? statusFilter[0] : undefined,
      tier: tierFilter.length > 0 ? tierFilter[0] : undefined,
    }),
  })

  const { data: syncStatus } = useQuery({
    queryKey: ['sync-status'],
    queryFn: getSyncStatus,
    refetchInterval: 60000,
  })

  const syncMutation = useMutation({
    mutationFn: (dryRun: boolean) => triggerUpstreamSync(dryRun),
    onSuccess: (result) => {
      if (result.success) {
        message.success(
          `同步完成: ${result.models_synced || 0} 模型, ${result.new_models?.length || 0} 新增, ${result.updated_models?.length || 0} 更新`
        )
        queryClient.invalidateQueries({ queryKey: ['support-matrix'] })
        queryClient.invalidateQueries({ queryKey: ['sync-status'] })
      } else {
        message.error(`同步失败: ${result.error}`)
      }
      setSyncing(false)
    },
    onError: (err: any) => {
      message.error(`同步出错: ${err?.message || '未知错误'}`)
      setSyncing(false)
    },
  })

  const handleSync = (dryRun: boolean) => {
    setSyncing(true)
    syncMutation.mutate(dryRun)
  }

  const models = matrixData?.models || []
  const featureColumns = matrixData?.feature_columns || []

  const columns: ColumnsType<SupportMatrixModel> = [
    {
      title: '模型',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 180,
      fixed: 'left',
      sorter: (a, b) => a.model_name.localeCompare(b.model_name),
      render: (text: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong style={{ fontSize: '13px' }}>{record.display_name || text}</Text>
          {record.series && <Tag color="#533afd" style={{ fontSize: '11px' }}>{record.series}</Tag>}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'model_type',
      key: 'model_type',
      width: 90,
      filters: [
        { text: '文本生成', value: 'text_generative' },
        { text: '池化', value: 'pooling' },
        { text: '多模态', value: 'multimodal_generative' },
      ],
      onFilter: (value, record) => record.model_type === value,
      render: (type: string) => MODEL_TYPE_LABELS[type] || type,
    },
    {
      title: 'Tier',
      dataIndex: 'tier',
      key: 'tier',
      width: 80,
      render: (tier: string) => tier ? <Tag>{TIER_LABELS[tier] || tier}</Tag> : '-',
    },
    {
      title: '状态',
      dataIndex: 'support_status',
      key: 'support_status',
      width: 70,
      render: (status: string) => {
        const config = STATUS_CONFIG[status] || STATUS_CONFIG.unmarked
        return <span style={{ fontSize: '16px' }}>{config.label}</span>
      },
    },
    {
      title: '硬件',
      dataIndex: 'supported_hardware',
      key: 'supported_hardware',
      width: 100,
      render: (hw: string[] | null) => hw ? hw.join('/') : '-',
    },
    {
      title: '权重',
      dataIndex: 'weight_formats',
      key: 'weight_formats',
      width: 90,
      render: (wf: string[] | null) => wf ? wf.join('/') : '-',
    },
    ...featureColumns.map((col: FeatureColumn) => ({
      title: col.title,
      key: col.key,
      width: 70,
      align: 'center' as const,
      render: (_: any, record: SupportMatrixModel) =>
        renderFeatureCell(record.features[col.key], record.verified_features[col.key]),
    })),
  ]

  const stats = matrixData?.statistics
  const syncSuccess = syncStatus?.success
  const syncTime = syncStatus?.last_sync_at

  return (
    <div>
      {syncTime && (
        <Alert
          type={syncSuccess === false ? 'error' : 'success'}
          showIcon
          style={{ marginBottom: 12 }}
          message={
            syncSuccess === false
              ? `⚠ 同步失败 ${dayjs(syncTime).add(8, 'hour').format('YYYY-MM-DD HH:mm')} · ${syncStatus?.error || ''}`
              : `最近同步成功 ${dayjs(syncTime).add(8, 'hour').format('YYYY-MM-DD HH:mm')} · ${syncStatus?.models_synced || 0} 模型 · 新增 ${syncStatus?.new_models?.length || 0} · 更新 ${syncStatus?.updated_models?.length || 0}`
          }
          closable={syncSuccess !== false}
        />
      )}

      <Card style={{ marginBottom: 12 }}>
        <Space size="middle" wrap>
          <Select
            mode="multiple"
            placeholder="模型类型"
            style={{ minWidth: 150 }}
            value={modelTypeFilter}
            onChange={setModelTypeFilter}
            options={[
              { label: '文本生成', value: 'text_generative' },
              { label: '池化', value: 'pooling' },
              { label: '多模态生成', value: 'multimodal_generative' },
            ]}
            allowClear
          />
          <Select
            mode="multiple"
            placeholder="支持状态"
            style={{ minWidth: 130 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { label: '✅ 支持', value: 'supported' },
              { label: '🔵 实验', value: 'experimental' },
              { label: '🟡 待测', value: 'untested' },
              { label: '❌ 不支持', value: 'not_supported' },
            ]}
            allowClear
          />
          <Select
            mode="multiple"
            placeholder="Tier"
            style={{ minWidth: 120 }}
            value={tierFilter}
            onChange={setTierFilter}
            options={[
              { label: 'Core', value: 'core' },
              { label: 'Extended', value: 'extended' },
            ]}
            allowClear
          />
          <Button
            icon={<SyncOutlined spin={syncing} />}
            loading={syncing}
            onClick={() => handleSync(false)}
          >
            同步上游
          </Button>
          <Button onClick={() => handleSync(true)} disabled={syncing}>
            Dry-run 预览
          </Button>
        </Space>
      </Card>

      {stats && (
        <Card style={{ marginBottom: 12 }}>
          <Space size="large">
            <Text>总模型: <strong>{stats.total_models}</strong></Text>
            {Object.entries(stats.by_type).map(([k, v]) => (
              <Text key={k}>{MODEL_TYPE_LABELS[k] || k}: <strong>{v}</strong></Text>
            ))}
            {Object.entries(stats.by_status).map(([k, v]) => {
              const config = STATUS_CONFIG[k] || {}
              return <Text key={k}>{config.label || k} <strong>{v}</strong></Text>
            })}
          </Space>
        </Card>
      )}

      <Card>
        <Table
          columns={columns}
          dataSource={models}
          loading={isLoading}
          rowKey="id"
          pagination={{ pageSize: 50, showSizeChanger: false }}
          scroll={{ x: 1200 }}
          size="small"
        />
      </Card>
    </div>
  )
}
