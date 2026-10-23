/**
 * AI 评估 Tab 页面
 * 包含评估触发区、最新评估仪表盘、历史评估列表
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import dayjs, { type Dayjs } from 'dayjs'
import {
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  message,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ClockCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import {
  triggerEvaluation,
  listEvaluations,
  getLatestEvaluation,
  getEvaluationDetail,
  trustScoreColor,
  trustLevelLabel,
  type AIEvaluationSummary,
  type AIEvaluation,
  type Workflow,
} from '../../services/releaseManagement'

const { Text, Paragraph } = Typography
const { RangePicker } = DatePicker
const { Option } = Select

/* ===================== ECharts 仪表盘 ===================== */

function GaugeChart({ score }: { score: number | null }) {
  const domRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!domRef.current || score === null) return
    const chart = echarts.init(domRef.current)
    const color = trustScoreColor(score)
    chart.setOption({
      series: [{
        type: 'gauge',
        min: 0,
        max: 100,
        radius: '90%',
        center: ['50%', '60%'],
        pointer: { show: true, length: '60%', width: 8, itemStyle: { color } },
        axisLine: {
          lineStyle: {
            width: 20,
            color: [
              [0.4, '#ff4d4f'],
              [0.6, '#faad14'],
              [0.8, '#faad14'],
              [1, '#52c41a'],
            ],
          },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: '#8c8c8c', fontSize: 11, distance: -24 },
        detail: {
          valueAnimation: true,
          formatter: '{value}',
          fontSize: 36,
          fontWeight: 700,
          color,
          offsetCenter: [0, '70%'],
        },
        data: [{ value: score, name: '可信度评分' }],
        title: { show: true, offsetCenter: [0, '90%'], fontSize: 12, color: '#8c8c8c' },
      }],
    })
    window.addEventListener('resize', () => chart.resize())
    return () => {
      chart.dispose()
    }
  }, [score])

  return <div ref={domRef} style={{ width: '100%', height: 200 }} />
}

/* ===================== 评估触发区 ===================== */

interface TriggerFormValues {
  workflow_id: number | undefined
  evaluation_type: string
  date_range: any[] | null
  model_name: string | undefined
}

function TriggerFormSelector({
  onTrigger, triggerLoading, defaultWorkflow, workflows, defaultRange,
}: {
  onTrigger: (v: TriggerFormValues) => void
  triggerLoading: boolean
  defaultWorkflow: number | null
  workflows: Workflow[]
  defaultRange: [Dayjs | null, Dayjs | null]
}) {
  const [wfId, setWorkflowId] = useState<number | undefined>(defaultWorkflow || undefined)
  const [evalType, setEvalType] = useState('trustworthiness')
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(defaultRange ?? null)
  const [modelName, setModelName] = useState<string | undefined>(undefined)

  return (
    <Row gutter={[16, 0]} align="middle">
      <Col>
        <Select
          value={wfId}
          onChange={setWorkflowId}
          style={{ width: 220 }}
          placeholder="选择工作流"
        >
          {workflows.map((w) => (
            <Option key={w.id} value={w.id}>{w.workflow_name}</Option>
          ))}
        </Select>
      </Col>
      <Col>
        <Select value={evalType} onChange={setEvalType} style={{ width: 140 }}>
          <Option value="trustworthiness">可信度评估</Option>
          <Option value="release_readiness">发布就绪评估</Option>
        </Select>
      </Col>
      <Col>
        <RangePicker value={dateRange} onChange={setDateRange} style={{ width: 260 }} />
      </Col>
      <Col>
        <Select
          mode="tags"
          value={modelName}
          onChange={(v) => setModelName(Array.isArray(v) ? v[0] : v)}
          style={{ width: 160 }}
          placeholder="LLM 模型"
          maxCount={1}
        />
      </Col>
      <Col>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={triggerLoading}
          onClick={() => onTrigger({
            workflow_id: wfId,
            evaluation_type: evalType,
            date_range: dateRange,
            model_name: modelName,
          })}
        >
          触发评估
        </Button>
      </Col>
    </Row>
  )
}

/* ===================== 最新评估展示区 ===================== */

