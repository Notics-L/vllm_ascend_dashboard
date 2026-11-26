import { RedoOutlined } from '@ant-design/icons'
import { Button, Popconfirm, Space, message } from 'antd'
import { isAxiosError } from 'axios'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import { useRerunDailyFailedCase } from '../../../hooks/useDailyPipeline'
import type { DailyFailedCaseDetail } from '../../../types/dailyPipeline'

export default function CaseRerunAction({ rerun }: {
  rerun: NonNullable<DailyFailedCaseDetail['processingFlow']['caseRerun']>
}) {
  const { data: user } = useCurrentUser()
  const mutation = useRerunDailyFailedCase()
  if (user?.role !== 'admin' && user?.role !== 'super_admin') return null

  const confirm = async () => {
    try {
      const result = await mutation.mutateAsync(rerun.failureId)
      if (result.collectionQueued) void message.success('GitHub 已受理重跑，结果将自动采集')
      else void message.warning('GitHub 已受理重跑，采集服务暂未响应，可在流水线采集页重新采集')
    } catch (error) {
      const detail = isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail : null
      void message.error(typeof detail === 'string' ? detail : '用例重跑失败，请稍后重试')
    }
  }

  return <div className="daily-processing-rerun-action daily-cases-actions">
    {rerun.pending ? <Space>
      <Button size="small" disabled icon={<RedoOutlined />}>重跑结果采集中</Button>
      {rerun.runUrl && <a href={rerun.runUrl} target="_blank" rel="noopener noreferrer">查看 GitHub Run</a>}
    </Space> : <Popconfirm title="重跑这个用例？"
      description="GitHub 将重跑当前用例对应的 Job，也可能重跑依赖它的下游任务。"
      okText="重跑" cancelText="取消" onConfirm={confirm}>
      <Button type="primary" size="small"
        icon={<RedoOutlined />} loading={mutation.isPending}>用例重跑</Button>
    </Popconfirm>}
  </div>
}
