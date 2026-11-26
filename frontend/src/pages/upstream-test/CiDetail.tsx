import { useState } from 'react'
import dayjs from 'dayjs'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { getCiDetail } from '../../services/upstreamTest'
import { CASE_TABLE_WIDTH, ISSUE_TABLE_WIDTH } from './constants'
import { caseColumns, issueColumns } from './columns'
import { FailureReasonList, StatusBar } from './shared'

const { Text, Title } = Typography

const STATUS_OPTIONS = [
  { label: '通过', value: 'passed' },
  { label: '失败', value: 'failed' },
  { label: '跳过', value: 'skipped' },
  { label: '未知', value: 'unknown' },
]

const CONCLUSION_META: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'success' },
  failure: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
  in_progress: { label: '运行中', color: 'processing' },
  queued: { label: '排队中', color: 'warning' },
}

export default function UpstreamCiDetail() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const detailQuery = useQuery({
    queryKey: ['upstream-test', 'ci-detail', status, keyword, page],
    queryFn: () => getCiDetail({ status, keyword: keyword || undefined, page, page_size: 20 }),
  })

  const detail = detailQuery.data
  const ci = detail?.ci
  const latest = ci?.latest

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Space>
            <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/upstream-test')}>
              返回
            </Button>
            <Title level={5} style={{ margin: 0 }}>
              本周 CI 执行情况详情
            </Title>
            {ci?.workflow_url && (
              <a href={ci.workflow_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                <LinkOutlined /> workflow
              </a>
            )}
          </Space>
          {ci?.available ? (
            latest ? (
              <Space size={10} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  最近一次
                </Text>
                <a href={latest.html_url || '#'} target="_blank" rel="noreferrer">
                  #{latest.run_number} {ci.workflow_name || 'E2E-upstream'}
                </a>
                <Tag color={CONCLUSION_META[latest.conclusion || '']?.color || 'default'}>
                  {CONCLUSION_META[latest.conclusion || '']?.label || latest.conclusion || '运行中'}
                </Tag>
                <Tag>{latest.event || '-'}</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {latest.created_at ? dayjs(latest.created_at).format('YYYY-MM-DD HH:mm') : '-'}
                </Text>
              </Space>
            ) : (
              <Text type="secondary">暂无运行记录</Text>
            )
          ) : (
            <Alert type="info" showIcon message="CI 数据暂不可用" description={ci?.reason || undefined} />
          )}
          <StatusBar summary={detail?.weekly?.summary} />
          <FailureReasonList reasons={detail?.weekly?.failure_reasons} />
        </Space>
      </Card>

      <Card title="CI 用例明细" size="small">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Space wrap>
            <Select
              allowClear
              placeholder="状态"
              style={{ width: 120 }}
              value={status}
              onChange={(value) => {
                setStatus(value)
                setPage(1)
              }}
              options={STATUS_OPTIONS}
            />
            <Input.Search
              allowClear
              placeholder="按用例路径搜索"
              style={{ width: 320 }}
              onSearch={(value) => {
                setKeyword(value)
                setPage(1)
              }}
            />
            <Button icon={<ReloadOutlined />} onClick={() => detailQuery.refetch()}>
              刷新
            </Button>
          </Space>
          <Table
            size="small"
            rowKey="id"
            loading={detailQuery.isFetching}
            dataSource={detail?.cases.items || []}
            columns={caseColumns()}
            scroll={{ x: CASE_TABLE_WIDTH }}
            pagination={{
              current: page,
              pageSize: 20,
              total: detail?.cases.total || 0,
              showSizeChanger: false,
              onChange: setPage,
              showTotal: (total) => `共 ${total} 条`,
            }}
          />
        </Space>
      </Card>

      <Card
        title={`当前 CI 失败用例关联的 issue（${new Set((detail?.issues || []).map((item) => item.issue_number)).size} 个 / ${detail?.issues.length || 0} 条）`}
        size="small"
      >
        <Table
          size="small"
          rowKey="id"
          dataSource={detail?.issues || []}
          columns={issueColumns()}
          scroll={{ x: ISSUE_TABLE_WIDTH }}
          pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
          locale={{ emptyText: '失败用例暂未关联 issue' }}
        />
      </Card>
    </Space>
  )
}
