import { Descriptions, Space, Tag, Typography } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useDailyTriggerHistoryDetail } from '../../../hooks/useDailyPipeline'
import type { DailyTriggerHistoryDetail } from '../../../types/dailyPipeline'
import './Trigger.css'

const timeFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const formatTime = (value: string) => timeFormat.format(new Date(value))
const resultColors = { 触发成功: 'green', 触发失败: 'red' }


const caseColumns: ProColumns<DailyTriggerHistoryDetail['dailyCases'][number]>[] = [
  { title: '用例名称', dataIndex: 'name', ellipsis: true },
  { title: '硬件类型', dataIndex: 'hardware', width: 100 },
  { title: '测试类型', dataIndex: 'testType', width: 90 },
]

function TriggerHistoryDetail({ id }: { id: string }) {
  const history = useDailyTriggerHistoryDetail(id)
  useDailyQueryError(history, '日志加载失败')
  const { data, isLoading, isFetching, refetch } = history
  return <Space className="daily-trigger-history-detail" direction="vertical" size={12}>
    <Descriptions size="small" column={1} items={[
      { key: 'time', label: '触发时间', children: data ? formatTime(data.triggeredAt) : '—' },
      { key: 'event', label: '触发事件', children: data?.event ?? '—' },
      { key: 'result', label: '触发结果', children: data ? <Tag color={resultColors[data.result]}>{data.result}</Tag> : '—' },
      { key: 'workflow', label: '流水线', children: data?.workflow ?? '—' },
      { key: 'cron', label: 'Cron', children: data?.cron ?? '—' },
    ]} />
    <Typography.Text>Daily 用例</Typography.Text>
    <ProTable search={false} headerTitle={false} toolBarRender={false} cardProps={false} {...dailyTableRefresh(refetch, isLoading || isFetching, false)} loading={isLoading || isFetching} rowKey="id" size="small" columns={caseColumns} dataSource={data?.dailyCases ?? []}  scroll={{ x: 500 }} />
    <Typography.Text>日志</Typography.Text>
    <pre className="daily-trigger-history-log">{data?.logs.map((item) => `${formatTime(item.time)} [${item.level}] ${item.message}`).join('\n')}</pre>
  </Space>
}

export default TriggerHistoryDetail
