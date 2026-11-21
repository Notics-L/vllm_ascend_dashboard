import { useState } from 'react'
import { Card, Col, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import { BarChartOutlined } from '@ant-design/icons'
import { useMetrics } from '../../hooks/useNotifications'

const { Text } = Typography

const CHANNEL_ZH: Record<string, string> = { email: '邮件', sms: '短信', voice: '语音电话' }
const ACTION_ZH: Record<string, string> = { retry: '重试', abort: '取消', ack: '确认', ignore: '忽略' }

export default function MetricsPanel() {
  const [days, setDays] = useState(7)
  const query = useMetrics(days)
  const data = query.data

  return (
    <Card
      size="small"
      title={<Space><BarChartOutlined /><span>运营指标</span></Space>}
      extra={
        <Select
          size="small"
          value={days}
          onChange={setDays}
          options={[{ value: 1, label: '近 1 天' }, { value: 7, label: '近 7 天' }, { value: 30, label: '近 30 天' }]}
        />
      }
      loading={query.isLoading}
    >
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={6}><Card size="small"><Statistic title="通知事件" value={data?.events ?? 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="投递总数" value={data?.deliveries ?? 0} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="发送成功" value={data?.sent ?? 0} valueStyle={{ color: '#389e0d' }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="成功率" value={data?.success_rate ?? 0} suffix="%" /></Card></Col>
      </Row>

      <Text strong>渠道表现</Text>
      <Table
        rowKey="channel"
        size="small"
        style={{ marginBottom: 16 }}
        pagination={false}
        dataSource={Object.entries(data?.channels ?? {}).map(([channel, stat]) => ({ channel, ...stat }))}
        columns={[
          { title: '渠道', dataIndex: 'channel', render: (v: string) => <Tag color="blue">{CHANNEL_ZH[v] || v}</Tag> },
          { title: '总数', dataIndex: 'total' },
          { title: '成功', dataIndex: 'sent' },
          { title: '失败', dataIndex: 'failed' },
          { title: '处理中', dataIndex: 'pending' },
          { title: '成功率', dataIndex: 'success_rate', render: (v: number) => `${v}%` },
        ]}
      />

      <Text strong>IVR 动作</Text>
      <Table
        rowKey="action"
        size="small"
        pagination={false}
        dataSource={Object.entries(data?.actions ?? {}).map(([action, stat]) => ({ action, ...stat }))}
        columns={[
          { title: '动作', dataIndex: 'action', render: (v: string) => ACTION_ZH[v] || v },
          { title: '总数', dataIndex: 'total' },
          { title: '成功', dataIndex: 'success' },
          { title: '失败', dataIndex: 'failed' },
        ]}
      />
    </Card>
  )
}
