import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { getVersionDetail, type StatusSummary } from '../../services/upstreamTest'
import { CASE_TABLE_WIDTH, FailureReasonList, ISSUE_TABLE_WIDTH, StatusBar } from './shared'
import { caseColumns, issueColumns } from './columns'

const { Text, Title } = Typography

const STATUS_OPTIONS = [
  { label: '通过', value: 'passed' },
  { label: '失败', value: 'failed' },
  { label: '跳过', value: 'skipped' },
  { label: '未知', value: 'unknown' },
]

export default function UpstreamVersionDetail() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string | undefined>()
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const detailQuery = useQuery({
    queryKey: ['upstream-test', 'version-detail', status, keyword, page],
    queryFn: () => getVersionDetail({ status, keyword: keyword || undefined, page, page_size: 20 }),
  })

  const detail = detailQuery.data
  const version = detail?.version

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Space>
            <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/upstream-test')}>
              返回
            </Button>
            <Title level={5} style={{ margin: 0 }}>
              最新版本新增用例详情
            </Title>
            {version?.name && <Tag color="blue">{version.name}</Tag>}
            {version?.previous_name && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                相对 {version.previous_name}
              </Text>
            )}
          </Space>
          <StatusBar summary={version?.summary as StatusSummary | undefined} />
          <FailureReasonList reasons={version?.failure_reasons} />
        </Space>
      </Card>

      <Card title="用例详情" size="small">
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
        title={`未通过用例关联的 issue（${new Set((detail?.issues || []).map((item) => item.issue_number)).size} 个 / ${detail?.issues.length || 0} 条）`}
        size="small"
      >
        <Table
          size="small"
          rowKey="id"
          dataSource={detail?.issues || []}
          columns={issueColumns()}
          scroll={{ x: ISSUE_TABLE_WIDTH }}
          pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
          locale={{ emptyText: '未通过用例暂未关联 issue' }}
        />
      </Card>
    </Space>
  )
}
