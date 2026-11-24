import { useState } from 'react'
import { Button, Switch, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useManuallyCompleteDailyNode, useRetryDailyProcessing } from '../../../hooks/useDailyPipeline'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import type { DailyManualNodeInput, DailyProcessingStage } from '../../../types/dailyPipeline'
import ManualProcessingForm from './ManualProcessingForm'
import type { ManualProcessingFormValues } from './ManualProcessingForm'

export default function ManualProcessingAction({ stage }: { stage: DailyProcessingStage }) {
  const { data: user } = useCurrentUser()
  const mutation = useManuallyCompleteDailyNode()
  const retry = useRetryDailyProcessing()
  const [manualEnabled, setManualEnabled] = useState(false)
  if (!stage.canManuallyComplete || (user?.role !== 'admin' && user?.role !== 'super_admin')) return null
  const code = stage.stageCode
  const reset = stage.status === 'reset'
  const canResumeAutomatically =
    ((code === 'revert_pr' && stage.revertMode !== 'manual') || code === 'problem_pr_notification')
      && (stage.status === 'failed' || stage.status === 'pending' || reset)
    || code === 'failure_notification' && (stage.status === 'failed' || reset)
    || code === 'pr_merge' && reset
  const submit = async (values: ManualProcessingFormValues) => {
    if (!manualEnabled) return
    const result: DailyManualNodeInput['result'] = {}
    if (values.category) result.category = values.category
    if (values.revert_mode) result.revert_mode = values.revert_mode
    if (values.pr_number) result.pr_number = values.pr_number
    if (values.run_id) result.run_id = values.run_id
    try {
      const response = await mutation.mutateAsync({ nodeId: stage.id, values: { success: values.outcome !== "failure", message: values.message ?? "", result } })
      setManualEnabled(false)
      if (response.automaticQueued === false) void message.warning('已保存，自动处理任务未送达，请重试')
      else void message.success('已提交')
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : null
      void message.error(typeof detail === 'string' ? detail : '提交失败')
    }
  }
  return <div className="daily-processing-manual-action">
    {canResumeAutomatically && <>
      <Button type="primary" size="small" icon={<ReloadOutlined />} loading={retry.isPending} disabled={mutation.isPending}
      style={{ marginBottom: 16 }} onClick={async () => {
        try {
          await retry.mutateAsync({ nodeId: stage.id })
          void message.success(code === 'pr_merge' ? '已恢复 PR 合入跟踪' : '已安排自动处理')
        } catch (error) {
          const detail = isAxiosError(error) ? error.response?.data?.detail : null
          void message.error(typeof detail === 'string' ? detail : '自动处理任务未送达，请稍后重试')
        }
      }}>{code === 'pr_merge' ? '继续跟踪' : stage.status === 'failed' ? '重试' : '执行'}</Button></>}
    <div className={'daily-processing-manual-toggle' + (manualEnabled ? ' is-open' : '')}>
      <label htmlFor={`daily-manual-${stage.id}`}>人工处理：</label>
      <Switch id={`daily-manual-${stage.id}`} aria-label="人工处理" size="small" checked={manualEnabled}
        onChange={setManualEnabled} disabled={mutation.isPending || retry.isPending} />
    </div>
    {manualEnabled && <ManualProcessingForm stage={stage} pending={mutation.isPending || retry.isPending} onSubmit={submit} />}

  </div>
}
