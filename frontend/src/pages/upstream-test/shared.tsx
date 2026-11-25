import { Space, Tag, Tooltip, Typography } from 'antd'

import type { FailureReason, StatusSummary } from '../../services/upstreamTest'

const { Text } = Typography

export const STATUS_META: Record<string, { label: string; color: string }> = {
  passed: { label: '通过', color: 'success' },
  failed: { label: '失败', color: 'error' },
  skipped: { label: '跳过', color: 'default' },
  unknown: { label: '未知', color: 'warning' },
}

// 分段横条配色：红色只留给"失败"，其余状态各有颜色，
// 避免整条横条被一个颜色占满而看不出各状态占比。
export const SEGMENT_COLOR: Record<string, string> = {
  passed: '#16a34a',
  failed: '#dc2626',
  skipped: '#94a3b8',
  unknown: '#d97706',
}

export const IMPACT_COLOR: Record<string, string> = {
  大: 'red',
  中: 'orange',
  小: 'blue',
}

/** 用例/issue 路径列统一宽度：太窄会导致路径按单词截断、行高拉长。 */
export const PATH_COLUMN_WIDTH = 420

/**
 * 表格横向滚动宽度必须等于各列 width 之和，否则 antd 会按容器宽度等比压缩列，
 * 设置的 width 不会生效（实测「关联用例」会被压到 32px）。
 *
 * issue 列顺序：Issue(100) | 影响程度(90) | 影响(340) | 标题(380) | 关联用例(420) | 备注(140)
 */
export const ISSUE_TABLE_WIDTH = 100 + 90 + 340 + 380 + PATH_COLUMN_WIDTH + 140
export const CASE_TABLE_WIDTH = PATH_COLUMN_WIDTH + 90 + 160 + 360

export function StatusTag({ status }: { status: string | null | undefined }) {
  const meta = STATUS_META[status || ''] || { label: status || '未知', color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

export function renderPath(value: string | null | undefined) {
  if (!value) return <Text type="secondary">-</Text>
  return (
    <Text style={{ fontSize: 12, wordBreak: 'break-word' }} copyable={{ tooltips: false }}>
      {value}
    </Text>
  )
}

/**
 * 按状态着色的分段横条。每段宽度即该状态占比、颜色即该状态。
 */
export function StatusBar({ summary }: { summary?: StatusSummary }) {
  if (!summary || !summary.total) {
    return <Text type="secondary">暂无数据</Text>
  }
  const keys: Array<keyof StatusSummary> = ['passed', 'failed', 'skipped', 'unknown']
  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      <div
        style={{
          display: 'flex',
          width: '100%',
          height: 12,
          borderRadius: 6,
          overflow: 'hidden',
          background: '#eef2f7',
        }}
        role="img"
        aria-label={`共 ${summary.total} 条，通过 ${summary.passed}，失败 ${summary.failed}，跳过 ${summary.skipped}，未知 ${summary.unknown}`}
      >
        {keys.map((key) => {
          const value = Number(summary[key])
          if (!value) return null
          const ratio = (value / summary.total) * 100
          return (
            <Tooltip key={key} title={`${STATUS_META[key].label} ${value}（${ratio.toFixed(1)}%）`}>
              <div
                style={{
                  width: `${ratio}%`,
                  background: SEGMENT_COLOR[key],
                  // 极小分段保留最小可见宽度，避免占比过低时分段消失
                  minWidth: ratio > 0 && ratio < 0.6 ? 3 : undefined,
                }}
              />
            </Tooltip>
          )
        })}
      </div>
      <Space size={14} wrap>
        {keys.map((key) => (
          <Space key={key} size={4}>
            <span
              style={{
                display: 'inline-block',
                width: 8,
                height: 8,
                borderRadius: 2,
                background: SEGMENT_COLOR[key],
              }}
            />
            <Text style={{ fontSize: 12 }}>
              {STATUS_META[key].label} {Number(summary[key])}
            </Text>
          </Space>
        ))}
        <Text type="secondary" style={{ fontSize: 12 }}>
          共 {summary.total}
        </Text>
      </Space>
    </Space>
  )
}

export function FailureReasonList({ reasons }: { reasons?: FailureReason[] }) {
  if (!reasons?.length) return null
  return (
    <Space size={6} wrap>
      <Text type="secondary" style={{ fontSize: 12 }}>
        未通过原因：
      </Text>
      {reasons.map((item) => (
        <Tag key={item.reason} color="volcano" style={{ marginInlineEnd: 0 }}>
          {item.reason} · {item.count}
        </Tag>
      ))}
    </Space>
  )
}
