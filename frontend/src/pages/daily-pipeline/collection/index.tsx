import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Modal, message } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyCollectionRuns, useDeleteDailyCollectionRuns } from '../../../hooks/useDailyPipeline'
import type { DailyCollectionRun } from '../../../types/dailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import CollectionDuration from './CollectionDuration'
import CollectionIssueLink from './CollectionIssueLink'
import DailyTriggerHistory from '../trigger/DailyTriggerHistory'
import { collectionGithubLink, executionResult, formatCollectionTime } from './collectionDisplay'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'
import './Collection.css'

function DailyPipelineCollection() {
  const runs = useDailyCollectionRuns()
  const opening = useDailyTabOpening(true)
  const loading = opening || runs.isFetching
  useDailyQueryError(runs, '采集记录加载失败')
  const [historyId, setHistoryId] = useState<string | null>(null)
  const [selectedRunIds, setSelectedRunIds] = useState<number[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const deleteRuns = useDeleteDailyCollectionRuns()
  const [modal, contextHolder] = Modal.useModal()
  const handleDelete = () => {
    const runIds = [...selectedRunIds]
    if (!runIds.length || loading || deleteRuns.isPending) return
    modal.confirm({
      title: `删除选中的 ${runIds.length} 条采集记录？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await deleteRuns.mutateAsync(runIds)
          setSelectedRunIds([])
          setCurrentPage(1)
          message.success(`已删除 ${result.deleted} 条记录`)
        } catch (error) {
          message.error(isAxiosError<{ detail?: string }>(error)
            ? error.response?.data?.detail || '删除失败，请重试' : '删除失败，请重试')
          throw error
        }
      },
    })
  }

  const columns: ProColumns<DailyCollectionRun>[] = [
    { title: 'Run ID', key: 'runId', dataIndex: 'runId', width: 125, search: false },
    { title: '流水线结果', dataIndex: 'result', width: 115, filters: ['执行中', '成功', '失败', '已取消'].map((value) => ({ text: value, value })), onFilter: (value, row) => row.result === value, valueType: 'select', valueEnum: { 执行中: '执行中', 成功: '成功', 失败: '失败', 已取消: '已取消' }, render: (_, row) => executionResult(row.result) },
    { title: '开始时间', dataIndex: 'startedAt', width: 180, search: false, render: (_, row) => formatCollectionTime(row.startedAt) },
    { title: '结束时间', dataIndex: 'endedAt', width: 180, search: false, render: (_, row) => formatCollectionTime(row.endedAt) },
    { title: '耗时', key: 'duration', width: 120, search: false, render: (_, row) => <CollectionDuration run={row} /> },
    { title: 'Issue', dataIndex: 'issue', width: 150, search: false, render: (_, row) => <CollectionIssueLink issue={row.issue} /> },
    { title: '关联', key: 'triggerHistory', width: 120, search: false, render: (_, row) =>
      <Button type="link" className="daily-collection-link daily-collection-history-link" onClick={() => setHistoryId(row.triggerHistoryId)}>触发日志</Button> },
    { title: '链接', dataIndex: 'githubUrl', width: 76, search: false, render: (_, row) => collectionGithubLink(row.githubUrl) },
    { title: '操作', key: 'actions', width: 85, search: false, render: (_, row) =>
      <Link className="daily-collection-link" to={'/daily-pipeline/collection/runs/' + row.runId} state={{ returnTo: '/daily-pipeline/collection' }}>详情</Link> },
  ]
  return <DailyCasePage title="流水线采集">
    {contextHolder}
    <div className="daily-collection-page">
      <ProTable<DailyCollectionRun> className="daily-collection-table" size="small" rowKey="runId" columns={columns}
          dataSource={runs.data ?? []}
          cardProps={false}
          headerTitle={<div className="daily-cases-actions"><Button danger size="small" icon={<DeleteOutlined />} onClick={handleDelete} disabled={!selectedRunIds.length || deleteRuns.isPending} loading={deleteRuns.isPending}>删除</Button></div>}
          {...dailyTableRefresh(runs.refetch, loading || deleteRuns.isPending, { current: currentPage, onChange: setCurrentPage, defaultPageSize: 5, pageSizeOptions: ['5', '10', '20'], showSizeChanger: true })}
          tableAlertRender={false}
          rowSelection={{
            columnWidth: 48,
            selectedRowKeys: selectedRunIds,
            preserveSelectedRowKeys: true,
            onChange: (keys) => setSelectedRunIds(keys.map(Number)),
            getCheckboxProps: () => ({ disabled: deleteRuns.isPending }),
          }}
          search={false} scroll={{ x: 1199 }}
           />
    </div>
    <DailyTriggerHistory open={Boolean(historyId)} focusId={historyId} onClose={() => setHistoryId(null)} />
  </DailyCasePage>
}

export default DailyPipelineCollection
