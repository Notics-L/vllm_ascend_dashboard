import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Badge, Button, Modal, Popconfirm, Select, Space, Tabs, message } from 'antd'
import { CheckOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyFailedCases, useDailyVersionFlowMutations } from '../../../hooks/useDailyPipeline'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import type { DailyFailedCase } from '../../../types/dailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import IssueTable from './IssueTable'
import CollectionIssueLink from '../collection/CollectionIssueLink'
import CollectionRunLink from '../collection/CollectionRunLink'
import { collectionGithubLink, formatCollectionTime } from '../collection/collectionDisplay'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'
import { failedCaseDisplayName, failedCaseIssueValue, failedCaseProcessingResult, filterFailedCases } from './failedCaseFilters'
import type { FailedCaseFilters } from './failedCaseFilters'
import '../collection/Collection.css'
import '../shared/DailyStableTabs.css'

const processingResultColors = { 已结束: '#8c8c8c', 成功: '#389e0d', 失败: '#cf1322', 执行中: '#faad14' }
const filterOptions = (values: string[]) => [...new Set(values)].map((value) => ({ label: value, value }))
const selectField = (values: string[], placeholder: string) => ({
  mode: 'multiple' as const, options: filterOptions(values), placeholder, allowClear: true, style: { width: '100%' },
})

function buildColumns(
  data: DailyFailedCase[],
  onUnshare?: (id: string) => Promise<void>,
  unsharingId?: string,
): ProColumns<DailyFailedCase>[] {
  return [
    { title: 'Run ID', dataIndex: 'runId', width: 125, valueType: 'text', order: 7,
      fieldProps: { placeholder: '请输入 Run ID', allowClear: true, style: { width: '100%' } },
      render: (_, row) => <CollectionRunLink runId={row.runId} /> },
    { title: '用例名称', dataIndex: 'name', width: 360, ellipsis: true, valueType: 'text', order: 8,
      renderText: (_, row) => failedCaseDisplayName(row),
      formItemProps: { className: 'daily-cases-filter-name' },
      fieldProps: { placeholder: '请输入用例名称', allowClear: true, style: { width: '100%' } } },
    { title: '硬件类型', dataIndex: 'hardware', width: 110, valueType: 'select', order: 6,
      formItemProps: { className: 'daily-cases-filter-hardware' },
      fieldProps: selectField(data.map((row) => row.hardware), '全部硬件') },
    { title: '类型', dataIndex: 'group', width: 90, valueType: 'select', order: 5,
      fieldProps: { mode: 'multiple', placeholder: '全部类型', allowClear: true, style: { width: '100%' },
        options: ['Daily', 'E2E', 'UT', 'UT/E2E'].map((value) => ({ label: value === 'Daily' ? 'Daily 用例' : value, value })) },
      render: (_, row) => row.group === 'Daily' ? 'Daily 用例' : row.group },
    { title: '问题分类', dataIndex: 'problemCategory', width: 120, valueType: 'select', order: 4,
      fieldProps: selectField(data.map((row) => row.problemCategory ?? '未分类'), '全部分类'),
      render: (_, row) => row.problemCategory ?? '—' },
    { title: '开始时间', dataIndex: 'collectedAt', width: 180, valueType: 'dateTimeRange', order: 1,
      fieldProps: { placeholder: ['开始时间', '结束时间'], style: { width: '100%' } },
      render: (_, row) => formatCollectionTime(row.collectedAt) },
    { title: 'Issue', dataIndex: 'issue', width: 150, valueType: 'select', order: 2,
      fieldProps: selectField(data.map(failedCaseIssueValue), '全部 Issue'),
      render: (_, row) => <CollectionIssueLink issue={row.issue} /> },
    { title: '处理结果', dataIndex: 'processingResult', width: 110, valueType: 'select', order: 3,
      fieldProps: selectField(['成功', '失败', '执行中', '已结束'], '全部结果'),
      render: (_, row) => {
        const result = failedCaseProcessingResult(row)
        const color = processingResultColors[result]
        return <Badge color={color} text={<span style={{ color }}>{result}</span>} />
      } },
    { title: '链接', dataIndex: 'githubUrl', width: 76, search: false,
      render: (_, row) => collectionGithubLink(row.githubUrl) },
    { title: '处理进展', dataIndex: 'processingProgress', key: 'processingProgress', width: 188, valueType: 'select', order: 3,
      fieldProps: selectField(data.map((row) => row.processingProgress), '全部进展'),
      render: (_, row) => {
        const shared = (row.processFlowSize ?? row.versionFlowSize ?? 1) > 1
        const canUnshare = onUnshare && shared && row.issue?.state === 'open'
          && ['待处理', '处理中', '未解决'].includes(row.processingProgress)
        return <Space size="small">
          <Link className="daily-collection-history-link" to={'/daily-pipeline/issue-tracking/cases/' + encodeURIComponent(row.id) + '/progress'}>
            {shared ? '查看共用流程' : '查看'}
          </Link>
          {canUnshare && <Popconfirm title="取消该用例的共用流程？"
            description="该用例将从问题分类重新开始，其余用例继续原流程。"
            okText="确认" cancelText="取消" onConfirm={() => onUnshare(row.id)}>
            <Button type="link" size="small" danger className="daily-collection-link"
              disabled={Boolean(unsharingId)} loading={unsharingId === row.id}>取消共用</Button>
          </Popconfirm>}
        </Space>
      } },
  ]
}

