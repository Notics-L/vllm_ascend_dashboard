import { Button, Descriptions, Form, Input, Select, Space, Switch, Typography, message } from 'antd'
import { CheckOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useState } from 'react'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import { useDailyVersionFlowMutations } from '../../../hooks/useDailyPipeline'
import type { DailyVersionFlow, DailyVersionFlowInput } from '../../../types/dailyPipeline'

export default function VersionCompatibilityFlow({ flow, allowManual }: {
  flow: DailyVersionFlow
  allowManual: boolean
}) {
  const { data: user } = useCurrentUser()
  const { update } = useDailyVersionFlowMutations()
  const [enabled, setEnabled] = useState(false)
  const [mode, setMode] = useState<'external_recovered' | 'track_pr'>('track_pr')
  const registeringBuild = flow.status === 'waiting_build' || flow.status === 'build_failed'
  const registeringResolution = flow.status === 'waiting_resolution' || flow.status === 'waiting_pr'
  const canEdit = allowManual && flow.canEdit && (user?.role === 'admin' || user?.role === 'super_admin')
  const references = flow.resolution.references
  const submit = async (values: Omit<DailyVersionFlowInput, 'action'>) => {
    try {
      await update.mutateAsync({
        failureId: flow.failureId,
        values: { ...values, action: registeringBuild ? 'track_build' : flow.status === 'waiting_pr' ? 'track_pr' : mode },
      })
      setEnabled(false)
      void message.success(registeringBuild ? '已登记构建链接' : '已保存配套处理进展')
    } catch (error) {
      const detail = isAxiosError(error) ? error.response?.data?.detail : null
      const action = registeringBuild ? '构建链接登记失败' : '配套进展提交失败'
      void message.error(`${action}。${typeof detail === 'string' ? detail : '请稍后重试。'}`)
    }
  }
  return <div className="daily-processing-manual-action">
    <Typography.Text className="daily-failure-section-title">
      {flow.memberCount > 1 ? `版本配套流程（${flow.memberCount} 条用例共用）` : '版本配套流程'}
    </Typography.Text>
    <Descriptions size="small" column={1} items={[
      ...(references?.repair ? [{ key: 'repair', label: '修复 PR', children: <a href={references.repair.pr_url} target="_blank" rel="noopener noreferrer">#{references.repair.pr_number}</a> }] : []),
      ...(references?.revert ? [{ key: 'revert', label: '回退 PR', children: <a href={references.revert.pr_url} target="_blank" rel="noopener noreferrer">#{references.revert.pr_number}</a> }] : []),
      ...(flow.resolution.note ? [{ key: 'resolution-note', label: '恢复说明', children: flow.resolution.note }] : []),
      ...(flow.triggerUrl && (registeringBuild || flow.status === 'building') ? [{ key: 'trigger', label: '构建入口', children: <a href={flow.triggerUrl} target="_blank" rel="noopener noreferrer">打开镜像构建工作流</a> }] : []),
      ...(flow.build.url ? [{ key: 'build', label: '跟踪链接', children: <a href={flow.build.url} target="_blank" rel="noopener noreferrer">Run #{flow.build.run_id}</a> }] : []),
    ]} />
    {canEdit && (registeringBuild || registeringResolution) && <>
      <Space className="daily-processing-manual-toggle">
        <label htmlFor={`daily-version-${flow.failureId}`}>人工处理</label>
        <Switch id={`daily-version-${flow.failureId}`} size="small" checked={enabled} onChange={setEnabled} disabled={update.isPending} />
      </Space>
      {enabled && <Form layout="vertical" size="small" onFinish={submit} disabled={update.isPending}
        initialValues={{ repair_url: references?.repair?.pr_url, revert_url: references?.revert?.pr_url }}>
        {registeringBuild ? <Form.Item name="build_url" label="实际构建 Run 链接" rules={[{ required: true, message: '请填写构建 Run 链接' }]}>
          <Input placeholder="https://github.com/组织/仓库/actions/runs/…" />
        </Form.Item> : <>
          {flow.status === 'waiting_resolution' && <Form.Item label="恢复方式">
            <Select value={mode} onChange={setMode} options={[
              { value: 'track_pr', label: '跟踪修复或回退 PR' },
              { value: 'external_recovered', label: '确认外部配套已恢复' },
            ]} />
          </Form.Item>}
          {mode === 'external_recovered' && flow.status === 'waiting_resolution'
            ? <Form.Item name="message" label="恢复说明" rules={[{ required: true, whitespace: true, message: '请填写恢复说明' }]}>
              <Input.TextArea rows={3} />
            </Form.Item>
            : <>
              <Form.Item name="repair_url" label="修复 PR 链接" preserve={false}><Input /></Form.Item>
              <Form.Item name="revert_url" label="回退 PR 链接" preserve={false}><Input /></Form.Item>
            </>}
        </>}
        <div className="daily-cases-actions">
          <Button type="primary" htmlType="submit" icon={<CheckOutlined />} loading={update.isPending}>提交</Button>
        </div>
      </Form>}
    </>}
  </div>
}
