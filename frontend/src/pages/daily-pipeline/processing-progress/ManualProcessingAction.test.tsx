import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import type { DailyProcessingStage } from '../../../types/dailyPipeline'
import ManualProcessingAction from './ManualProcessingAction'
import ManualProcessingForm from './ManualProcessingForm'

let role = 'admin'
vi.mock('../../../hooks/useCurrentUser', () => ({ useCurrentUser: () => ({ data: { role } }) }))
vi.mock('../../../hooks/useDailyPipeline', () => ({ useManuallyCompleteDailyNode: () => ({ isPending: false, mutateAsync: vi.fn() }), useRetryDailyProcessing: () => ({ isPending: false, mutateAsync: vi.fn() }) }))
const stage: DailyProcessingStage = {
  id: '10', name: '问题分类', stageCode: 'classify', status: 'pending', canManuallyComplete: true,
  updatedAt: null, result: null, records: [],
}
const render = (changes: Partial<DailyProcessingStage> = {}) => renderToStaticMarkup(<ManualProcessingAction stage={{ ...stage, ...changes }} />)
const renderForm = (changes: Partial<DailyProcessingStage> = {}) => renderToStaticMarkup(<ManualProcessingForm stage={{ ...stage, ...changes }} pending={false} onSubmit={async () => {}} />)

it('offers human classification without PR or build fields', () => {
  const html = renderForm()
  expect(html).toContain('问题分类')
  expect(html).not.toContain('分类依据')
  expect(html).not.toContain('<textarea')
  expect(html).toContain('提交')
  expect(html).not.toContain('PR 编号')
  expect(html).not.toContain('构建 Run ID')
})

it('does not offer completed nodes or readonly users an action', () => {
  expect(render({ canManuallyComplete: false, status: 'completed' })).toBe('')
  role = 'user'
  expect(render()).toBe('')
  role = 'admin'
})

it('keeps the saved classification readonly during locating', () => {
  const html = renderForm({ stageCode: 'locating', name: '问题定位', problemCategory: 'code' })
  expect(html).not.toContain('<textarea')
  expect(html).not.toContain('问题分类')
  expect(html).toContain('问题 PR 编号')
  expect(html).toContain('代码处理方式')
  expect(html).toContain('系统创建回退 PR')
  expect(html).toContain('用户创建回退 PR')
  expect(html).toContain('优化修复（无需回退）')
})

it('shows the relevant external result reference for later stages', () => {
  const revert = renderForm({ stageCode: 'revert_pr' })
  const merge = renderForm({ stageCode: 'pr_merge' })
  const build = renderForm({ stageCode: 'image_rebuild' })
  expect(revert).toContain('回退 PR 编号')
  expect(merge).toContain('合入 PR 编号')
  expect(build).toContain('构建 Run ID')
  expect(revert + merge + build).not.toContain('<textarea')
  expect(renderForm({ stageCode: 'mark_success' })).not.toContain('PR 编号')
})


it('offers an optional remark for environment locating', () => {
  const html = renderForm({ stageCode: 'locating', problemCategory: 'environment' })
  expect(html).toContain('备注说明（选填）')
  expect(html).not.toContain('问题 PR 编号')
})

it('lets the user confirm the failure notification directly', () => {
  const html = renderForm({ stageCode: 'failure_notification' })
  expect(html).toContain('确认已通知')
  expect(html).not.toContain('<textarea')
})

it('offers an automatic retry only for pending or failed reverts', () => {
  expect(render({ stageCode: 'revert_pr', status: 'failed' })).toContain('重试')
  expect(render({ stageCode: 'revert_pr', status: 'failed', revertMode: 'manual' })).not.toContain('重试')
  expect(render({ stageCode: 'revert_pr', status: 'running' })).not.toContain('重试')
})


it('does not ask for a repository or URL when retrying a revert', () => {
  const html = render({ stageCode: 'revert_pr', status: 'failed' })
  expect(html).not.toContain('原问题 PR 链接')
  expect(html).not.toContain('仓库')
})


it('starts with manual handling switched off and no form or submit button', () => {
  const html = render()
  expect(html).toContain('人工处理：')
  expect(html).toContain('role="switch"')
  expect(html).toContain('aria-checked="false"')
  expect(html).not.toContain('<form')
  expect(html).not.toContain('提交')
  expect(html).not.toContain('分类依据')
})
