import { Alert, Button, Drawer, Spin } from 'antd'
import { useDailyFailedCase } from '../../../hooks/useDailyPipeline'
import FailureInformation from '../processing-progress/FailureInformation'
import ProcessingFlow from '../processing-progress/ProcessingFlow'

function IssueCaseProgressDrawer({ failureId, onClose }: { failureId: string | null; onClose: () => void }) {
  const { data, isLoading, isFetching, isError, refetch } = useDailyFailedCase(failureId ?? undefined)
  return <Drawer rootClassName="daily-collection-run-drawer" title="处理进展" placement="right" width={960}
    styles={{ wrapper: { maxWidth: '100vw' } }} open={Boolean(failureId)} onClose={onClose} destroyOnHidden>
    {isLoading ? <Spin size="small" /> : isError ? <Alert type="error" showIcon message="处理进展加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } /> : data && <><FailureInformation failure={data} /><ProcessingFlow flow={data.processingFlow} /></>}
  </Drawer>
}

export default IssueCaseProgressDrawer
