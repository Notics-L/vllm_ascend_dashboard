import { Alert, Button, Drawer, Spin } from 'antd'
import { useDailyFailedCase } from '../../../hooks/useDailyPipeline'
import FailureInformation from '../processing-progress/FailureInformation'
import ProcessingFlow from '../processing-progress/ProcessingFlow'
import { useDailyQueryError } from '../shared/useDailyQueryError'

function IssueCaseProgressDrawer({ failureId, onClose }: { failureId: string | null; onClose: () => void }) {
  const query = useDailyFailedCase(failureId ?? undefined)
  const { data, isLoading, isFetching, isError, refetch } = query
  useDailyQueryError(query, "处理进展刷新失败", Boolean(data) && Boolean(failureId))
  return <Drawer rootClassName="daily-collection-run-drawer" title="处理进展" placement="right" width={960}
    styles={{ wrapper: { maxWidth: '100vw' } }} open={Boolean(failureId)} onClose={onClose} destroyOnHidden>
    <Spin spinning={isFetching} wrapperClassName="daily-processing-loading">
      {data ? <><FailureInformation failure={data} /><ProcessingFlow flow={data.processingFlow} allowManual loading={isFetching} /></> : isError ? <Alert type="error" showIcon message="处理进展加载失败" action={
        <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
      } /> : isLoading ? <div className="daily-processing-loading-placeholder" /> : null}
    </Spin>
  </Drawer>
}

export default IssueCaseProgressDrawer
