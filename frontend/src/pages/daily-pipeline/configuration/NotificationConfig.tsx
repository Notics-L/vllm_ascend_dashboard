import { useLayoutEffect, useRef, useState } from 'react'
import { Badge, Button, Select, Space, Spin, message } from 'antd'
import { ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import dayjs from 'dayjs'
import { useDailyFailureNotificationConfig, useDailyNotificationRecords, useSaveDailyFailureNotificationConfig } from '../../../hooks/useDailyPipeline'
import type { DailyNotificationConsumptionRecord, DailyNotificationConsumptionResult, DailyNotificationConsumptionStatus } from '../../../types/dailyPipeline'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyRefreshFeedback } from '../shared/useDailyRefreshFeedback'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'
import './NotificationRecords.css'

const STATUSES: Record<DailyNotificationConsumptionStatus, { text: string; color: string }> = {
  pending: { text: '待消费', color: '#8c8c8c' },
  processing: { text: '消费中', color: '#1677ff' },
  completed: { text: '已消费', color: '#52c41a' },
  ended: { text: '已结束', color: '#8c8c8c' },
}
const RESULTS: Record<DailyNotificationConsumptionResult, { text: string; color: string }> = {
  waiting: { text: '未回传', color: '#8c8c8c' },
  sent: { text: '发送成功', color: '#52c41a' },
  failed: { text: '发送失败', color: '#ff4d4f' },
  unknown: { text: '结果未知', color: '#faad14' },
  ended: { text: '已结束', color: '#8c8c8c' },
}
const time = (value: string | null) => value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—'

export default function NotificationConfig({ active, canEdit = false }: { active: boolean; canEdit?: boolean }) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [result, setResult] = useState<DailyNotificationConsumptionResult>()
  const [status, setStatus] = useState<DailyNotificationConsumptionStatus>()
  const query = useDailyNotificationRecords(page, pageSize, result, status, active)
  const recipients = useDailyFailureNotificationConfig(active)
  const saveRecipients = useSaveDailyFailureNotificationConfig()
  const [draftRecipients, setDraftRecipients] = useState<string[]>()
  const opening = useDailyTabOpening(active)
  const refresh = useDailyRefreshFeedback(async () => {
    const [config] = await Promise.all([recipients.refetch(), query.refetch()])
    if (config.isSuccess) setDraftRecipients(undefined)
  })
  const pageBusy = saveRecipients.isPending || refresh.pending
  const loading = active && !pageBusy && (opening || query.isFetching)
  const controls = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (controls.current) controls.current.inert = pageBusy
  }, [pageBusy])
  useDailyQueryError(query, '消费记录获取失败，请稍后重试', active)
  useDailyQueryError(recipients, '失败通知人获取失败，请稍后重试', active)
  const githubNames = draftRecipients ?? recipients.data?.githubNames
  const save = async () => {
    if (!githubNames) return
    const names = githubNames.map((name) => name.trim().toLowerCase())
    if (names.some((name) => !/^[a-z0-9][a-z0-9-]{0,99}$/.test(name))) {
      void message.error('GitHub 用户名格式无效')
      return
    }
    try {
      await saveRecipients.mutateAsync([...new Set(names)])
      setDraftRecipients(undefined)
      void message.success('已保存')
    } catch {
      void message.error('保存失败，请稍后重试')
    }
  }
  const columns: ProColumns<DailyNotificationConsumptionRecord>[] = [
    { title: '通知名称', dataIndex: 'notificationName', width: 120 },
    { title: '生成时间', dataIndex: 'createdAt', width: 180, render: (_, row) => time(row.createdAt) },
    { title: '领取时间', dataIndex: 'claimedAt', width: 180, render: (_, row) => time(row.claimedAt) },
    { title: 'GitHub 用户', dataIndex: 'githubName', width: 150, ellipsis: true },
    { title: '通知内容', dataIndex: 'content', ellipsis: true },
    { title: '消费状态', dataIndex: 'status', width: 120, filters: Object.entries(STATUSES).map(([value, item]) => ({ text: item.text, value })),
      filterMultiple: false, filteredValue: status ? [status] : null,
      render: (_, row) => <Badge color={STATUSES[row.status].color} text={STATUSES[row.status].text} /> },
    { title: '消费结果', dataIndex: 'result', width: 130, filters: Object.entries(RESULTS).map(([value, item]) => ({ text: item.text, value })),
      filterMultiple: false, filteredValue: result ? [result] : null,
      render: (_, row) => <Badge color={RESULTS[row.result].color} text={RESULTS[row.result].text} /> },
    { title: '消费者', dataIndex: 'consumer', width: 120, ellipsis: true },
    { title: '确认时间', dataIndex: 'confirmedAt', width: 180, render: (_, row) => time(row.confirmedAt) },
  ]
  return <div className="daily-notification-records" aria-busy={pageBusy}>
    <div ref={controls}>
      <div className="daily-notification-config">
        <label htmlFor="daily-failure-notification-recipients">失败通知人</label>
        <Select id="daily-failure-notification-recipients" mode="tags"
          placeholder="输入 GitHub 用户名后回车" tokenSeparators={[',', '，', ' ']} maxCount={100}
          value={githubNames ?? []} onChange={setDraftRecipients}
          loading={recipients.isFetching && !pageBusy} disabled={!canEdit || !recipients.data} />
      </div>
      <div className="daily-notification-records-title">消费记录</div>
      <ProTable<DailyNotificationConsumptionRecord> size="small" rowKey="id" columns={columns}
        dataSource={query.data?.items ?? []} search={false} cardProps={false}
        toolBarRender={false} tableAlertRender={false}
        onChange={(_pagination, filters, _sorter, extra) => {
          if (extra.action === 'filter') {
            setResult(filters.result?.[0] as DailyNotificationConsumptionResult | undefined)
            setStatus(filters.status?.[0] as DailyNotificationConsumptionStatus | undefined)
            setPage(1)
          }
        }}
        {...dailyTableRefresh(query.refetch, loading, {
          current: page, pageSize, total: query.data?.total ?? 0, showSizeChanger: true,
          pageSizeOptions: ['5', '10', '20', '50'],
          onChange: (nextPage, nextSize) => { setPage(nextSize === pageSize ? nextPage : 1); setPageSize(nextSize) },
        })}
        scroll={{ x: 1400, y: 480 }} />
      <Space className="daily-cases-actions daily-notification-actions">
        {canEdit && <Button type="primary" icon={<SaveOutlined />}
          disabled={!githubNames || (recipients.isFetching && !pageBusy)} onClick={() => void save()}>保存</Button>}
        <Button className="daily-trigger-refresh" icon={<ReloadOutlined />}
          onClick={() => void refresh.run()}>刷新</Button>
      </Space>
    </div>
    {pageBusy && <div className="daily-page-loading-mask" role="status" aria-label={saveRecipients.isPending ? '正在保存通知配置' : '正在刷新消息通知'}>
      <Spin />
    </div>}
  </div>
}
