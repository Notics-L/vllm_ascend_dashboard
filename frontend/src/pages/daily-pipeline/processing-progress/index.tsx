import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useDailyFailedCase } from '../../../hooks/useDailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import FailureInformation from './FailureInformation'
import ProcessingFlow from './ProcessingFlow'

function DailyPipelineProcessingProgress() {
  const navigate = useNavigate()
  const location = useLocation()
  const requestedReturn = (location.state as { returnTo?: unknown } | null)?.returnTo
  const returnTo = typeof requestedReturn === 'string' && /^\/daily-pipeline\/issue-tracking\/issues\/[1-9]\d*\/progress$/.test(requestedReturn)
    ? requestedReturn : '/daily-pipeline/issue-tracking'
  const { failureId } = useParams<{ failureId: string }>()
  const { data, isLoading, isFetching, isError, refetch } = useDailyFailedCase(failureId)
  return <DailyCasePage title="处理进展" onBack={() => navigate(returnTo, { replace: true })}>
    {!failureId ? <Alert type="error" showIcon message="失败用例不存在" /> : isLoading ? <Spin size="small" /> : isError ?
      <Alert type="error" showIcon message="失败信息加载失败" action={
        <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
      } /> : data && <>
        <FailureInformation failure={data} />
        <ProcessingFlow flow={data.processingFlow} />
      </>}
    <div className="daily-cases-actions" style={{ marginTop: 16 }}>
      <Button type="primary" icon={<ReloadOutlined />} loading={isFetching} disabled={!failureId} onClick={() => void refetch()}>刷新</Button>
    </div>
  </DailyCasePage>
}

export default DailyPipelineProcessingProgress
