import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Input, Space, Table, Tabs, Typography } from 'antd'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { getUpstreamOverview, listUpstreamIssues } from '../../services/upstreamTest'
import { issueColumns } from './columns'
import { ISSUE_TABLE_WIDTH } from './constants'

const { Title } = Typography

type IssueState = 'open' | 'resolved' | 'other'

export default function UpstreamIssueDetail() {
  const navigate = useNavigate()
  const [state, setState] = useState<IssueState>('open')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const overviewQuery = useQuery({
    queryKey: ['upstream-test', 'overview'],
    queryFn: getUpstreamOverview,
  })
  const summary = overviewQuery.data?.issues?.summary || {}

  const issuesQuery = useQuery({
    queryKey: ['upstream-test', 'issues', state, keyword, page],
    queryFn: () =>
      listUpstreamIssues({ state, keyword: keyword || undefined, page, page_size: 20 }),
  })

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space>
          <Button type="text" size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/upstream-test')}>
            返回
          </Button>
          <Title level={5} style={{ margin: 0 }}>
            全量 issue 情况
          </Title>
        </Space>
      </Card>

      <Card size="small">
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Tabs
            activeKey={state}
            onChange={(key) => {
              setState(key as IssueState)
              setPage(1)
            }}
            items={[
              { key: 'open', label: `未关闭 ${summary.open ?? 0}` },
              { key: 'resolved', label: `已修复 ${summary.resolved ?? 0}` },
              { key: 'other', label: `其他 ${summary.other ?? 0}` },
            ]}
          />
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="按标题搜索"
              style={{ width: 320 }}
              onSearch={(value) => {
                setKeyword(value)
                setPage(1)
              }}
            />
            <Button icon={<ReloadOutlined />} onClick={() => issuesQuery.refetch()}>
              刷新
            </Button>
          </Space>
          <Table
            size="small"
            rowKey="id"
            loading={issuesQuery.isFetching}
            dataSource={issuesQuery.data?.items || []}
            columns={issueColumns()}
            scroll={{ x: ISSUE_TABLE_WIDTH }}
            pagination={{
              current: page,
              pageSize: 20,
              total: issuesQuery.data?.total || 0,
              showSizeChanger: false,
              onChange: setPage,
              showTotal: (total) => `共 ${total} 条`,
            }}
            locale={{ emptyText: '暂无数据' }}
          />
        </Space>
      </Card>
    </Space>
  )
}
