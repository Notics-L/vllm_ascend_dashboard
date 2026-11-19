import type { ReactNode } from 'react'
import { Descriptions, Tag, Typography } from 'antd'
import type { DailyIssueItem } from '../../../types/dailyPipeline'
import CollectionIssueLink from '../collection/CollectionIssueLink'
import CollectionRunLink from '../collection/CollectionRunLink'
import '../processing-progress/ProcessingProgress.css'

function IssueInformation({ issue, actions }: { issue: DailyIssueItem; actions?: ReactNode }) {
  return <section className="daily-failure-information" aria-label="Issue 信息">
    <Typography.Text className="daily-failure-section-title">Issue 信息</Typography.Text>
    <Descriptions size="small" column={1}
      styles={{ label: { whiteSpace: 'nowrap' }, content: { overflowWrap: 'anywhere' } }} items={[
      { key: 'title', label: '标题', children: issue.title },
      { key: 'number', label: '编号', children: <CollectionIssueLink issue={issue} showState={false} /> },
      { key: 'state', label: '状态', children: <Tag color={issue.state === 'open' ? 'blue' : 'default'}>{issue.state === 'open' ? 'open' : 'close'}</Tag> },
      { key: 'run', label: 'Run ID', children: issue.runId ? <CollectionRunLink runId={issue.runId} /> : '—' },
    ]} />
    {actions && <div className="daily-cases-actions" style={{ marginTop: 12 }}>{actions}</div>}
  </section>
}

export default IssueInformation
