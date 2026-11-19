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
  expect(html).toContain('定位结果')
  expect(html).not.toContain('问题分类')
  expect(html).toContain('问题 PR 编号')
})

it('shows the relevant external result reference for later stages', () => {
  expect(renderForm({ stageCode: 'revert_pr' })).toContain('回退 PR 编号')
  expect(renderForm({ stageCode: 'pr_merge' })).toContain('合入 PR 编号')
  expect(renderForm({ stageCode: 'image_rebuild' })).toContain('构建 Run ID')
  expect(renderForm({ stageCode: 'mark_success' })).not.toContain('PR 编号')
})


it('does not ask for a PR for environment locating', () => {
  expect(renderForm({ stageCode: 'locating', problemCategory: 'environment' })).not.toContain('问题 PR 编号')
})

it('offers an automatic retry only for pending or failed reverts', () => {
  expect(render({ stageCode: 'revert_pr', status: 'failed' })).toContain('重试')
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
