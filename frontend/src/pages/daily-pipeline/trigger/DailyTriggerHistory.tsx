import { useState } from 'react'
import { Alert, Button, Drawer, Table, Tag, message } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { DeleteOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useDeleteDailyTriggerHistory, useDailyTriggerHistory } from '../../../hooks/useDailyPipeline'
import type { DailyTriggerHistoryItem } from '../../../types/dailyPipeline'
import TriggerHistoryDetail from './TriggerHistoryDetail'
import './Trigger.css'

const timeFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
const formatTime = (value: string) => timeFormat.format(new Date(value))
const resultColors = { 触发成功: 'green', 触发失败: 'red', 触发中: 'processing' }

const columns: ProColumns<DailyTriggerHistoryItem>[] = [
  { title: '触发时间', dataIndex: 'triggeredAt', width: 175, render: (_, row) => formatTime(row.triggeredAt) },
  { title: '触发事件', dataIndex: 'event', width: 110 },
  { title: '触发结果', dataIndex: 'result', width: 110, render: (_, row) => <Tag color={resultColors[row.result]}>{row.result}</Tag> },
]

interface DailyTriggerHistoryProps {
  open: boolean
  focusId?: string | null
  onClose: () => void
}

function DailyTriggerHistory({ open, focusId, onClose }: DailyTriggerHistoryProps) {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(5)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const deleteHistory = useDeleteDailyTriggerHistory()
  const { data, isLoading, isFetching, isError, refetch } = useDailyTriggerHistory(page, pageSize, open && !focusId)
  const handleDelete = async () => {
    try {
      const result = await deleteHistory.mutateAsync(selectedIds)
      setSelectedIds([])
      const lastPage = Math.max(1, Math.ceil(((data?.total ?? 0) - result.deleted) / pageSize))
      setPage((current) => Math.min(current, lastPage))
      message.success('已删除')
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '删除失败，请重试' : '删除失败，请重试')
    }
  }
  return <Drawer
    rootClassName="daily-trigger-history"
    title="触发历史"
    placement="right"
    width={760}
    styles={{ wrapper: { maxWidth: '100vw' } }}
    open={open}
    onClose={onClose}
  >
    {focusId ? <TriggerHistoryDetail key={focusId} id={focusId} /> : <>
    <div className="daily-cases-actions daily-trigger-history-actions">
      <Button
        danger
        icon={<DeleteOutlined />}
        disabled={!selectedIds.length || isFetching || isError}
        loading={deleteHistory.isPending}
        onClick={() => void handleDelete()}
      >删除</Button>
    </div>
    {isError ? <Alert type="error" showIcon message="触发历史加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } /> : <ProTable<DailyTriggerHistoryItem>
      search={false}
      headerTitle={false}
      toolBarRender={false}
      cardProps={false}
      tableAlertRender={false}
      {...dailyTableRefresh(refetch, isFetching || deleteHistory.isPending, {
        current: page, pageSize, total: data?.total ?? 0, showSizeChanger: false,
        onChange: (current, size) => { setPage(current); setPageSize(size) },
      })}
      rowKey="id"
      size="small"
      columns={[Table.SELECTION_COLUMN, Table.EXPAND_COLUMN, ...columns]}
      dataSource={data?.items ?? []}
      loading={isLoading || isFetching || deleteHistory.isPending}
      rowSelection={{
        selectedRowKeys: selectedIds,
        onChange: (keys) => setSelectedIds(keys.map(String)),
        preserveSelectedRowKeys: true,
        getCheckboxProps: () => ({ disabled: deleteHistory.isPending }),
      }}
      scroll={{ x: 580 }}

      expandable={{
        expandRowByClick: true,
        expandedRowRender: (record) => <TriggerHistoryDetail id={record.id} />,
        columnTitle: '日志',
      }}
    />}
    </>}
  </Drawer>
}

export default DailyTriggerHistory
