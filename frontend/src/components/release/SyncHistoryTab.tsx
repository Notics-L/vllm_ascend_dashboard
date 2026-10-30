import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Card, Row, Col, Table, Tag, Button, Space, Select,
  message, Progress, Modal, Descriptions, Empty, Typography, Alert, Tooltip,
} from 'antd'
import {
  ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined, EyeOutlined, StopOutlined, FlagOutlined,
  FolderOpenOutlined, FileExcelOutlined, FilePdfOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { formatTimezone, fromTimezoneNow } from '@/utils/timezone'
import { useNavigate } from 'react-router-dom'
import {
  getSyncTasks, getSyncTask, getSyncSummary, retrySync as retrySyncApi,
  cancelSync, exportSyncExcel, exportSyncPdf, archiveSyncTask,
  fmtDuration, type SyncTask, type SyncSummary,
} from '../../services/releaseManagement'

dayjs.extend(relativeTime)

const { Text } = Typography
const { Option } = Select

/* ── Minimal stat-card helper ── */
function StatBlock({ label, value, color, icon }: { label: string; value: string | number; color: string; icon: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontSize: 18, opacity: 0.45 }}>{icon}</span>
      <div>
        <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
        <div style={{ fontSize: 11, color: '#999' }}>{label}</div>
      </div>
    </div>
  )
}

