import { Button, Popconfirm, message } from 'antd'
import { RollbackOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import { useRollbackDailyProcessing } from '../../../hooks/useDailyPipeline'
import type { DailyProcessingStage } from '../../../types/dailyPipeline'

export default function RollbackProcessingAction({ stage, failureId, sharedMemberCount, onRolledBack }: {
  stage: DailyProcessingStage
  failureId?: string
  sharedMemberCount: number
  onRolledBack: () => void
}) {
  const { data: user } = useCurrentUser()
  const rollback = useRollbackDailyProcessing()
  if (user?.role !== 'admin' && user?.role !== 'super_admin') return null
  const code = stage.stageCode
  const confirm = async () => {
    try {
      if (failureId && (code === 'version_resolution' || code === 'version_build')) {
        await rollback.mutateAsync({ failureId, stage: code })
      } else {
        await rollback.mutateAsync({ nodeId: stage.id })
      }
      onRolledBack()
      void message.success('已回退，可以重新处理')
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : null
      void message.error(typeof detail === 'string' ? detail : '回退失败，请稍后重试')
    }
  }
  return <div className="daily-processing-rollback-action">
    <Popconfirm title={`回退到「${stage.name}」？`}
      description={sharedMemberCount > 1
        ? '共用流程的相关节点将重新处理，已有执行记录会保留。'
        : '此节点和依赖它的后续节点将重新处理，已有执行记录会保留。'}
      okText="回退" cancelText="取消" onConfirm={confirm}>
      <Button size="small" icon={<RollbackOutlined />} loading={rollback.isPending}>回退到此节点</Button>
    </Popconfirm>
  </div>
}