function DailyPipelineIssueTracking() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') === 'issues' ? 'issues' : 'failed-cases'
  const showFailures = activeTab === 'failed-cases'
  const failedCases = useDailyFailedCases({ enabled: showFailures, staleTime: 0 })
  const opening = useDailyTabOpening(showFailures)
  const { data = [], isFetching, refetch } = failedCases
  const { data: user } = useCurrentUser()
  const { share, unshare } = useDailyVersionFlowMutations()
  const canShare = user?.role === 'admin' || user?.role === 'super_admin'
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [shareOpen, setShareOpen] = useState(false)
  const [target, setTarget] = useState<string>()
  const selectedCases = data.filter((row) => selectedIds.includes(row.id))
  const confirmShare = async () => {
    if (!target || selectedIds.length < 2 || share.isPending) return
    try {
      await share.mutateAsync({ failureIds: selectedIds, targetFailureId: target })
      void message.success('已共用处理流程')
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : null
      void message.error(typeof detail === 'string' ? detail : '共用流程失败，请稍后重试')
    } finally {
      setShareOpen(false)
      setSelectedIds([])
      setTarget(undefined)
    }
  }
  const cancelSharing = async (id: string) => {
    try {
      await unshare.mutateAsync(id)
      setSelectedIds((current) => current.filter((item) => item !== id))
      void message.success('已取消共用')
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : null
      void message.error(typeof detail === 'string' ? detail : '取消共用失败，请稍后重试')
    }
  }
  useDailyQueryError(failedCases, '失败用例加载失败', showFailures)
  const [filters, setFilters] = useState<FailedCaseFilters>({})
  const [searchCollapsed, setSearchCollapsed] = useState(true)
  const [currentPage, setCurrentPage] = useState(1)
  const filteredCases = useMemo(() => filterFailedCases(data, filters), [data, filters])
  return <DailyCasePage>
    <Tabs className="daily-stable-tabs" size="small" animated={false} activeKey={activeTab}
      onChange={(key) => setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('tab', key)
        return next
      }, { replace: true })} items={[
      { key: 'failed-cases', label: '失败用例', forceRender: true, children: <>
    <ProTable<DailyFailedCase, FailedCaseFilters>
      className="daily-cases-pro-table" tableClassName="daily-cases-table daily-collection-table" size="small" rowKey="id" tableLayout="fixed"
      headerTitle={false} toolBarRender={false} cardProps={false}
      tableAlertRender={false}
      tableExtraRender={canShare ? () => <div className="daily-cases-actions">
        <Button disabled={selectedIds.length < 2 || share.isPending} onClick={() => {
          setTarget(undefined)
          setShareOpen(true)
        }}>
          共用处理流程
        </Button>
      </div> : undefined}
      rowSelection={canShare ? {
        preserveSelectedRowKeys: true,
        selectedRowKeys: selectedIds,
        onChange: (keys) => setSelectedIds(keys.map(String)),
      } : undefined}
      search={{
        labelWidth: 'auto', collapsed: searchCollapsed, onCollapse: setSearchCollapsed,
        submitterColSpanProps: { span: 24, className: 'daily-cases-filter-actions' },
        span: { xs: 24, sm: 12, md: 8, lg: 6, xl: 4, xxl: 4 },
        searchText: '查询', resetText: '重置',
      }}
      onSubmit={(values) => { setFilters(values); setCurrentPage(1) }}
      onReset={() => { setFilters({}); setCurrentPage(1) }}
      columns={buildColumns(data, canShare ? cancelSharing : undefined, unshare.isPending ? unshare.variables : undefined)} dataSource={filteredCases}
      {...dailyTableRefresh(refetch, showFailures && (opening || isFetching), { current: currentPage, onChange: setCurrentPage, defaultPageSize: 10, pageSizeOptions: ['10', '20', '50'], showSizeChanger: true })}
      scroll={{ x: 1244 }}
    />
    <Modal title="共用处理流程" open={shareOpen} okText="确认共用" cancelText="取消"
      okButtonProps={{ disabled: !target || selectedIds.length < 2, size: 'small', icon: <CheckOutlined /> }}
      cancelButtonProps={{ size: 'small', disabled: share.isPending }} confirmLoading={share.isPending}
      footer={(_, { OkBtn, CancelBtn }) => <Space className="daily-cases-actions" size={8}><CancelBtn /><OkBtn /></Space>}
      onOk={confirmShare} onCancel={() => { if (!share.isPending) setShareOpen(false) }}>
      <Select aria-label="保留的流程" placeholder="选择要保留的用例流程"
        value={target} onChange={setTarget} style={{ width: '100%' }}
        options={selectedCases.map((row) => ({
          value: row.id, label: `${failedCaseDisplayName(row)} · ${row.problemCategory ?? '未分类'} · #${row.id}`,
        }))} />
    </Modal>
      </> },
      { key: 'issues', label: 'issue', forceRender: true, children: <IssueTable active={activeTab === 'issues'} /> },
    ]} />
  </DailyCasePage>
}

export default DailyPipelineIssueTracking
