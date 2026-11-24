import { useState } from 'react'
import { Button, Descriptions, Drawer, Spin, Tabs } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { dailyTableRefresh } from '../shared/dailyTableRefresh'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import { useDailyCollectionDetail } from '../../../hooks/useDailyPipeline'
import type { DailyCollectionDetail } from '../../../types/dailyPipeline'
import CollectionDuration from './CollectionDuration'
import CollectionIssueLink from './CollectionIssueLink'
import TriggerHistoryDetail from '../trigger/TriggerHistoryDetail'
import { collectionGithubLink, executionResult, formatCollectionTime } from './collectionDisplay'
import { runAllResultRows } from './collectionResultRows'
import type { RunAllResultRow } from './collectionResultRows'

type CollectedCase = DailyCollectionDetail['cases'][number]
const githubColumn = {
  title: '链接',
  dataIndex: 'githubUrl',
  width: 76,
  render: (_: unknown, row: { githubUrl?: string | null }) => collectionGithubLink(row.githubUrl),
}

const caseColumns: ProColumns<CollectedCase>[] = [
  { title: '用例名称', dataIndex: 'name', ellipsis: true },
  { key: 'group', title: '类型', dataIndex: 'group', width: 90, filters: ['E2E', 'UT'].map((value) => ({ text: value, value })), onFilter: (value, row) => row.group === value },
  { title: '硬件类型', dataIndex: 'hardware', width: 110 },
  { title: '执行结果', dataIndex: 'result', width: 100, render: (_, row) => executionResult(row.result) },
  githubColumn,
]

const runAllColumns: ProColumns<RunAllResultRow>[] = [
  { title: '用例名称', dataIndex: 'name', ellipsis: true },
  { title: 'vLLM 版本', dataIndex: 'vllmVersion', width: 120,
    filters: ['main', 'release'].map((value) => ({ text: value, value })),
    onFilter: (value, row) => row.vllmVersion === value },
  { title: '类型', dataIndex: 'group', width: 90,
    filters: ['E2E', 'UT', 'UT/E2E'].map((value) => ({ text: value, value })),
    onFilter: (value, row) => row.group === value },
  { title: '硬件类型', dataIndex: 'hardware', width: 110 },
  { title: '执行结果', dataIndex: 'result', width: 100, render: (_, row) => executionResult(row.result) },
  githubColumn,
]

type CollectedJob = DailyCollectionDetail['jobs'][number]
const jobColumns: ProColumns<CollectedJob>[] = [
  { title: '名称', dataIndex: 'name', ellipsis: true },
  { title: '执行结果', dataIndex: 'result', width: 120, render: (_, row) => executionResult(row.result) },
  githubColumn,
]

function CollectionDetail({ id }: { id: string | null }) {
  const [triggerLogOpen, setTriggerLogOpen] = useState(false)
  const detail = useDailyCollectionDetail(id)
  useDailyQueryError(detail, '采集详情加载失败', Boolean(id))
  const { data, isLoading, isFetching, refetch } = detail
  return <section className="daily-collection-detail">
    <Spin spinning={isLoading}>
      <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
        { key: 'workflow', label: '主流水线', children: 'schedule_daily_test.yaml' },
        { key: 'result', label: '流水线结果', children: data ? executionResult(data.result) : '—' },
        { key: 'started', label: '开始时间', children: formatCollectionTime(data?.startedAt ?? null) },
        { key: 'ended', label: '结束时间', children: formatCollectionTime(data?.endedAt ?? null) },
        { key: 'duration', label: '耗时', children: data ? <CollectionDuration run={data} /> : '—' },
        { key: 'updated', label: '最近采集', children: formatCollectionTime(data?.lastCollectedAt ?? null) },
        { key: 'github', label: '链接', children: collectionGithubLink(data?.githubUrl) },
        { key: 'issue', label: '社区 Issue', children: <CollectionIssueLink issue={data?.issue ?? null} /> },
        { key: 'trigger-log', children: <Button type="link" className="daily-collection-link daily-collection-history-link" disabled={!data?.triggerHistoryId} onClick={() => setTriggerLogOpen(true)}>触发日志</Button> },
      ]} />
      <div className="daily-collection-execution-title">执行结果：</div>
      <Tabs size="small" animated={false} items={[
        { key: 'daily', label: 'Daily 用例', children: <ProTable<CollectedCase> search={false} headerTitle={false} toolBarRender={false} cardProps={false} {...dailyTableRefresh(refetch, isFetching, { pageSize: 5, hideOnSinglePage: true, showSizeChanger: false })} size="small" loading={isFetching} rowKey="id" columns={caseColumns.filter((column) => column.key !== 'group')} dataSource={data?.cases.filter((item) => item.group === 'Daily') ?? []}
           scroll={{ x: 680 }} /> },
        { key: 'ut-e2e', label: 'run-all-tests', children: <ProTable<RunAllResultRow> search={false} headerTitle={false} toolBarRender={false} cardProps={false} {...dailyTableRefresh(refetch, isFetching, { pageSize: 5, hideOnSinglePage: true, showSizeChanger: false })} size="small" loading={isFetching} rowKey="id" columns={runAllColumns} dataSource={runAllResultRows(data)}
           scroll={{ x: 680 }} /> },
        { key: 'other', label: '其他', children: <ProTable<CollectedJob>
          search={false} headerTitle={false} toolBarRender={false} cardProps={false}
          {...dailyTableRefresh(refetch, isFetching, { pageSize: 10, hideOnSinglePage: true, showSizeChanger: false })}
          size="small" loading={isFetching} rowKey="id" columns={jobColumns} dataSource={data?.jobs ?? []}
          scroll={{ x: 680 }} /> },
      ]} />
      <Drawer rootClassName="daily-trigger-history" title="触发日志" placement="right" open={triggerLogOpen} onClose={() => setTriggerLogOpen(false)} width={760} styles={{ wrapper: { maxWidth: '100vw' } }} destroyOnHidden>
        {triggerLogOpen && data?.triggerHistoryId && <TriggerHistoryDetail key={data.triggerHistoryId} id={data.triggerHistoryId} />}
      </Drawer>
    </Spin>
  </section>
}

export default CollectionDetail
