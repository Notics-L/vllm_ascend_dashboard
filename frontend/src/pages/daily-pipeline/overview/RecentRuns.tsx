import { Table, type TableColumnsType } from 'antd'
import { Link } from 'react-router-dom'
import type { DailyOverviewData } from '../../../types/dailyPipeline'
import CollectionDuration from '../collection/CollectionDuration'
import { executionResult, formatCollectionTime } from '../collection/collectionDisplay'

type RecentRun = DailyOverviewData['recentRuns'][number]

const columns: TableColumnsType<RecentRun> = [
  {
    title: 'Run ID',
    dataIndex: 'id',
    render: (id: RecentRun['id']) => <Link to={`/daily-pipeline/collection/runs/${id}`} state={{ returnTo: '/daily-pipeline/overview' }}>{id}</Link>,
  },
  { title: '开始时间', dataIndex: 'startedAt', render: formatCollectionTime },
  { title: '结果', dataIndex: 'result', render: executionResult },
  { title: '耗时', render: (_, run) => <CollectionDuration run={run} /> },
  { title: 'Issue', dataIndex: 'issue', render: (issue: RecentRun['issue']) => issue ? `#${issue.number}` : '—' },
]

export default function RecentRuns({ runs }: { runs: RecentRun[] }) {
  return <Table className="daily-overview-recent-runs" rowKey="id" size="small" columns={columns} dataSource={runs}
    pagination={false} scroll={{ x: 490 }} locale={{ emptyText: '暂无执行记录' }} />
}
