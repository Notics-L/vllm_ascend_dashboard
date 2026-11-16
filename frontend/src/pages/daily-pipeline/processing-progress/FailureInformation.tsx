import { Descriptions, Typography } from 'antd'
import type { DailyFailedCase } from '../../../types/dailyPipeline'
import CollectionIssueLink from '../collection/CollectionIssueLink'
import CollectionRunLink from '../collection/CollectionRunLink'
import { executionResult, formatCollectionTime } from '../collection/collectionDisplay'
import './ProcessingProgress.css'

function FailureInformation({ failure }: { failure: DailyFailedCase }) {
  return <section className="daily-failure-information" aria-label="失败信息">
    <Typography.Text className="daily-failure-section-title">失败信息</Typography.Text>
    <Descriptions size="small" column={{ xs: 1, sm: 2 }} items={[
      { key: 'name', label: '用例名称', span: 2, children: failure.name },
      { key: 'hardware', label: '硬件类型', children: failure.hardware },
      { key: 'type', label: '类型', children: failure.group === 'Daily' ? 'Daily 用例' : failure.group },
      ...(failure.vllmVersion ? [{ key: 'version', label: 'vLLM 版本', children: failure.vllmVersion }] : []),
      { key: 'result', label: '执行结果', children: executionResult(failure.result) },
      { key: 'collected', label: '采集时间', children: formatCollectionTime(failure.collectedAt) },
      { key: 'run', label: 'Run ID', children: <CollectionRunLink runId={failure.runId} /> },
      { key: 'issue', label: 'Issue', children: <CollectionIssueLink issue={failure.issue} /> },
      { key: 'category', label: '问题分类', children: failure.problemCategory ?? '—' },
    ]} />
  </section>
}

export default FailureInformation
