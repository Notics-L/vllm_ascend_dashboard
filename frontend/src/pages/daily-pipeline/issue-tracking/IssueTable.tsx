import { useState } from 'react'
import { Alert, Button, Modal, Space, Tag, message } from 'antd'
import { CheckCircleOutlined, DeleteOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyIssues, useDailyIssueMutations } from '../../../hooks/useDailyPipeline'
import type { DailyIssueItem } from '../../../types/dailyPipeline'
import CollectionIssueLink from '../collection/CollectionIssueLink'
import CollectionRunLink from '../collection/CollectionRunLink'
import IssueProgressLink from './IssueProgressLink'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'

const columns: ProColumns<DailyIssueItem>[] = [
  { title: '编号', dataIndex: 'number', width: 110, render: (_, row) => <CollectionIssueLink issue={row} showState={false} /> },
  { title: '标题', dataIndex: 'title', width: 360, ellipsis: true },
  { title: '状态', dataIndex: 'state', width: 100, render: (_, row) => <Tag color={row.state === 'open' ? 'blue' : 'default'}>{row.state === 'open' ? 'open' : 'close'}</Tag> },
  { title: 'Run ID', dataIndex: 'runId', width: 125, render: (_, row) => row.runId ? <CollectionRunLink runId={row.runId} /> : '—' },
  { title: '处理进展', key: 'processingProgress', width: 130, render: (_, row) => <IssueProgressLink issueNumber={row.number} /> },
]

function IssueTable() {
  const { data = [], isLoading, isFetching, isError, refetch } = useDailyIssues()
  const [selectedNumbers, setSelectedNumbers] = useState<number[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const { closeIssues, deleteIssues } = useDailyIssueMutations()
  const [modal, contextHolder] = Modal.useModal()
  const busy = closeIssues.isPending || deleteIssues.isPending
  const openNumbers = selectedNumbers.filter((number) => data.some((issue) => issue.number === number && issue.state === 'open'))
  const showError = (error: unknown, fallback: string) => message.error(isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail || fallback : fallback)
  const handleClose = async () => {
    if (!openNumbers.length || busy) return
    try {
      const result = await closeIssues.mutateAsync(openNumbers)
      setSelectedNumbers([])
      message.success(`已关闭 ${result.closed} 个 Issue`)
    } catch (error) { showError(error, '关闭失败，请重试') }
  }
  const handleDelete = () => {
    const numbers = [...selectedNumbers]
    if (!numbers.length || busy) return
    modal.confirm({
      title: `删除选中的 ${numbers.length} 个 Issue？`, okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const result = await deleteIssues.mutateAsync(numbers)
          setSelectedNumbers([])
          setCurrentPage(1)
          message.success(`已删除 ${result.deleted} 个 Issue`)
        } catch (error) { showError(error, '删除失败，请重试'); throw error }
      },
    })
  }
  return <>
    {contextHolder}
    {isError && <Alert type="error" showIcon message="Issue 列表加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } />}
    <ProTable<DailyIssueItem>
      className="daily-collection-table" size="small" rowKey="number" tableLayout="fixed"
      search={false} toolBarRender={false} cardProps={false} tableAlertRender={false}
      headerTitle={<Space className="daily-cases-actions" size={8}>
        <Button color="danger" variant="outlined" icon={<DeleteOutlined />} disabled={!selectedNumbers.length || busy} loading={deleteIssues.isPending} onClick={handleDelete}>删除</Button>
        <Button color="danger" variant="solid" icon={<CheckCircleOutlined />} disabled={!openNumbers.length || busy} loading={closeIssues.isPending} onClick={() => void handleClose()}>关闭</Button>
      </Space>}
      rowSelection={{ columnWidth: 48, selectedRowKeys: selectedNumbers, preserveSelectedRowKeys: true,
        onChange: (keys) => setSelectedNumbers(keys.map(Number)), getCheckboxProps: () => ({ disabled: busy }) }}
      columns={columns} dataSource={data} loading={isLoading || isFetching || busy}
      {...dailyTableRefresh(refetch, isFetching || busy, { current: currentPage, onChange: setCurrentPage, defaultPageSize: 10, pageSizeOptions: ['10', '20', '50'], showSizeChanger: true })}
      scroll={{ x: 900 }}
    />
  </>
}

export default IssueTable
