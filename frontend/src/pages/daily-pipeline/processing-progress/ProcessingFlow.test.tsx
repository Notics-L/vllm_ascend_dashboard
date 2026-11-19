import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import type { DailyFailedCaseDetail, DailyProcessingStage } from '../../../types/dailyPipeline'
import ProcessingFlow from './ProcessingFlow'

const locating: DailyProcessingStage = {
  id: 'locating', name: '问题定位', status: 'running', result: null,
  updatedAt: '2026-11-12T04:42:00Z', records: [],
}
const markup = (flow: DailyFailedCaseDetail['processingFlow']) => renderToStaticMarkup(<ProcessingFlow flow={flow} />)

it('shows only known stages while locating and does not expose unchosen future steps', () => {
  const html = markup({ currentStageId: 'locating', stages: [locating,
    { ...locating, id: 'future', name: '尚未确定的分支', status: 'pending' },
  ] })
  expect(html).toContain('问题定位')
  expect(html).toContain('进行中')
  expect(html).not.toContain('尚未确定的分支')
  expect(html.match(/<li /g)).toHaveLength(1)
})

it('shows actual branches with drawer triggers and hides results until opened', () => {
  const html = markup({ currentStageId: 'environment-recovery', stages: [
    { ...locating, status: 'completed', result: '环境问题' },
    { ...locating, id: 'environment-recovery', name: '环境恢复', status: 'running' },
  ] })
  expect(html).not.toContain('环境问题')
  expect(html).toContain('环境恢复')
  expect(html).toContain('已完成')
  expect(html).not.toContain('代码问题处理')
  expect(html).not.toContain('处理人')
  expect(html).not.toContain('审批')
  expect(html.match(/<li /g)).toHaveLength(2)
  expect(html.match(/aria-haspopup="dialog"/g)).toHaveLength(2)
  expect(html.match(/aria-expanded="false"/g)).toHaveLength(2)
  const triggers = [...html.matchAll(/<button[^>]*class="daily-processing-event-trigger"[^>]*>([\s\S]*?)<\/button>/g)]
  expect(triggers).toHaveLength(2)
  for (const [, content] of triggers) {
    expect(content).toContain('<time')
    expect(content).toContain('daily-processing-event-dot')
    expect(content).not.toContain('环境恢复')
    expect(content).not.toContain('问题定位')
  }
  expect(html).not.toMatch(/<button[^>]*class="daily-processing-event-heading"/)
  expect(html).not.toContain('<details')
  expect(html).not.toContain('定位结果')
  expect(html).not.toContain('处理结果')
})

it('keeps a scheduled current stage visible without making up a result', () => {
  const html = markup({ currentStageId: 'locating', stages: [{ ...locating, status: 'pending' }] })
  expect(html).toContain('问题定位')
  expect(html).toContain('未开始')
  expect(html).not.toContain('处理结果')
})
