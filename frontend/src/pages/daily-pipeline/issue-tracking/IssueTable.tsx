import { useState } from 'react'
import { Button, Modal, Space, Tag, message } from 'antd'
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
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'

interface IssueFilters {
  number?: string
  title?: string
  state?: DailyIssueItem['state'][]
  runId?: string
}

const columns: ProColumns<DailyIssueItem>[] = [
  { title: '编号', dataIndex: 'number', width: 110, valueType: 'text',
    fieldProps: { placeholder: '请输入 Issue 编号', allowClear: true },
    render: (_, row) => <CollectionIssueLink issue={row} showState={false} /> },
  { title: '标题', dataIndex: 'title', width: 360, ellipsis: true, valueType: 'text',
    formItemProps: { className: 'daily-cases-filter-name' },
    fieldProps: { placeholder: '请输入标题关键词', allowClear: true } },
  { title: '状态', dataIndex: 'state', width: 100, valueType: 'select',
    fieldProps: { mode: 'multiple', placeholder: '全部状态', allowClear: true,
      options: [{ label: 'open', value: 'open' }, { label: 'close', value: 'closed' }] },
    render: (_, row) => <Tag color={row.state === 'open' ? 'blue' : 'default'}>{row.state === 'open' ? 'open' : 'close'}</Tag> },
  { title: 'Run ID', dataIndex: 'runId', width: 125, valueType: 'text',
    fieldProps: { placeholder: '请输入 Run ID', allowClear: true },
    render: (_, row) => row.runId ? <CollectionRunLink runId={row.runId} /> : '—' },
  { title: '处理进展', key: 'processingProgress', width: 130, search: false, render: (_, row) => <IssueProgressLink issueNumber={row.number} /> },
]

function IssueTable({ active }: { active: boolean }) {
  const issues = useDailyIssues({ enabled: active, staleTime: 0 })
  const { data = [], isFetching, refetch } = issues
  const opening = useDailyTabOpening(active)
  const loading = active && (opening || isFetching)
  useDailyQueryError(issues, 'Issue 列表加载失败', active)
  const [selectedNumbers, setSelectedNumbers] = useState<number[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const [filters, setFilters] = useState<IssueFilters>({})
  const [searchCollapsed, setSearchCollapsed] = useState(true)
  const applyFilters = (values: IssueFilters) => {
    setFilters(values)
    setCurrentPage(1)
    setSelectedNumbers([])
  }
  const number = filters.number?.trim().replace(/^#/, '')
  const title = filters.title?.trim().toLowerCase()
  const runId = filters.runId?.trim()
  const filteredIssues = data.filter((row) =>
    (!number || String(row.number).includes(number)) &&
    (!title || row.title.toLowerCase().includes(title)) &&
    (!filters.state?.length || filters.state.includes(row.state)) &&
    (!runId || (row.runId !== null && String(row.runId).includes(runId))),
  )
  const { closeIssues, deleteIssues } = useDailyIssueMutations()
  const [modal, contextHolder] = Modal.useModal()
  const busy = closeIssues.isPending || deleteIssues.isPending
  const openNumbers = selectedNumbers.filter((number) => data.some((issue) => issue.number === number && issue.state === 'open'))
  const showError = (error: unknown, fallback: string) => message.error(isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail || fallback : fallback)
  const handleClose = async () => {
    if (!openNumbers.length || loading || busy) return
    try {
      const result = await closeIssues.mutateAsync(openNumbers)
      setSelectedNumbers([])
      message.success(`已关闭 ${result.closed} 个 Issue`)
    } catch (error) { showError(error, '关闭失败，请重试') }
  }
  const handleDelete = () => {
    const numbers = [...selectedNumbers]
    if (!numbers.length || loading || busy) return
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
    <ProTable<DailyIssueItem, IssueFilters>
      className="daily-cases-pro-table daily-collection-table" tableClassName="daily-cases-table"
      size="small" rowKey="number" tableLayout="fixed"
      toolBarRender={false} cardProps={false} tableAlertRender={false}
      search={{
        labelWidth: 'auto', collapsed: searchCollapsed, onCollapse: setSearchCollapsed,
        submitterColSpanProps: { span: 24, className: 'daily-cases-filter-actions' },
        span: { xs: 24, sm: 12, md: 8, lg: 6, xl: 4, xxl: 4 },
        searchText: '查询', resetText: '重置',
      }}
      onSubmit={applyFilters}
      onReset={() => applyFilters({})}
      headerTitle={<Space className="daily-cases-actions" size={8}>
        <Button color="danger" variant="outlined" icon={<DeleteOutlined />} disabled={!selectedNumbers.length || busy} loading={deleteIssues.isPending} onClick={handleDelete}>删除</Button>
        <Button color="danger" variant="solid" icon={<CheckCircleOutlined />} disabled={!openNumbers.length || busy} loading={closeIssues.isPending} onClick={() => void handleClose()}>关闭</Button>
      </Space>}
      rowSelection={{ columnWidth: 48, selectedRowKeys: selectedNumbers, preserveSelectedRowKeys: true,
        onChange: (keys) => setSelectedNumbers(keys.map(Number)), getCheckboxProps: () => ({ disabled: busy }) }}
      columns={columns} dataSource={filteredIssues}
      {...dailyTableRefresh(refetch, loading || busy, { current: currentPage, onChange: setCurrentPage, defaultPageSize: 10, pageSizeOptions: ['10', '20', '50'], showSizeChanger: true })}
      scroll={{ x: 900 }}
    />
  </>
}

export default IssueTable
