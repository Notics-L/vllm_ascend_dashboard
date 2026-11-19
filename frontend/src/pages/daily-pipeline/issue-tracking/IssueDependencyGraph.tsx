import { createContext, useContext, useMemo, useState } from 'react'
import { Alert, Badge, Button, Spin, Typography } from 'antd'
import { FileTextOutlined, ExperimentOutlined } from '@ant-design/icons'
import { Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { useDailyFailedCases } from '../../../hooks/useDailyPipeline'
import type { DailyIssueItem } from '../../../types/dailyPipeline'
import { buildIssueDependencyGraph, ISSUE_CASE_SPACING } from './issueDependencyModel'
import type { IssueDependencyNode } from './issueDependencyModel'
import '@xyflow/react/dist/style.css'
import './IssueDependencyGraph.css'
import IssueCaseProgressDrawer from './IssueCaseProgressDrawer'

const CaseSelectionContext = createContext<((id: string) => void) | null>(null)
const nodeIcons = { issue: FileTextOutlined, case: ExperimentOutlined }

function DependencyNode({ data }: NodeProps<IssueDependencyNode>) {
  const Icon = nodeIcons[data.kind]
  const selectCase = useContext(CaseSelectionContext)
  const displayTitle = data.kind === 'case' && data.title.startsWith('tests/') ? data.title.split('/').pop() : data.title
  const content = data.kind === 'case' ? <>
    <div className="daily-issue-dependency-node-title"><Icon /><span title={data.title}>{displayTitle}</span></div>
    <div className="daily-issue-dependency-node-meta"><span>{data.subtitle}</span><Badge color={data.color} text={data.status} /></div>
  </> : <>
    <div className="daily-issue-dependency-icon"><Icon /></div>
    <div className="daily-issue-dependency-caption">Issue</div>
    <div className="daily-issue-dependency-value">{data.title}</div>
    <Badge color={data.color} text={data.status} />
    <div className="daily-issue-dependency-footer">{data.caseCount ? `${data.resolvedCount} / ${data.caseCount} 已解决` : '暂无处理进展'}</div>
  </>
  return <div className={`daily-issue-dependency-node daily-issue-dependency-node-${data.kind}`}>
    {data.kind !== 'case' && <Handle type="target" position={Position.Left} isConnectable={false} />}
    {data.failureId ? <button type="button" className="daily-issue-dependency-case-link nodrag nopan"
      aria-haspopup="dialog" onClick={() => selectCase?.(data.failureId!)}>{content}</button> :
      <div className="daily-issue-dependency-node-content">{content}</div>}
    {data.kind !== 'issue' && <Handle type="source" position={Position.Right} isConnectable={false} />}
  </div>
}

const nodeTypes = { dependency: DependencyNode }
const fitViewOptions = { padding: 0.12, minZoom: 0.75, maxZoom: 1 }
const edgeOptions = { markerEnd: { type: MarkerType.ArrowClosed, color: '#c3cedd', width: 14, height: 14 }, style: { stroke: '#c3cedd', strokeWidth: 1 } }

function IssueDependencyGraph({ issue }: { issue: DailyIssueItem }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data, isLoading, isFetching, isError, refetch } = useDailyFailedCases()
  const graph = useMemo(() => buildIssueDependencyGraph(issue, data ?? []), [issue, data])
  const count = graph.nodes.filter((node) => node.data.kind === 'case').length
  return <section className="daily-processing-flow" aria-label="Issue 处理依赖图">
    <Typography.Text className="daily-failure-section-title">处理进展</Typography.Text>
    {isError ? <Alert type="error" showIcon message="处理进展加载失败" action={
      <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
    } /> : <div className="daily-issue-dependency-graph" style={{ height: Math.min(640, Math.max(300, count * ISSUE_CASE_SPACING + 64)) }} aria-busy={isLoading}>
      <CaseSelectionContext.Provider value={setSelectedId}>
      {isLoading ? <Spin className="daily-issue-dependency-loading" size="small" /> : <ReactFlow<IssueDependencyNode> key={`${issue.number}:${issue.runId}:${count}`}
        nodes={graph.nodes} edges={graph.edges} nodeTypes={nodeTypes} defaultEdgeOptions={edgeOptions}
        fitView fitViewOptions={fitViewOptions} minZoom={0.4} maxZoom={1.5}
        nodesDraggable={false} nodesConnectable={false} deleteKeyCode={null}
        zoomOnDoubleClick={false} zoomOnScroll={false} panOnScroll
        proOptions={{ hideAttribution: true }}>
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d5dce6" bgColor="#f3f5f8" />
        <Controls showInteractive={false} fitViewOptions={fitViewOptions} />
      </ReactFlow>}
      </CaseSelectionContext.Provider>
    </div>}
    <IssueCaseProgressDrawer failureId={selectedId} onClose={() => setSelectedId(null)} />
  </section>
}

export default IssueDependencyGraph
