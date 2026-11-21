import { useState } from 'react'
import { Button, Card, Empty, Pagination, Segmented, Space, Tag, Typography } from 'antd'
import { InboxOutlined, MessageOutlined, PhoneOutlined, ReloadOutlined } from '@ant-design/icons'
import { useInbox } from '../../hooks/useNotifications'
import type { InboxItem } from '../../services/notifications'

const { Text, Paragraph } = Typography

function splitPhone(phone?: string | null): { code: string; number: string } {
  if (!phone) return { code: '+86', number: '' }
  const codes = ['+852', '+853', '+886', '+81', '+82', '+65', '+44', '+49', '+33', '+61', '+91', '+86', '+1']
  const code = codes.find((item) => phone.startsWith(item))
  if (!code) return { code: '+86', number: phone }
  return { code, number: phone.slice(code.length) }
}

function PhoneBadge({ phone }: { phone: string }) {
  const { code, number } = splitPhone(phone)
  return (
    <span className="nc-phone">
      <span className="nc-phone-code">{code}</span>
      <span className="nc-phone-number">{number}</span>
    </span>
  )
}

function formatTime(value: string | null): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(5, 19)
}

export default function InboxPanel() {
  const [filter, setFilter] = useState<'all' | 'sms' | 'voice'>('all')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const { data, isLoading, isFetching, refetch } = useInbox({
    channel: filter === 'all' ? undefined : filter,
    page,
    page_size: pageSize,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <Card
      className="nc-panel"
      styles={{ body: { padding: 20 } }}
      loading={isLoading}
      title={
        <Space>
          <InboxOutlined />
          <span>收件箱</span>
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
      }
    >
      <div className="nc-inbox-toolbar">
        <Segmented
          value={filter}
          onChange={(value) => {
            setFilter(value as 'all' | 'sms' | 'voice')
            setPage(1)
          }}
          options={[
            { value: 'all', label: '全部' },
            { value: 'sms', label: '短信' },
            { value: 'voice', label: '语音电话' },
          ]}
        />
        <Text type="secondary">统一展示短信与语音的发送情况，包含模拟与真实渠道。</Text>
      </div>

      {items.length === 0 ? (
        <Empty description="暂无发送记录" />
      ) : (
        <>
          <div className="nc-inbox-list">
            {items.map((item) => (
              <InboxRow key={`${item.channel}-${item.id}`} item={item} />
            ))}
          </div>
          <div className="nc-inbox-pager">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={total}
              showSizeChanger
              pageSizeOptions={[10, 20, 50]}
              showTotal={(count, range) => `${range[0]}-${range[1]} / 共 ${count} 条`}
              onChange={(nextPage, nextSize) => {
                setPage(nextSize !== pageSize ? 1 : nextPage)
                setPageSize(nextSize)
              }}
            />
          </div>
        </>
      )}
    </Card>
  )
}

function InboxRow({ item }: { item: InboxItem }) {
  const isSms = item.channel === 'sms'
  const success = item.status === 'sent'
  return (
    <div className={`nc-inbox-row nc-inbox-${item.channel}`}>
      <div className="nc-inbox-icon">
        {isSms ? <MessageOutlined /> : <PhoneOutlined />}
      </div>
      <div className="nc-inbox-main">
        <div className="nc-inbox-head">
          <Space size={8} wrap>
            <Tag color={isSms ? 'blue' : 'purple'}>{isSms ? '短信' : '语音电话'}</Tag>
            <PhoneBadge phone={item.recipient} />
            <Tag color={success ? 'green' : 'red'}>{success ? '发送成功' : '发送失败'}</Tag>
            {item.provider && <Tag>{item.provider === 'mock' ? '模拟' : item.provider}</Tag>}
            {!isSms && typeof item.duration_seconds === 'number' && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {item.answered ? `通话 ${item.duration_seconds}s` : '未接通'}
              </Text>
            )}
            {isSms && item.segments ? (
              <Text type="secondary" style={{ fontSize: 12 }}>{item.segments} 条计费</Text>
            ) : null}
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{formatTime(item.created_at)}</Text>
        </div>
        <div className="nc-inbox-bubble">
          <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{item.content || item.title}</Paragraph>
        </div>
        <div className="nc-inbox-meta">
          <Text type={item.error ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
            事件 #{item.event_id} · {item.severity}
            {item.provider_message_id ? ` · ${item.provider_message_id}` : ''}
            {item.error ? ` · ${item.error}` : ''}
          </Text>
        </div>
      </div>
    </div>
  )
}
