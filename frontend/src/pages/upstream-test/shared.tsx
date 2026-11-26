import { Space, Tag, Tooltip, Typography } from 'antd'

import type { FailureReason, StatusSummary } from '../../services/upstreamTest'
import { SEGMENT_COLOR, STATUS_META } from './constants'

const { Text } = Typography

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
