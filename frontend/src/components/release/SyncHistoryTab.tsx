import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Card, Row, Col, Statistic, Table, Tag, Button, Space, Select,
  message, Progress, Modal, Descriptions, Empty, Typography,
} from 'antd'
import {
  ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, EyeOutlined, StopOutlined, FlagOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import {
  getSyncTasks, getSyncTask, getSyncSummary, retrySync as retrySyncApi,
  fmtDuration, type SyncTask, type SyncSummary,
} from '../../services/releaseManagement'

dayjs.extend(relativeTime)

const { Text } = Typography

const { Option } = Select

export default function SyncHistoryTab() {
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [tasks, setTasks] = useState<SyncTask[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [filterStatus, setFilterStatus] = useState<string | null>(null)
  const [filterWorkflow, setFilterWorkflow] = useState<number | null>(null)
  const [workflowIds, setWorkflowIds] = useState<number[]>([])
  const [detailTask, setDetailTask] = useState<SyncTask | null>(null)
  const [summary, setSummary] = useState<SyncSummary | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [])

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const offset = (pagination.current - 1) * pagination.pageSize
      const result = await getSyncTasks(filterWorkflow, filterStatus, pagination.pageSize, offset)
      setTasks(result.tasks)
      setTotal(result.total)

      const allIds = result.tasks.map((t) => t.workflow_id).filter((id): id is number => id != null)
      if (allIds.length > 0) {
        setWorkflowIds((prev) => {
          const next = [...new Set([...prev, ...allIds])]
          return next
        })
      }
    } catch {
      message.error('加载同步任务失败')
    } finally {
      setLoading(false)
    }
  }, [pagination.current, pagination.pageSize, filterWorkflow, filterStatus])

  const loadSummary = useCallback(async () => {
    try {
      const data = await getSyncSummary()
      setSummary(data)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadTasks()
    loadSummary()
  }, [filterWorkflow, filterStatus, pagination.current, pagination.pageSize, loadTasks, loadSummary])

  // Auto-poll running tasks
  useEffect(() => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current)

    const running = tasks.find((t) => t.status === 'running' || t.status === 'pending')
    if (!running) return

    pollTimerRef.current = setInterval(async () => {
      try {
        const task = await getSyncTask(running.id)
        setTasks((prev) => prev.map((t) => t.id === running.id ? task : t))
        if (['completed', 'failed'].includes(task.status)) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
          loadTasks()
          loadSummary()
        }
      } catch {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }, 2000)

    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [tasks.map((t) => t.id).join(','), loadTasks, loadSummary])

  const handleRetry = async (task: SyncTask) => {
    if (retryingId) return
    setRetryingId(task.id)
    try {
      const result = await retrySyncApi(task.id)
      if (result.task_id) {
        message.success('重试已触发，后台执行中...')
        loadTasks()
        loadSummary()
      } else {
        message.warning('重试触发失败')
      }
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err ? (err as { detail: string }).detail : '重试失败'
      message.error(msg)
    } finally {
      setRetryingId(null)
    }
  }

  const statusConfig: Record<string, { color: string; label: string; icon: React.ReactNode }> = {
    pending: { color: 'default', label: '等待中', icon: <ClockCircleOutlined /> },
    running: { color: 'processing', label: '执行中', icon: <ClockCircleOutlined /> },
    completed: { color: 'success', label: '成功', icon: <CheckCircleOutlined /> },
    failed: { color: 'error', label: '失败', icon: <CloseCircleOutlined /> },
  }

  const triggerConfig: Record<string, { color: string; label: string }> = {
    manual: { color: 'blue', label: '手动' },
    retry: { color: 'orange', label: '重试' },
  }

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
      align: 'center' as const,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const cfg = statusConfig[status] || { color: 'default', label: status, icon: null }
        return <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
      },
    },
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 200,
      ellipsis: true,
      render: (v: string) => v || '-',
    },
    {
      title: '来源',
      dataIndex: 'trigger_source',
      key: 'trigger_source',
      width: 80,
      render: (v: string) => {
        const cfg = triggerConfig[v ?? ''] || { color: 'default', label: v ?? '其他' }
        return <Tag color={cfg.color} style={{ margin: 0 }}>{cfg.label}</Tag>
      },
    },
    {
      title: '天数',
      dataIndex: 'days_back',
      key: 'days_back',
      width: 60,
      align: 'right' as const,
      render: (v: number) => v || '-',
    },
    {
      title: '进度',
      key: 'progress',
      width: 160,
      render: (_: unknown, r: SyncTask) => (
        <div>
          <Progress
            percent={r.progress || 0}
            size="small"
            status={
              r.status === 'failed' ? 'exception'
                : r.status === 'completed' ? 'success'
                : r.status === 'pending' ? 'normal'
                : 'active'
            }
          />
          {r.progress_message && (
            <div style={{ fontSize: 11, color: '#999', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {r.progress_message}
            </div>
          )}
        </div>
      ),
    },
    {
      title: '采集数',
      dataIndex: 'collected_items',
      key: 'collected_items',
      width: 80,
      align: 'right' as const,
      render: (v: number) => v || '-',
    },
    {
      title: '耗时',
      key: 'duration',
      width: 80,
      align: 'right' as const,
      render: (_: unknown, r: SyncTask) =>
        r.duration_seconds > 0 ? fmtDuration(r.duration_seconds) : '-',
    },
    {
      title: '时间',
      key: 'created_at',
      width: 150,
      render: (_: unknown, r: SyncTask) =>
        r.created_at ? dayjs(r.created_at).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      fixed: 'right' as const,
      render: (_: unknown, r: SyncTask) => {
        const errors = r.errors_json ? (Array.isArray(r.errors_json) ? r.errors_json : [r.errors_json]) : []
        const hasErrors = r.error_message || errors.length > 0
        return (
          <Space size="small">
            {hasErrors && (
              <Tag color="blue" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }} onClick={() => setDetailTask(r)}>
                <EyeOutlined /> 详情
              </Tag>
            )}
            {(r.status === 'running' || r.status === 'pending') && (
              <Tag color="red" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}>
                <StopOutlined /> 终止
              </Tag>
            )}
            {r.status === 'failed' && (
              <Tag color="orange" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }} onClick={() => handleRetry(r)}>
                <ReloadOutlined /> 重试
              </Tag>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      {/* 统计卡片 */}
      {summary && (
        <Row gutter={16} style={{ marginBottom: 20 }}>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="总同步次数" value={summary.total_syncs} suffix="次" valueStyle={{ color: '#1890ff' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="成功次数" value={summary.completed_count} suffix="次" valueStyle={{ color: '#52c41a' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="失败次数" value={summary.failed_count} suffix="次" valueStyle={{ color: summary.failed_count > 0 ? '#ff4d4f' : '#999' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="正在运行" value={summary.running_count} suffix="个" valueStyle={{ color: summary.running_count > 0 ? '#faad14' : '#999' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic title="采集记录总计" value={summary.total_collected} precision={0} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              <Statistic
                title="上次同步"
                value={summary.last_sync_at ? dayjs(summary.last_sync_at).fromNow() : '无记录'}
                valueStyle={{ fontSize: 13, color: summary.last_sync_status === 'completed' ? '#52c41a' : '#999' }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 筛选栏 */}
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <Button onClick={() => { loadTasks(); loadSummary() }} icon={<ReloadOutlined />} loading={loading}>
          刷新
        </Button>
        <Select
          placeholder="选择 Workflow"
          allowClear
          showSearch
          style={{ width: 220 }}
          value={filterWorkflow}
          onChange={(v) => { setFilterWorkflow(v); setPagination(p => ({ ...p, current: 1 })) }}
          options={workflowIds.map(id => ({ label: `#${id}`, value: id }))}
          filterOption={(input, option) => String(option?.label ?? '').includes(input)}
        />
        <Select
          allowClear
          placeholder="状态过滤"
          style={{ width: 140 }}
          value={filterStatus}
          onChange={(v) => { setFilterStatus(v); setPagination(p => ({ ...p, current: 1 })) }}
        >
          <Option value="pending">等待中</Option>
          <Option value="running">执行中</Option>
          <Option value="completed">成功</Option>
          <Option value="failed">失败</Option>
        </Select>
        <Text type="secondary">共 {total} 条记录</Text>
      </div>

      {total === 0 && !loading && (
        <Empty
          description="暂无同步记录，请先执行数据同步以采集构建数据"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: '20px 0' }}
        />
      )}

      {total > 0 && (
        <Table
          columns={columns}
          dataSource={tasks}
          loading={loading}
          rowKey="id"
          pagination={{
            ...pagination,
            total,
            showSizeChanger: true,
            showTotal: () => `共 ${total} 条`,
            onChange: (page, size) => setPagination({ current: page, pageSize: size }),
          }}
          size="small"
          scroll={{ x: 1200 }}
        />
      )}

      {/* 详情弹窗 */}
      <Modal
        title="同步记录详情"
        open={!!detailTask}
        onCancel={() => setDetailTask(null)}
        footer={[<Button key="close" onClick={() => setDetailTask(null)}>关闭</Button>]}
        width={700}
      >
        {detailTask && (
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="状态">
              {(() => {
                const cfg = statusConfig[detailTask.status] || { color: 'default', label: detailTask.status, icon: null }
                return <Tag color={cfg.color}>{cfg.icon} {cfg.label}</Tag>
              })()}
            </Descriptions.Item>
            <Descriptions.Item label="来源">
              {(() => {
                const cfg = triggerConfig[detailTask.trigger_source ?? ''] || { color: 'default', label: detailTask.trigger_source ?? '其他' }
                return <Tag color={cfg.color}>{cfg.label}</Tag>
              })()}
            </Descriptions.Item>
            <Descriptions.Item label="Workflow">
              {detailTask.workflow_name || `#${detailTask.workflow_id}`}
            </Descriptions.Item>
            <Descriptions.Item label="天数">
              {detailTask.days_back}
            </Descriptions.Item>
            <Descriptions.Item label="采集数">
              {detailTask.collected_items || 0}
            </Descriptions.Item>
            <Descriptions.Item label="耗时">
              {detailTask.duration_seconds > 0 ? fmtDuration(detailTask.duration_seconds) : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间" span={2}>
              {detailTask.created_at ? dayjs(detailTask.created_at).format('YYYY-MM-DD HH:mm:ss') : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="开始时间" span={2}>
              {detailTask.started_at ? dayjs(detailTask.started_at).format('YYYY-MM-DD HH:mm:ss') : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间" span={2}>
              {detailTask.completed_at ? dayjs(detailTask.completed_at).format('YYYY-MM-DD HH:mm:ss') : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="进度消息" span={2}>
              {detailTask.progress_message || '—'}
            </Descriptions.Item>
            {detailTask.error_message && (
              <Descriptions.Item label="错误信息" span={2}>
                <Text type="danger" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {detailTask.error_message}
                </Text>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </div>
  )
}
