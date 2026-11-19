import { useNavigate, useParams } from 'react-router-dom'
import { useIsFetching, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Spin, message } from 'antd'
import { CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useDailyIssues, useDailyIssueMutations } from '../../../hooks/useDailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import IssueInformation from './IssueInformation'
import IssueDependencyGraph from './IssueDependencyGraph'

function IssueProgressPage() {
  const { issueNumber: issueParam } = useParams<{ issueNumber: string }>()
  const navigate = useNavigate()
  const client = useQueryClient()
  const issueNumber = Number(issueParam)
  const validNumber = Number.isSafeInteger(issueNumber) && issueNumber > 0
  const { data = [], isLoading, isFetching, isError, refetch } = useDailyIssues()
  const dependenciesFetching = useIsFetching({ queryKey: ['daily-pipeline', 'failed-cases'] }) > 0
  const issue = data.find((item) => item.number === issueNumber)
  const { closeIssues } = useDailyIssueMutations()
  const refresh = () => Promise.all([
    refetch(), client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-cases'] }),
    client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-case-detail'] }),
  ])
  const handleClose = async () => {
    if (!issue || issue.state === 'closed' || closeIssues.isPending) return
    try {
      await closeIssues.mutateAsync([issue.number])
      message.success('Issue 已关闭')
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail || '关闭失败，请重试' : '关闭失败，请重试')
    }
  }
  return <DailyCasePage title="处理进展" onBack={() => navigate('/daily-pipeline/issue-tracking?tab=issues', { replace: true })}>
    {!validNumber ? <Alert type="error" showIcon message="Issue 不存在" /> : isLoading ? <Spin size="small" /> : isError ?
      <Alert type="error" showIcon message="Issue 信息加载失败" action={<Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>} /> :
      !issue ? <Alert type="error" showIcon message="Issue 不存在" /> : <>
        <IssueInformation issue={issue} actions={<Button color="danger" variant="solid" icon={<CheckCircleOutlined />}
          loading={closeIssues.isPending} disabled={issue.state === 'closed'} onClick={() => void handleClose()}>关闭</Button>} />
        <IssueDependencyGraph issue={issue} />
      </>}
    <div className="daily-cases-actions" style={{ marginTop: 16 }}>
      <Button type="primary" icon={<ReloadOutlined />} loading={isFetching || dependenciesFetching}
        disabled={closeIssues.isPending || !validNumber} onClick={() => void refresh()}>刷新</Button>
    </div>
  </DailyCasePage>
}

export default IssueProgressPage
