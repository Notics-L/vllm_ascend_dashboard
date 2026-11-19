import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import DailyPipelineCollection from './index'
import type { DailyCollectionRun } from '../../../types/dailyPipeline'

const running: DailyCollectionRun = {
  id: 'current', runId: 101, startedAt: '2026-11-12T04:00:00Z', endedAt: null,
  triggerHistoryId: 'trigger-1', issue: null, lastCollectedAt: null,
  result: '执行中',
}
const fixtures: DailyCollectionRun[] = [
  running,
  { ...running, id: 'previous', runId: 102, triggerHistoryId: 'trigger-2',
    endedAt: '2026-11-12T04:28:42Z', result: '失败',
    issue: { number: 12, state: 'open' } },
]

it('shows Run ID in record cells and keeps it as the internal row key', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['daily-pipeline', 'collection-runs'], fixtures)
  const originalError = console.error
  const error = vi.spyOn(console, 'error').mockImplementation((message, ...args) => {
    if (typeof message === 'string' && message.includes('useLayoutEffect does nothing on the server')) return
    originalError(message, ...args)
  })
  try {
    const html = renderToStaticMarkup(<QueryClientProvider client={client}><MemoryRouter><DailyPipelineCollection /></MemoryRouter></QueryClientProvider>)
    const text = html.replace(/<[^>]*>/g, '')
    expect(text).toContain('流水线结果')
    expect(text).not.toContain('采集记录')
    expect(text).toContain('删除')
    expect(html).toContain('type="checkbox"')
    for (const label of ['开始时间', '结束时间', '耗时', 'Issue', '关联']) expect(text).toContain(label)
    expect(text).toContain('2026-11-12 12:28:42')
    expect(text).toContain('28m 42s')
    expect(text).toMatch(/run\s*id/i)
    expect(text).toContain('触发日志')
    expect(html).not.toContain('href="/daily-pipeline/trigger')
    for (const row of fixtures) {
      expect(text).toContain(String(row.runId))
      expect(html).toContain('data-row-key="' + row.runId + '"')
      const renderedRow = html.match(new RegExp('<tr[^>]*data-row-key="' + row.runId + '"[^>]*>([\\s\\S]*?)</tr>'))
      expect(renderedRow).not.toBeNull()
      const detailLink = [...renderedRow![1].matchAll(/<a\b[^>]*>/g)].find(([tag]) => tag.includes('/daily-pipeline/collection/runs/' + row.runId + '"'))
      expect(detailLink).toBeDefined()
      expect(detailLink![0]).not.toContain('target="_blank"')
      const cells = [...renderedRow![1].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/g)].slice(1).map((cell) => cell[1].replace(/<[^>]*>/g, ''))
      expect(cells[6]).toContain('触发日志')
      if (row.result === '失败') expect(row.issue).not.toBeNull()
      if (row.issue) {
        expect(cells[5]).toBe('#' + row.issue.number + '-' + (row.issue.state === 'open' ? 'open' : 'close'))
        expect(renderedRow![1]).not.toContain('href="https://github.com/vllm-project/vllm-ascend/issues/')
      } else expect(cells[5]).toBe('—')
      if (!row.endedAt) expect(cells[4]).toBe('—')

    }
  } finally {
    error.mockRestore()
    client.clear()
  }
})
