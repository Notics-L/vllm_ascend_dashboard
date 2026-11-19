import { expect, it } from 'vitest'
import type { DailyFailedCase, DailyIssueItem } from '../../../types/dailyPipeline'
import { buildIssueDependencyGraph } from './issueDependencyModel'

const issue: DailyIssueItem = { number: 801, title: 'Daily', state: 'open', runId: 9900002, processingProgress: '处理中' }
const failure: DailyFailedCase = {
  id: 'first', caseId: 'case-1', name: 'Kimi', group: 'Daily', hardware: 'A3', result: '失败', failureLog: null,
  runId: 9900002, collectedAt: null, problemCategory: '代码问题', processingProgress: '已解决', issue: { number: 801, state: 'open' },
}

it('connects all failures from the associated run directly to the issue without a Run node', () => {
  const second = { ...failure, id: 'second', name: 'Other', issue: { number: 999, state: 'open' as const }, processingProgress: '处理中' as const }
  const graph = buildIssueDependencyGraph(issue, [failure, second, { ...failure, id: 'unrelated', runId: 77 }, failure])
  expect(graph.nodes.map((node) => node.id)).toEqual(['issue', 'case:first', 'case:second'])
  expect(graph.edges.map((edge) => [edge.source, edge.target])).toEqual([['case:first', 'issue'], ['case:second', 'issue']])
  expect(graph.nodes.some((node) => node.id === 'run')).toBe(false)
  expect(graph.nodes[0].data.caseCount).toBe(2)
  expect(graph.nodes[0].data.resolvedCount).toBe(1)
  for (const edge of graph.edges) {
    const source = graph.nodes.find((node) => node.id === edge.source)!
    const target = graph.nodes.find((node) => node.id === edge.target)!
    expect(source.position.x).toBeLessThan(target.position.x)
  }
  expect(graph.nodes.find((node) => node.id === 'case:first')?.data.failureId).toBe('first')
})

it('preserves each case state and keeps the actual issue state independent', () => {
  const graph = buildIssueDependencyGraph(issue, [failure, { ...failure, id: 'failed', name: 'Other', processingProgress: '未解决' }])
  expect(graph.nodes.find((node) => node.id === 'case:failed')?.data.status).toBe('未解决')
  expect(graph.nodes[0].data.status).toBe('open')
  expect(graph.nodes[0].data.resolvedCount).toBe(1)
})

it('counts resolved cases without automatically changing the issue open or close state', () => {
  const graph = buildIssueDependencyGraph(issue, [failure])
  expect(graph.nodes[0].data.resolvedCount).toBe(1)
  expect(graph.nodes[0].data.status).toBe('open')
  expect(buildIssueDependencyGraph({ ...issue, state: 'closed' }, [failure]).nodes[0].data.status).toBe('close')
  expect(buildIssueDependencyGraph(issue, []).nodes[0].data.caseCount).toBe(0)
})

it('does not invent a Run ID when the issue has no association', () => {
  const graph = buildIssueDependencyGraph({ ...issue, runId: null }, [failure])
  expect(graph.nodes).toHaveLength(1)
  expect(graph.edges).toEqual([])
})


it('shows latest failure per case in an Issue while preserving actual Issue state', () => {
  const latest = { ...failure, id: 'latest', processingProgress: '处理中' as const }
  const graph = buildIssueDependencyGraph(issue, [latest, failure])
  expect(graph.nodes.map((node) => node.id)).toEqual(['issue', 'case:latest'])
  expect(graph.nodes[0].data.status).toBe('open')
})
