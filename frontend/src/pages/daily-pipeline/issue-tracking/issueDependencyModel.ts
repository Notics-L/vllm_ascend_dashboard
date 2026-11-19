import type { Edge, Node } from '@xyflow/react'
import type { DailyFailedCase, DailyIssueItem } from '../../../types/dailyPipeline'

export type IssueDependencyData = {
  kind: 'issue' | 'case'
  title: string
  subtitle: string
  status: string
  color: string
  failureId?: string
  caseCount?: number
  resolvedCount?: number
}
export type IssueDependencyNode = Node<IssueDependencyData, 'dependency'>

export const ISSUE_CASE_SPACING = 76
const ISSUE_HEIGHT = 180
const CASE_HEIGHT = 64
const progressColors = { 已结束: '#8c8c8c', 已解决: '#389e0d', 未解决: '#cf1322', 处理中: '#1677ff', 待处理: '#8c8c8c' }

export function buildIssueDependencyGraph(issue: DailyIssueItem, records: DailyFailedCase[]): { nodes: IssueDependencyNode[]; edges: Edge[] } {
  const failures = [...new Map(records.filter((row) => row.runId === issue.runId).slice().reverse().map((row) => [JSON.stringify([row.name, row.hardware, row.group, row.vllmVersion]), row])).values()]
  const centerY = Math.max(0, failures.length - 1) * ISSUE_CASE_SPACING / 2 + CASE_HEIGHT / 2
  const nodes: IssueDependencyNode[] = [{
    id: 'issue', type: 'dependency', position: { x: failures.length ? 420 : 0, y: centerY - ISSUE_HEIGHT / 2 },
    data: {
      kind: 'issue', title: `#${issue.number}`, subtitle: 'Issue', status: issue.state === 'open' ? 'open' : 'close',
      color: issue.state === 'open' ? '#1677ff' : '#8c8c8c',
      caseCount: failures.length, resolvedCount: failures.filter((row) => row.processingProgress === '已解决').length,
    },
  }]
  const edges: Edge[] = []
  for (const [index, row] of failures.entries()) {
    const id = `case:${row.id}`
    nodes.push({
      id, type: 'dependency', position: { x: 0, y: index * ISSUE_CASE_SPACING },
      data: {
        kind: 'case', title: row.name,
        subtitle: [row.group === 'Daily' ? 'Daily 用例' : row.group, row.hardware, row.vllmVersion].filter(Boolean).join(' · '),
        status: row.processingProgress, color: progressColors[row.processingProgress], failureId: row.id,
      },
    })
    edges.push({ id: `${id}-issue`, source: id, target: 'issue', type: 'default' })
  }
  return { nodes, edges }
}
