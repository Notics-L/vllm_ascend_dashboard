import { Tag, Tooltip, Typography, type TableColumnsType } from 'antd'

import type { IssueRow } from '../../services/upstreamTest'
import { IMPACT_COLOR, PATH_COLUMN_WIDTH } from './shared'

const { Text } = Typography

/**
 * issue 表格列（首页与各详情页共用）。
 *
 * 列顺序：Issue | 影响程度 | 影响 | 标题 | 关联用例 | 备注。
 * 「影响程度」「影响」紧跟 Issue 之后，因为在排查时可读性最高的信息是
 * "这个问题有多严重、影响什么"，标题和用例路径可以后置。
 *
 * 长文本列必须给足宽度：实测未设 width/ellipsis 时「关联用例」会被挤到 32px，
 * 一行只放得下两个字母，而「备注」被撑到 1700px 以上。「影响」列则相反，
 * 要求完整可见，因此不设 ellipsis、允许自动换行。
 */
export function issueColumns(): TableColumnsType<IssueRow> {
  return [
    {
      title: 'Issue',
      dataIndex: 'issue_number',
      width: 100,
      render: (value: number | null, record) =>
        value ? (
          <a href={record.issue_url || '#'} target="_blank" rel="noreferrer">
            #{value}
          </a>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '影响程度',
      dataIndex: 'impact_level',
      width: 90,
      render: (value: string | null) =>
        value ? <Tag color={IMPACT_COLOR[value] || 'default'}>{value}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '影响',
      dataIndex: 'impact',
      width: 340,
      render: (value: string | null) =>
        value ? (
          <Text style={{ fontSize: 12, whiteSpace: 'normal', wordBreak: 'break-word' }}>{value}</Text>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '标题',
      dataIndex: 'issue_title',
      width: 380,
      ellipsis: true,
      render: (value: string | null, record) =>
        value ? (
          <Tooltip title={value}>
            <a href={record.issue_url || '#'} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
              {value}
            </a>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '关联用例',
      dataIndex: 'test_path',
      width: PATH_COLUMN_WIDTH,
      ellipsis: { showTitle: false },
      render: (value: string | null) =>
        value ? (
          <Tooltip title={value}>
            <Text style={{ fontSize: 12 }}>{value}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
    {
      title: '备注',
      dataIndex: 'notes',
      width: 140,
      ellipsis: true,
      render: (value: string | null) => (value ? <Tag>{value}</Tag> : <Text type="secondary">-</Text>),
    },
  ]
}

/** 用例表格列（版本详情与 CI 详情共用） */
export function caseColumns(): TableColumnsType<{
  id: number
  test_path: string
  status: string
  category: string | null
  analysis: string | null
}> {
  return [
    {
      title: '用例路径',
      dataIndex: 'test_path',
      width: PATH_COLUMN_WIDTH,
      ellipsis: { showTitle: false },
      render: (value: string) => (
        <Tooltip title={value}>
          <Text style={{ fontSize: 12 }}>{value}</Text>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (value: string) => (
        <Tag
          color={
            value === 'passed'
              ? 'success'
              : value === 'failed'
                ? 'error'
                : value === 'unknown'
                  ? 'warning'
                  : 'default'
          }
        >
          {value === 'passed' ? '通过' : value === 'failed' ? '失败' : value === 'unknown' ? '未知' : '跳过'}
        </Tag>
      ),
    },
    {
      title: '原因分类',
      dataIndex: 'category',
      width: 160,
      render: (value: string | null) =>
        value ? <Tag color="volcano">{value}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '分析',
      dataIndex: 'analysis',
      width: 360,
      ellipsis: { showTitle: false },
      render: (value: string | null) =>
        value ? (
          <Tooltip title={value}>
            <Text style={{ fontSize: 12 }}>{value}</Text>
          </Tooltip>
        ) : (
          <Text type="secondary">-</Text>
        ),
    },
  ]
}