function LatestEvaluationPanel({
  evaluation, reloadKey,
}: {
  evaluation: AIEvaluation | null
  reloadKey: number
}) {
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<AIEvaluation | null>(null)

  useEffect(() => {
    if (!evaluation?.id) return
    setDetailLoading(true)
    getEvaluationDetail(evaluation.id)
      .then(setDetail)
      .catch(() => message.error('加载评估详情失败'))
      .finally(() => setDetailLoading(false))
  }, [evaluation?.id, reloadKey])

  const ev = detail || evaluation

  if (!ev || !ev.id) {
    return (
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <div style={{ textAlign: 'center', padding: '40px 0', color: '#bfbfbf' }}>
          暂无评估数据，请先触发一次评估
        </div>
      </Card>
    )
  }

  const score = ev.trust_score

  return (
    <Spin spinning={detailLoading}>
      {/* Score + Summary */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card bordered={false} title="可信度评分">
            <div style={{ textAlign: 'center' }}>
              <GaugeChart score={score} />
              <div style={{ marginTop: 8 }}>
                <Tag color={trustScoreColor(score)} style={{ fontSize: 14 }}>
                  信任等级: {trustLevelLabel(ev.trust_level)}
                </Tag>
                {ev.production_ready && (
                  <Tag color="success" style={{ marginLeft: 8, fontSize: 14 }}>
                    可上线
                  </Tag>
                )}
                {!ev.production_ready && (
                  <Tag color="error" style={{ marginLeft: 8, fontSize: 14 }}>
                    不建议上线
                  </Tag>
                )}
              </div>
            </div>
          </Card>
        </Col>
        <Col span={16}>
          <Card bordered={false} title="评估概要">
            <Descriptions column={2} size="small" bordered style={{ marginBottom: 8 }}>
              <Descriptions.Item label="评估类型">{ev.evaluation_type === 'trustworthiness' ? '可信度评估' : '发布就绪评估'}</Descriptions.Item>
              <Descriptions.Item label="覆盖次数">{ev.run_count} 次</Descriptions.Item>
              <Descriptions.Item label="评估模型">{ev.llm_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="生成时间">
                {ev.generated_at ? new Date(ev.generated_at).toLocaleString('zh-CN') : '-'}
              </Descriptions.Item>
            </Descriptions>
            {ev.executive_summary && (
              <Paragraph style={{ marginTop: 12, padding: '8px 12px', background: '#fafafa', borderRadius: 6 }}
                ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}>
                {ev.executive_summary}
              </Paragraph>
            )}
          </Card>
        </Col>
      </Row>

      {/* Findings + Risks + Recommendations */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {/* Findings */}
        {ev.findings?.length > 0 && (
          <Col span={8}>
            <Card bordered={false} title="核心发现">
              {ev.findings.map((f) => (
                <div key={f.id} style={{ marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid #f0f0f0' }}>
                  <Space>
                    <Tag color={
                      f.severity === 'high' ? 'error' :
                      f.severity === 'medium' ? 'warning' : 'default'
                    }>
                      {f.severity === 'high' ? '高' : f.severity === 'medium' ? '中' : '低'}
                    </Tag>
                    <Text strong>{f.title}</Text>
                  </Space>
                  <Paragraph style={{ marginTop: 4, marginBottom: 0, fontSize: 12, color: '#8c8c8c' }}>
                    {f.description}
                  </Paragraph>
                </div>
              ))}
            </Card>
          </Col>
        )}

        {/* Risk Factors */}
        {ev.risk_factors?.length > 0 && (
          <Col span={8}>
            <Card bordered={false} title="风险因子">
              {ev.risk_factors.map((r, i) => (
                <div key={i} style={{ marginBottom: 10 }}>
                  <Text strong style={{ display: 'block' }}>{r.name}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>{r.description}</Text>
                  <Tag color="orange" style={{ marginTop: 4 }}>影响: {r.impact}</Tag>
                </div>
              ))}
            </Card>
          </Col>
        )}

        {/* Recommendations */}
        {ev.recommendations?.length > 0 && (
          <Col span={8}>
            <Card bordered={false} title="改进建议">
              {ev.recommendations.map((r, i) => (
                <div key={i} style={{ marginBottom: 10 }}>
                  <Space>
                    <Tag color={
                      r.priority === 'high' ? 'red' :
                      r.priority === 'medium' ? 'orange' : 'blue'
                    }>
                      {r.priority}
                    </Tag>
                    <Text strong>{r.title}</Text>
                  </Space>
                  <Paragraph style={{ marginTop: 2, marginBottom: 0, fontSize: 12, color: '#8c8c8c' }}>
                    {r.description}
                  </Paragraph>
                </div>
              ))}
            </Card>
          </Col>
        )}
      </Row>

      {/* Data quality issues */}
      {ev.data_quality_issues?.length > 0 && (
        <Card bordered={false} title="数据质量问题" style={{ marginBottom: 16 }}>
          <Space wrap>
            {ev.data_quality_issues.map((issue, i) => (
              <Tag key={i} color="yellow" style={{ fontSize: 12 }}>{issue}</Tag>
            ))}
          </Space>
        </Card>
      )}

      {/* Report content */}
      {ev.report_content && (
        <Card bordered={false} title="完整报告" style={{ marginBottom: 16 }}>
          <pre style={{
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6,
            maxHeight: 400, overflow: 'auto', background: '#fafafa', padding: 12, borderRadius: 6,
          }}>
            {ev.report_content}
          </pre>
        </Card>
      )}
    </Spin>
  )
}

/* ===================== 历史评估列表 ===================== */

function HistoryTable({
  workflowId, onEvalClick, reloadKey,
}: {
  workflowId: number | null
  onEvalClick: (ev: AIEvaluationSummary) => void
  reloadKey: number
}) {
  const [loading, setLoading] = useState(false)
  const [evaluations, setEvaluations] = useState<AIEvaluationSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const result = await listEvaluations(workflowId, page, pageSize)
      setEvaluations(result.evaluations)
      setTotal(result.total)
    } catch {
      message.error('加载评估列表失败')
    } finally {
      setLoading(false)
    }
  }, [workflowId, page, pageSize])

  useEffect(() => {
    loadData()
  }, [loadData, reloadKey])

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '评估类型',
      dataIndex: 'evaluation_type',
      width: 120,
      render: (v: string) => v === 'trustworthiness' ? '可信度评估' : '发布就绪评估',
    },
    {
      title: '可信度评分',
      dataIndex: 'trust_score',
      width: 100,
      render: (s: number | null) => (
        <Text strong style={{ color: trustScoreColor(s) }}>
          {s ?? '-'}
        </Text>
      ),
    },
    {
      title: '信任等级',
      dataIndex: 'trust_level',
      width: 80,
      render: (v: string | null) => (
        <Tag color={trustScoreColor(v ? parseInt(v) || 0 : null)}>
          {trustLevelLabel(v)}
        </Tag>
      ),
    },
    {
      title: '可上线',
      dataIndex: 'production_ready',
      width: 60,
      render: (v: boolean) => v ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag>,
    },
    { title: '覆盖次数', dataIndex: 'run_count', width: 80 },
    { title: '模型', dataIndex: 'llm_model', width: 100, ellipsis: true },
    {
      title: '生成时间',
      dataIndex: 'generated_at',
      width: 180,
      render: (v: string | null) => v ? new Date(v).toLocaleString('zh-CN') : '-',
    },
  ]

  return (
    <Card
      bordered={false}
      title={
        <Space>
          <ClockCircleOutlined />
          <span>历史评估 ({total})</span>
        </Space>
      }
      extra={
        <Button icon={<ReloadOutlined />} onClick={loadData}>
          刷新
        </Button>
      }
    >
      <Table
        dataSource={evaluations}
        rowKey="id"
        size="small"
        loading={loading}
        scroll={{ x: 900 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
        columns={columns}
      />
    </Card>
  )
}

/* ===================== 主组件 ===================== */

interface AIEvaluationTabProps {
  selectedIds: number[] | null
  workflows: Workflow[]
}

export default function AIEvaluationTab({ selectedIds, workflows }: AIEvaluationTabProps) {
  const [latest, setLatest] = useState<AIEvaluationSummary | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [triggerLoading, setTriggerLoading] = useState(false)

  const selectedWorkflowId = selectedIds?.[0] ?? null

  const loadLatest = useCallback(() => {
    getLatestEvaluation(selectedWorkflowId)
      .then(setLatest)
      .catch(() => {})
  }, [selectedWorkflowId])

  useEffect(() => {
    loadLatest()
  }, [loadLatest])

  const handleTriggered = () => {
    setReloadKey((k) => k + 1)
    loadLatest()
  }

  const handleTrigger = async (v: TriggerFormValues) => {
    if (!v.workflow_id) { message.warning('请选择工作流'); return }
    const periodStart = v.date_range?.[0]?.toISOString() ?? null
    const periodEnd = v.date_range?.[1]?.toISOString() ?? null
    setTriggerLoading(true)
    try {
      const result = await triggerEvaluation(
        v.workflow_id,
        v.evaluation_type,
        periodStart,
        periodEnd,
        v.model_name || null,
      )
      message.success(
        `评估已提交 (任务 ${result.task_id}) · 可信度: ${result.trust_score ?? '计算中'}`,
      )
      handleTriggered()
    } catch {
      message.error('触发评估失败')
    } finally {
      setTriggerLoading(false)
    }
  }

  return (
    <div>
      {/* Trigger Area */}
      <Card bordered={false} title="触发评估" style={{ marginBottom: 16 }}>
        <TriggerFormSelector
          onTrigger={handleTrigger}
          triggerLoading={triggerLoading}
          defaultWorkflow={selectedWorkflowId}
          workflows={workflows}
          defaultRange={[dayjs().subtract(30, 'day'), dayjs()]}
        />
      </Card>

      {/* Latest Evaluation */}
      <LatestEvaluationPanel evaluation={latest as AIEvaluation} reloadKey={reloadKey} />

      {/* History Table */}
      <HistoryTable
        workflowId={selectedWorkflowId}
        onEvalClick={() => {}}
        reloadKey={reloadKey}
      />
    </div>
  )
}