export default function SyncHistoryTab() {
  const navigate = useNavigate()
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [tasks, setTasks] = useState<SyncTask[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 })
  const [filterStatus, setFilterStatus] = useState<string | null>(null)
  const [filterWorkflow, setFilterWorkflow] = useState<number | null>(null)
  const [workflowOptions, setWorkflowOptions] = useState<{id: number, name: string}[]>([])
  const [detailTask, setDetailTask] = useState<SyncTask | null>(null)
  const [summary, setSummary] = useState<SyncSummary | null>(null)
  const [archivingTask, setArchivingTask] = useState<number | null>(null)
  const [errorDetailOpen, setErrorDetailOpen] = useState(false)
  const [errorTask, setErrorTask] = useState<SyncTask | null>(null)
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

      const allWfs = result.tasks
        .filter(t => t.workflow_id != null && t.workflow_name)
        .map(t => ({ id: t.workflow_id!, name: t.workflow_name! }))
      if (allWfs.length > 0) {
        setWorkflowOptions((prev) => {
          const map = new Map(prev.map(o => [o.id, o]))
          for (const w of allWfs) {
            if (!map.has(w.id)) map.set(w.id, w)
          }
          return Array.from(map.values())
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

        // Update active sync task in parent via event
        const evt = new CustomEvent('release-sync-task-update', { detail: task })
        window.dispatchEvent(evt)

        if (['completed', 'failed', 'cancelled'].includes(task.status)) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current)
          pollTimerRef.current = null
          loadTasks()
          loadSummary()
        }
      } catch {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }, 3000)

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

  const handleCancel = async (task: SyncTask) => {
    try {
      await cancelSync(task.id)
      message.success('任务已终止')
      loadTasks()
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err ? (err as { detail: string }).detail : '终止失败'
      message.error(msg)
    }
  }

  const handleViewBuildAnalysis = async (task: SyncTask) => {
    if (task.status !== 'completed') {
      message.warning('只有已完成的同步记录才可查看构建分析')
      return
    }
    try {
      const archive = await archiveSyncTask(task.id)
      if (archive.total_runs === 0) {
        message.warning('该工作流暂无构建数据')
        return
      }
      const evt = new CustomEvent('release-view-archive', {
        detail: {
          workflow_id: archive.workflow_id,
          workflow_name: archive.workflow_name,
          overview: archive.overview,
          runs: archive.runs,
          total_runs: archive.total_runs,
        },
      })
      window.dispatchEvent(evt)
    } catch (err: unknown) {
      const msg = typeof err === 'object' && err && 'detail' in err
        ? (err as { detail: string }).detail
        : '加载构建分析失败'
      if (typeof msg === 'string' && msg.includes('暂无构建数据')) {
        message.warning(msg)
        return
      }
      message.error(msg as string)
    }
  }

  const handleExportExcel = async (task: SyncTask) => {
    try {
      setArchivingTask(task.id)
      const blob = await exportSyncExcel(task.id)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `release_sync_${task.id}_${dayjs().format('YYYYMMDDHHmmss')}.xlsx`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    } finally {
      setArchivingTask(null)
    }
  }

  const handleExportPdf = async (task: SyncTask) => {
    try {
      setArchivingTask(task.id)
      const blob = await exportSyncPdf(task.id)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `release_sync_${task.id}_${dayjs().format('YYYYMMDDHHmmss')}.pdf`
      a.click()
      window.URL.revokeObjectURL(url)
      message.success('PDF 导出成功')
    } catch {
      message.error('PDF 导出失败')
    } finally {
      setArchivingTask(null)
    }
  }

  const statusConfig: Record<string, { color: string; label: string; icon: React.ReactNode; bg: string }> = {
    pending: { color: '#faad14', label: '等待中', icon: <ClockCircleOutlined />, bg: '#fffbe6' },
    running: { color: '#1677ff', label: '执行中', icon: <ClockCircleOutlined />, bg: '#e6f4ff' },
    completed: { color: '#52c41a', label: '成功', icon: <CheckCircleOutlined />, bg: '#f6ffed' },
    failed: { color: '#ff4d4f', label: '失败', icon: <CloseCircleOutlined />, bg: '#fff2f0' },
    cancelled: { color: '#999', label: '已终止', icon: <StopOutlined />, bg: '#fafafa' },
  }

  const triggerConfig: Record<string, { color: string; label: string }> = {
    manual: { color: 'blue', label: '手动' },
    retry: { color: 'orange', label: '重试' },
    scheduled: { color: 'cyan', label: '定时' },
  }

  // Row-level style based on status
   const getRowStyle = (_record: SyncTask) => ({})

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
        const cfg = statusConfig[status] || { color: '#999', label: status, icon: null, bg: '' }
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
      title: '范围(天)',
      dataIndex: 'days_back',
      key: 'days_back',
      width: 80,
      align: 'right' as const,
      render: (v: number) => v || '-',
    },
    {
      title: '进度',
      key: 'progress',
      width: 180,
      render: (_: unknown, r: SyncTask) => (
        <div>
          <Progress
            percent={r.progress || 0}
            size="small"
            strokeColor={
              r.status === 'completed' ? '#52c41a'
                : r.status === 'failed' ? '#ff4d4f'
                : r.status === 'cancelled' ? '#d9d9d9'
                : '#1677ff'
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
      render: (v: number) => v != null ? v : '-',
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
        formatTimezone(r.created_at, 'YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right' as const,
      render: (_: unknown, r: SyncTask) => {
        const isActive = r.status === 'running' || r.status === 'pending'
        const isCompleted = r.status === 'completed'
        const isFailed = r.status === 'failed'

        return (
          <Space size="small">
            {isActive && (
              <Tooltip title="终止任务">
                <Tag color="red" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => handleCancel(r)}>
                  <StopOutlined /> 终止
                </Tag>
              </Tooltip>
            )}
            {isFailed && (
              <Tooltip title="查看失败详情">
                <Tag color="blue" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => { setErrorTask(r); setErrorDetailOpen(true); }}>
                  <EyeOutlined /> 错误
                </Tag>
              </Tooltip>
            )}
            {isFailed && (
              <Tooltip title="重新同步">
                <Tag color="orange" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => handleRetry(r)}>
                  <ReloadOutlined /> 重试
                </Tag>
              </Tooltip>
            )}
            {isCompleted && (
              <Tooltip title="查看构建分析">
                <Tag color="cyan" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => handleViewBuildAnalysis(r)}>
                  <FolderOpenOutlined /> 分析
                </Tag>
              </Tooltip>
            )}
            {isCompleted && (
              <Tooltip title="导出 Excel">
                <Tag color="green" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => handleExportExcel(r)}>
                  <FileExcelOutlined />
                </Tag>
              </Tooltip>
            )}
            {isCompleted && (
              <Tooltip title="导出 PDF">
                <Tag color="red" style={{ cursor: 'pointer', margin: 0, lineHeight: '22px' }}
                  onClick={() => handleExportPdf(r)}>
                  <FilePdfOutlined />
                </Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <div>
      {/* 精致聚合统计卡片 — 单行显示 */}
      {summary && (
        <Card size="small" bordered={false}
          style={{ marginBottom: 16, borderRadius: 8, background: '#fafafa' }}
          styles={{ body: { padding: '10px 20px' } }}
        >
          <Row gutter={[24, 8]} justify="space-between">
            <Col>
              <StatBlock label="总同步" value={summary.total_syncs} color="#1677ff" icon={<FlagOutlined />} />
            </Col>
            <Col>
              <StatBlock label="成功" value={summary.completed_count} color="#52c41a" icon={<CheckCircleOutlined />} />
            </Col>
            <Col>
              <StatBlock label="失败" value={summary.failed_count} color={summary.failed_count > 0 ? '#ff4d4f' : '#bbb'} icon={<CloseCircleOutlined />} />
            </Col>
            <Col>
              <StatBlock label="运行中" value={summary.running_count} color={summary.running_count > 0 ? '#fa8c16' : '#bbb'} icon={<ClockCircleOutlined />} />
            </Col>
            <Col>
              <StatBlock label="采集记录" value={summary.total_collected} color="#722ed1" icon={<CheckCircleOutlined />} />
            </Col>
            <Col>
              <StatBlock
                label="上次同步"
                value={summary.last_sync_at ? fromTimezoneNow(summary.last_sync_at) : '-'}
                color={summary.last_sync_status === 'completed' ? '#52c41a' : '#bbb'}
                icon={<ClockCircleOutlined />}
              />
            </Col>
          </Row>
        </Card>
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
          options={workflowOptions.map(w => ({ label: w.name, value: w.id }))}
          filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
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
          <Option value="cancelled">已终止</Option>
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
          onRow={(record) => ({ style: getRowStyle(record) })}
          pagination={{
            ...pagination,
            total,
            showSizeChanger: true,
            showTotal: () => `共 ${total} 条`,
            onChange: (page, size) => setPagination({ current: page, pageSize: size }),
          }}
          size="small"
          scroll={{ x: 1300 }}
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
              {detailTask.collected_items ?? 0}
            </Descriptions.Item>
            <Descriptions.Item label="耗时">
              {detailTask.duration_seconds > 0 ? fmtDuration(detailTask.duration_seconds) : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间" span={2}>
              {formatTimezone(detailTask.created_at)}
            </Descriptions.Item>
            <Descriptions.Item label="开始时间" span={2}>
              {formatTimezone(detailTask.started_at)}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间" span={2}>
              {formatTimezone(detailTask.completed_at)}
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

      {/* Error Detail Modal */}
      <Modal
        title={
          <Space>
            <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
            <span>同步失败详情</span>
          </Space>
        }
        open={errorDetailOpen}
        onCancel={() => setErrorDetailOpen(false)}
        footer={[
          <Button key="close" onClick={() => setErrorDetailOpen(false)}>
            关闭
          </Button>,
          <Button key="retry" type="primary" danger icon={<ReloadOutlined />} onClick={() => {
            if (errorTask) {
              setErrorDetailOpen(false)
              handleRetry(errorTask)
            }
          }}>
            重新同步
          </Button>,
        ]}
        width={640}
      >
        {errorTask && (
          <>
            <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="工作流">
                {errorTask.workflow_name || `#${errorTask.workflow_id}`}
              </Descriptions.Item>
              <Descriptions.Item label="任务ID">#{errorTask.id}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color="error">{errorTask.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="进度">
                {errorTask.processed_items}/{errorTask.total_items}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {formatTimezone(errorTask.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {formatTimezone(errorTask.completed_at)}
              </Descriptions.Item>
            </Descriptions>
            <Alert
              message="错误详情"
              description={
                <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0, fontSize: 13 }}>
                  {errorTask.error_message || errorTask.progress_message || '未知错误'}
                </pre>
              }
              type="error"
              showIcon
            />
          </>
        )}
      </Modal>
    </div>
  )
}
