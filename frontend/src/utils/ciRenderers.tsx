import { Space, Tag, Tooltip } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  MinusCircleOutlined,
  PlayCircleOutlined,
  SyncOutlined,
  AppstoreOutlined,
} from '@ant-design/icons'
import type { CIRunJobStats } from '../services/ci'

/**
 * Render status tag for CI workflow/job status - Stripe Design System
 */
export const renderStatusTag = (status: string) => {
  const statusMap: Record<string, { color: string; icon: JSX.Element; text: string }> = {
    completed: { color: '#1890ff', icon: <CheckCircleOutlined />, text: '已完成' },
    in_progress: { color: '#1890ff', icon: <SyncOutlined spin />, text: '进行中' },
    queued: { color: '#64748d', icon: <ClockCircleOutlined />, text: '等待中' },
  }
  const config = statusMap[status] || { color: '#64748d', icon: <ClockCircleOutlined />, text: status }
  return (
    <Tag
      color={config.color}
      icon={config.icon}
      className="stripe-ci-tag"
    >
      {config.text}
    </Tag>
  )
}

/**
 * Render conclusion tag for CI workflow/job conclusion
 * Handles all possible conclusion values from GitHub Actions - Stripe Design System
 */
export const renderConclusionTag = (conclusion: string | null) => {
  if (!conclusion) return <span className="stripe-ci-empty">-</span>

  const conclusionMap: Record<string, { color: string; icon: JSX.Element; text: string }> = {
    success: { color: '#15be53', icon: <CheckCircleOutlined />, text: '成功' },
    failure: { color: '#ff4d4f', icon: <CloseCircleOutlined />, text: '失败' },
    cancelled: { color: '#faad14', icon: <ClockCircleOutlined />, text: '已取消' },
    skipped: { color: '#64748d', icon: <ClockCircleOutlined />, text: '已跳过' },
    timed_out: { color: '#ff4d4f', icon: <CloseCircleOutlined />, text: '超时' },
    action_required: { color: '#faad14', icon: <ClockCircleOutlined />, text: '需处理' },
    stale: { color: '#64748d', icon: <ClockCircleOutlined />, text: '已过期' },
    startup_failure: { color: '#ff4d4f', icon: <CloseCircleOutlined />, text: '启动失败' },
    neutral: { color: '#64748d', icon: <ClockCircleOutlined />, text: '中性' },
  }
  const config = conclusionMap[conclusion] || { color: '#64748d', icon: <ClockCircleOutlined />, text: conclusion }
  return (
    <Tag
      color={config.color}
      icon={config.icon}
      className="stripe-ci-tag"
    >
      {config.text}
    </Tag>
  )
}

/**
 * Format duration in seconds to human-readable string - Stripe Design System
 */
export const formatDuration = (seconds: number | null) => {
  if (!seconds || seconds < 0) return '-'
  // 取整到秒
  const secs = Math.round(seconds)
  const hours = Math.floor(secs / 3600)
  const minutes = Math.floor((secs % 3600) / 60)
  const remainingSecs = secs % 60

  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  if (minutes > 0) {
    return `${minutes}m ${remainingSecs}s`
  }
  return `${remainingSecs}s`
}

/**
 * Render hardware tag with consistent colors - Stripe Design System
 */
export const renderHardwareTag = (hardware: string | null) => {
  if (!hardware || hardware === 'unknown') return '-'
  const colorMap: Record<string, string> = {
    A2: '#15be53',
    A3: '#1890ff',
    '310P': '#faad14',
  }
  const color = colorMap[hardware] || '#64748d'
  return (
    <Tag color={color} className="stripe-hardware-tag">
      {hardware}
    </Tag>
  )
}

export const renderRunJobStats = (stats?: CIRunJobStats | null) => {
  if (!stats) return <span className="stripe-ci-empty">-</span>
  const items = [
    { key: 'total', value: stats.total, color: '#262626', icon: <AppstoreOutlined />, label: '总 Job 数' },
    { key: 'executed', value: stats.executed, color: '#1890ff', icon: <PlayCircleOutlined />, label: '实际执行数' },
    { key: 'skipped', value: stats.skipped, color: '#8c8c8c', icon: <MinusCircleOutlined />, label: '已跳过数' },
    { key: 'success', value: stats.success, color: '#52c41a', icon: <CheckCircleOutlined />, label: '成功数' },
    { key: 'failure', value: stats.failure, color: '#ff4d4f', icon: <CloseCircleOutlined />, label: '失败数' },
    { key: 'running', value: stats.in_progress, color: '#1890ff', icon: <SyncOutlined spin={stats.in_progress > 0} />, label: '进行中数' },
    { key: 'cancelled', value: stats.cancelled, color: '#faad14', icon: <ClockCircleOutlined />, label: '已取消数' },
  ]
  return (
    <Space size={4} wrap>
      {items.map((item) => (
        <Tooltip key={item.key} title={`${item.label}：${item.value}`}>
          <Tag color={item.color} icon={item.icon} style={{ marginInlineEnd: 0, cursor: 'default' }}>
            <span style={{ paddingInlineStart: 6, fontVariantNumeric: 'tabular-nums' }} aria-label={String(item.value)}>{String(item.value)}</span>
          </Tag>
        </Tooltip>
      ))}
    </Space>
  )
}
