import { useState } from 'react'
import { Button, Card, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, SendOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useDeliveries, useRetryDelivery } from '../../hooks/useNotifications'
import type { NotificationDelivery } from '../../services/notifications'

const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  pending: 'gold',
  sent: 'green',
  delivered: 'green',
  failed: 'red',
  no_answer: 'volcano',
  busy: 'orange',
  answered: 'blue',
}

const STATUS_ZH: Record<string, string> = {
  pending: '待发送',
  sent: '已发送',
  delivered: '已送达',
  failed: '失败',
  no_answer: '未接听',
  busy: '占线',
  answered: '已接听',
}

const SOURCE_ZH: Record<string, string> = {
  image_build_monitor: '镜像构建监控',
  alert_rules: '资源告警',
  daily_report: '每日报告',
  notification: '系统通知',
}

function fmtTime(value: string | null) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'
}

export default function DeliveriesPanel() {
  const [status, setStatus] = useState<string | undefined>()
  const [channel, setChannel] = useState<string | undefined>()
  const [source, setSource] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const pageSize = 20
  const query = useDeliveries({ status, channel, source, page, page_size: pageSize })
  const retry = useRetryDelivery()

  const handleRetry = async (row: NotificationDelivery) => {
    try {
      await retry.mutateAsync(row.id)
      message.success('已重新发送')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '重试失败')
    }
  }

  return (
    <Card
      className="nc-panel"
      size="small"
      title="发送记录"
      extra={
        <Space wrap>
          <Select
            allowClear
            size="small"
            placeholder="来源"
            style={{ width: 150 }}
            value={source}
            onChange={(v) => { setSource(v); setPage(1) }}
            options={Object.entries(SOURCE_ZH).map(([value, label]) => ({ value, label }))}
          />
          <Select
            allowClear
            size="small"
            placeholder="渠道"
            style={{ width: 110 }}
            value={channel}
            onChange={(v) => { setChannel(v); setPage(1) }}
            options={[{ value: 'email', label: '邮件' }, { value: 'sms', label: '短信' }, { value: 'voice', label: '语音' }]}
          />
          <Select
            allowClear
            size="small"
            placeholder="状态"
            style={{ width: 120 }}
            value={status}
            onChange={(v) => { setStatus(v); setPage(1) }}
            options={Object.entries(STATUS_ZH).map(([value, label]) => ({ value, label }))}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={() => query.refetch()}>刷新</Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={query.isLoading}
        dataSource={query.data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: query.data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        scroll={{ x: 1100 }}
        columns={[
          {
            title: '事件', key: 'event', width: 300,
            render: (_, row) => (
              <div>
                <Text strong>{row.title}</Text>
                {row.summary && (
                  <div style={{ fontSize: 12, color: '#8c8c8c', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {row.summary}
                  </div>
                )}
              </div>
            ),
          },
          {
            title: '来源', dataIndex: 'source', width: 130,
            render: (v: string) => <Tag color="blue">{SOURCE_ZH[v] || v}</Tag>,
          },
          {
            title: '渠道', dataIndex: 'channel', width: 80,
            render: (v: string) => <Tag>{v === 'email' ? '邮件' : v}</Tag>,
          },
          { title: '接收人', dataIndex: 'recipient', width: 200, ellipsis: true },
          {
            title: '状态', dataIndex: 'status', width: 90,
            render: (v: string) => <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_ZH[v] || v}</Tag>,
          },
          {
            title: '尝试', dataIndex: 'attempt', width: 60,
            render: (v: number) => <Text type="secondary">#{v}</Text>,
          },
          {
            title: '发生时间', dataIndex: 'created_at', width: 165,
            render: (v: string) => <Text style={{ whiteSpace: 'nowrap' }}>{fmtTime(v)}</Text>,
          },
          {
            title: '失败原因', dataIndex: 'error', width: 220, ellipsis: true,
            render: (v: string | null) => v ? (
              <Tooltip title={v}><Text type="danger" style={{ fontSize: 12 }}>{v}</Text></Tooltip>
            ) : '-',
          },
          {
            title: '操作', key: 'actions', width: 90, fixed: 'right',
            render: (_, row) => (
              <Button
                size="small"
                type="link"
                icon={<SendOutlined />}
                disabled={row.channel !== 'email'}
                loading={retry.isPending}
                onClick={() => handleRetry(row)}
              >
                重试
              </Button>
            ),
          },
        ]}
      />
    </Card>
  )
}
