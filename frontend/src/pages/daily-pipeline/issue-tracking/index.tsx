import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, Badge, Button, Tabs } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyFailedCases } from '../../../hooks/useDailyPipeline'
import type { DailyFailedCase } from '../../../types/dailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import IssueTable from './IssueTable'
import CollectionIssueLink from '../collection/CollectionIssueLink'
import CollectionRunLink from '../collection/CollectionRunLink'
import { formatCollectionTime } from '../collection/collectionDisplay'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { failedCaseIssueValue, failedCaseProcessingResult, filterFailedCases } from './failedCaseFilters'
import type { FailedCaseFilters } from './failedCaseFilters'
import '../collection/Collection.css'

const processingResultColors = { 成功: '#389e0d', 失败: '#cf1322', 执行中: '#faad14' }
const filterOptions = (values: string[]) => [...new Set(values)].map((value) => ({ label: value, value }))
const selectField = (values: string[], placeholder: string) => ({
  mode: 'multiple' as const, options: filterOptions(values), placeholder, allowClear: true, style: { width: '100%' },
})

function buildColumns(data: DailyFailedCase[]): ProColumns<DailyFailedCase>[] {
  return [
    { title: 'Run ID', dataIndex: 'runId', width: 125, valueType: 'text', order: 7,
      fieldProps: { placeholder: '请输入 Run ID', allowClear: true, style: { width: '100%' } },
      render: (_, row) => <CollectionRunLink runId={row.runId} /> },
    { title: '用例名称', dataIndex: 'name', width: 360, ellipsis: true, valueType: 'text', order: 8,
      formItemProps: { className: 'daily-cases-filter-name' },
      fieldProps: { placeholder: '请输入用例名称', allowClear: true, style: { width: '100%' } } },
    { title: '硬件类型', dataIndex: 'hardware', width: 110, valueType: 'select', order: 6,
      formItemProps: { className: 'daily-cases-filter-hardware' },
      fieldProps: selectField(data.map((row) => row.hardware), '全部硬件') },
    { title: '类型', dataIndex: 'group', width: 90, valueType: 'select', order: 5,
      fieldProps: { mode: 'multiple', placeholder: '全部类型', allowClear: true, style: { width: '100%' },
        options: ['Daily', 'E2E', 'UT'].map((value) => ({ label: value === 'Daily' ? 'Daily 用例' : value, value })) },
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
      fieldProps: selectField(['成功', '失败', '执行中'], '全部结果'),
      render: (_, row) => {
        const result = failedCaseProcessingResult(row)
        const color = processingResultColors[result]
        return <Badge color={color} text={<span style={{ color }}>{result}</span>} />
      } },
    { title: '处理进展', dataIndex: 'processingProgress', key: 'processingProgress', width: 120, valueType: 'select', order: 3,
      fieldProps: selectField(data.map((row) => row.processingProgress), '全部进展'),
      render: (_, row) => <Link className="daily-collection-history-link" to={'/daily-pipeline/issue-tracking/cases/' + encodeURIComponent(row.id) + '/progress'}>查看</Link> },
  ]
}

function DailyPipelineIssueTracking() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') === 'issues' ? 'issues' : 'failed-cases'
  const { data = [], isLoading, isFetching, isError, refetch } = useDailyFailedCases()
  const [filters, setFilters] = useState<FailedCaseFilters>({})
  const [searchCollapsed, setSearchCollapsed] = useState(true)
  const [currentPage, setCurrentPage] = useState(1)
  const filteredCases = useMemo(() => filterFailedCases(data, filters), [data, filters])
  return <DailyCasePage>
    <Tabs size="small" animated={false} activeKey={activeTab}
      onChange={(key) => setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('tab', key)
        return next
      }, { replace: true })} items={[
      { key: 'failed-cases', label: '失败用例', children: <>
    {isError && <Alert type="error" showIcon message="失败用例加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } />}
    <ProTable<DailyFailedCase, FailedCaseFilters>
      className="daily-cases-pro-table" tableClassName="daily-cases-table daily-collection-table" size="small" rowKey="id" tableLayout="fixed"
      headerTitle={false} toolBarRender={false} cardProps={false}
      search={{
        labelWidth: 'auto', collapsed: searchCollapsed, onCollapse: setSearchCollapsed,
        submitterColSpanProps: { span: 24, className: 'daily-cases-filter-actions' },
        span: { xs: 24, sm: 12, md: 8, lg: 6, xl: 4, xxl: 4 },
        searchText: '查询', resetText: '重置',
      }}
      onSubmit={(values) => { setFilters(values); setCurrentPage(1) }}
      onReset={() => { setFilters({}); setCurrentPage(1) }}
      columns={buildColumns(data)} dataSource={filteredCases} loading={isLoading || isFetching}
      {...dailyTableRefresh(refetch, isFetching, { current: currentPage, onChange: setCurrentPage, defaultPageSize: 10, pageSizeOptions: ['10', '20', '50'], showSizeChanger: true })}
      scroll={{ x: 1100 }}
    />
      </> },
      { key: 'issues', label: 'issue', children: <IssueTable /> },
    ]} />
  </DailyCasePage>
}

export default DailyPipelineIssueTracking
