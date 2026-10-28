/**
 * AI 评估 Tab 页面
 * 包含评估触发区、最新评估仪表盘、历史评估列表
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import dayjs, { type Dayjs } from 'dayjs'
import { formatTimezone } from '@/utils/timezone'
import {
  Button,
  Card,
  Col,
  DatePicker,
  Descriptions,
  Drawer,
  message,
  Modal,
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
  CodeOutlined,
  EyeOutlined,
  PlayCircleOutlined,
  ReadOutlined,
  ReloadOutlined,
} from '@ant-design/icons'

import {
  triggerEvaluation,
  listEvaluations,
  getLatestEvaluation,
  getEvaluationDetail,
  trustScoreColor,
  trustLevelLabel,
  getTrustLevelColor,
  type AIEvaluationSummary,
  type AIEvaluation,
  type Workflow,
} from '../../services/releaseManagement'

const { Text, Paragraph } = Typography
const { RangePicker } = DatePicker
const { Option } = Select

/* ===================== ECharts 仪表盘 ===================== */

function GaugeChart({ score, size = 'normal' }: { score: number | null; size?: 'small' | 'normal' | 'large' }) {
  const domRef = useRef<HTMLDivElement | null>(null)
  const color = score !== null ? trustScoreColor(score) : '#8c8c8c'

  const dimMap = {
    small: { chartH: 160, scoreFont: 28, labelFont: 11, pointerW: 6, axisW: 18 },
    normal: { chartH: 220, scoreFont: 48, labelFont: 13, pointerW: 10, axisW: 30 },
    large: { chartH: 280, scoreFont: 56, labelFont: 14, pointerW: 14, axisW: 40 },
  }
  const dim = dimMap[size]

  useEffect(() => {
    if (!domRef.current || score === null) return
    const chart = echarts.init(domRef.current)
    chart.setOption({
      series: [{
        type: 'gauge',
        min: 0,
        max: 100,
        radius: '80%',
        center: ['50%', '68%'],
        startAngle: 210,
        endAngle: -30,
        pointer: {
          show: true,
          length: '55%',
          width: dim.pointerW,
          itemStyle: { color },
        },
        axisLine: {
          lineStyle: {
            width: dim.axisW,
            color: [
              [0.4, '#ff4d4f'],
              [0.6, '#faad14'],
              [1, '#52c41a'],
            ],
          },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: { show: false },
        data: [{ value: score }],
        title: { show: false },
        animationDuration: 800,
      }],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [score, size])

  return (
    <div style={{ width: '100%', textAlign: 'center' }}>
      <div ref={domRef} style={{ width: '100%', height: dim.chartH }} />
      <div style={{ fontSize: dim.scoreFont, fontWeight: 700, color, lineHeight: 1 }}>
        {score ?? '-'}
      </div>
      <div style={{ fontSize: dim.labelFont, color: '#8c8c8c', marginTop: 4 }}>可信度评分</div>
    </div>
  )
}

/* ===================== 评估触发区 ===================== */

interface TriggerFormValues {
  workflow_id: number | undefined
  evaluation_type: string
  date_range: any[] | null
  model_name: string | undefined
}

function TriggerFormSelector({
  onTrigger, triggerLoading, defaultWorkflow, workflows, defaultRange, onWorkflowChange,
}: {
  onTrigger: (v: TriggerFormValues) => void
  triggerLoading: boolean
  defaultWorkflow: number | null
  workflows: Workflow[]
  defaultRange: [Dayjs | null, Dayjs | null]
  onWorkflowChange: (id: number | undefined) => void
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
          onChange={(v) => { setWorkflowId(v); onWorkflowChange(v) }}
          style={{ width: 220 }}
          placeholder="选择工作流"
          showSearch
          filterOption={(input, option) =>
            String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
          }
          optionFilterProp="label"
        >
          {workflows.map((w) => (
            <Option key={w.id} value={w.id} label={w.workflow_name}>{w.workflow_name}</Option>
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

/* Parse data summary text into structured items */
function parseDataSummary(text: string): Array<{ key: string; value: string }> {
  const items: Array<{ key: string; value: string }> = []
  text.split('\n').forEach((line) => {
    const idx = line.indexOf(':')
    if (idx > 0) {
      items.push({ key: line.slice(0, idx).trim(), value: line.slice(idx + 1).trim() })
    }
  })
  return items
}

function LatestEvaluationPanel({
  evaluation, reloadKey, compact,
}: {
  evaluation: AIEvaluation | null
  reloadKey: number
  compact?: boolean
}) {
  const [detailLoading, setDetailLoading] = useState(false)
  const [detail, setDetail] = useState<AIEvaluation | null>(null)
  const [jsonVisible, setJsonVisible] = useState(false)
  const [jsonContent, setJsonContent] = useState('')

  useEffect(() => {
    if (!evaluation?.id) return
    setDetailLoading(true)
    getEvaluationDetail(evaluation.id)
      .then((data) => {
        setDetail(data)
        if (data.report_content) {
          try {
            const parsed = JSON.parse(data.report_content)
            setJsonContent(JSON.stringify(parsed, null, 2))
          } catch {
            setJsonContent(data.report_content)
          }
        }
      })
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
  const gaugeSize: 'small' | 'normal' | 'large' = compact ? 'large' : 'normal'

  /* Parse algorithm breakdown */
  let breakdown: Record<string, number> | null = null
  let dataSummaryText: string | null = null
  if (ev.report_content) {
    try {
      const rpt = JSON.parse(ev.report_content)
      if (rpt.algorithm_breakdown && typeof rpt.algorithm_breakdown === 'object') {
        breakdown = rpt.algorithm_breakdown
      }
      if (rpt.data_summary) {
        dataSummaryText = rpt.data_summary
      }
    } catch { /* ignore */ }
  }

  const dimensionMap: Record<string, string> = {
    success: '成功率',
    stability: '稳定性',
    coverage: '覆盖率',
    compliance: '合规性',
    trend: '趋势',
  }

  const summaryItems = dataSummaryText ? parseDataSummary(dataSummaryText) : []

  return (
    <Spin spinning={detailLoading}>
      {/* Score + Summary — equal height via stretch */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }} align="stretch">
        <Col span={compact ? 8 : 8}>
          <Card bordered={false} title="可信度评分" bodyStyle={{ height: '100%' }}>
            <GaugeChart score={score} size={gaugeSize} />
            <Space style={{ marginTop: 8, justifyContent: 'center', display: 'flex', flexWrap: 'wrap', width: '100%' }}>
              <Tag color={getTrustLevelColor(ev.trust_level || 'low')} style={{ fontSize: 14, padding: '2px 10px' }}>
                {trustLevelLabel(ev.trust_level)}
              </Tag>
              {ev.production_ready ? (
                <Tag color="#52c41a" style={{ fontSize: 14, padding: '2px 10px' }}>
                  可上线
                </Tag>
              ) : (
                <Tag color="#ff4d4f" style={{ fontSize: 14, padding: '2px 10px' }}>
                  不建议上线
                </Tag>
              )}
            </Space>
          </Card>
        </Col>
        <Col span={compact ? 16 : 16}>
          <Card bordered={false} title="评估概要" extra={
            ev.report_content ? (
              <Button size="small" icon={<CodeOutlined />} onClick={() => setJsonVisible(true)}>
                查看 JSON 详情
              </Button>
            ) : null
          }>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="评估类型">
                {ev.evaluation_type === 'trustworthiness' ? '可信度评估' : '发布就绪评估'}
              </Descriptions.Item>
              <Descriptions.Item label="覆盖次数">{ev.run_count} 次</Descriptions.Item>
              <Descriptions.Item label="评估模型">{ev.llm_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="生成时间">
                {formatTimezone(ev.generated_at)}
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

      {/* Algorithm Breakdown + Data Summary — equal height */}
      {(breakdown || summaryItems.length > 0) && (
        <Row gutter={[16, 16]} style={{ marginBottom: 16 }} align="stretch">
          {breakdown && (
            <Col span={12}>
              <Card bordered={false} title="算法评分明细">
                <Descriptions column={1} size="small" bordered>
                  {Object.entries(breakdown).map(([key, value]) => (
                    <Descriptions.Item key={key} label={dimensionMap[key] || key}>
                      <Text strong style={{ color: trustScoreColor(value) }}>
                        {value}
                      </Text>
                    </Descriptions.Item>
                  ))}
                  <Descriptions.Item label="总分">
                    <Text strong style={{ color: trustScoreColor(score), fontSize: 16 }}>
                      {score}
                    </Text>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          )}
          {summaryItems.length > 0 && (
            <Col span={breakdown ? 12 : 24}>
              <Card bordered={false} title="数据摘要">
                <Descriptions column={1} size="small" bordered>
                  {summaryItems.map((item) => (
                    <Descriptions.Item key={item.key} label={item.key}>
                      <Text>{item.value}</Text>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            </Col>
          )}
        </Row>
      )}

      {/* Findings + Risks + Recommendations */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
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

      {/* JSON Raw Detail Modal */}
      <Modal
        title="完整报告 (JSON)"
        open={jsonVisible}
        onCancel={() => setJsonVisible(false)}
        footer={null}
        width={800}
      >
        <pre style={{
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6,
          maxHeight: 600, overflow: 'auto', background: '#fafafa', padding: 12, borderRadius: 6,
        }}>
          {jsonContent}
        </pre>
      </Modal>
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
  const [viewingEv, setViewingEv] = useState<AIEvaluationSummary | null>(null)
  const [viewLoading, setViewLoading] = useState(false)
  const [viewDetail, setViewDetail] = useState<AIEvaluation | null>(null)
  const [jsonModalVisible, setJsonModalVisible] = useState(false)
  const [jsonText, setJsonText] = useState('')
  const [drawerKey, setDrawerKey] = useState(0)

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

  const handleViewReport = async (record: AIEvaluationSummary) => {
    setViewingEv(record)
    setViewLoading(true)
    setViewDetail(null)
    try {
      const d = await getEvaluationDetail(record.id)
      setViewDetail(d)
      setDrawerKey((k) => k + 1)
      setViewLoading(false)
      setViewingEv(null)
    } catch {
      message.error('加载评估详情失败')
      setViewLoading(false)
    }
  }

  const handleViewJson = async (record: AIEvaluationSummary) => {
    try {
      const d = await getEvaluationDetail(record.id)
      if (d.report_content) {
        try {
          const parsed = JSON.parse(d.report_content)
          setJsonText(JSON.stringify(parsed, null, 2))
        } catch {
          setJsonText(d.report_content)
        }
        setJsonModalVisible(true)
      } else {
        message.info('该评估没有完整报告')
      }
    } catch {
      message.error('加载评估详情失败')
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    {
      title: '评估类型',
      dataIndex: 'evaluation_type',
      width: 120,
      render: (v: string) => v === 'trustworthiness' ? '可信度评估' : '发布就绪评估',
    },
    {
      title: '可信度评分',
      dataIndex: 'trust_score',
      width: 90,
      render: (s: number | null) => (
        <Text strong style={{ color: trustScoreColor(s) }}>
          {s ?? '-'}
        </Text>
      ),
    },
    {
      title: '信任等级',
      dataIndex: 'trust_level',
      width: 70,
      render: (v: string | null) => (
        <Tag color={getTrustLevelColor(v || 'low')}>
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
    { title: '覆盖次数', dataIndex: 'run_count', width: 70 },
    { title: '模型', dataIndex: 'llm_model', width: 90, ellipsis: true },
    {
      title: '生成时间',
      dataIndex: 'generated_at',
      width: 160,
      render: (v: string | null) => formatTimezone(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_: any, record: AIEvaluationSummary) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            loading={viewLoading && viewingEv?.id === record.id}
            onClick={() => handleViewReport(record)}
          >
            查看
          </Button>
          <Button
            type="link"
            size="small"
            icon={<CodeOutlined />}
            onClick={() => handleViewJson(record)}
          >
            JSON
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <>
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
          scroll={{ x: 1000 }}
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

      {/* View report drawer */}
      <Drawer
        title="评估报告详情"
        open={!!viewDetail}
        onClose={() => setViewDetail(null)}
        width={800}
        destroyOnHidden
      >
        {viewDetail && (
          <LatestEvaluationPanel evaluation={viewDetail} reloadKey={drawerKey} compact />
        )}
      </Drawer>

      {/* JSON Modal */}
      <Modal
        title="完整报告 (JSON)"
        open={jsonModalVisible}
        onCancel={() => setJsonModalVisible(false)}
        footer={null}
        width={800}
      >
        <pre style={{
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6,
          maxHeight: 600, overflow: 'auto', background: '#fafafa', padding: 12, borderRadius: 6,
        }}>
          {jsonText}
        </pre>
      </Modal>
    </>
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
  const [aiWorkflowId, setAiWorkflowIdState] = useState<number | null>(selectedIds?.[0] ?? null)

  const setAiWorkflowId = (id: number | undefined) => {
    setAiWorkflowIdState(id ?? null)
  }

  // Sync with external selection when it changes
  useEffect(() => {
    if (selectedIds?.[0]) {
      setAiWorkflowIdState(selectedIds[0])
    }
  }, [selectedIds])

  const loadLatest = useCallback(() => {
    getLatestEvaluation(aiWorkflowId)
      .then(setLatest)
      .catch(() => {})
  }, [aiWorkflowId])

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
          defaultWorkflow={aiWorkflowId}
          workflows={workflows}
          defaultRange={[dayjs().subtract(30, 'day'), dayjs()]}
          onWorkflowChange={setAiWorkflowId}
        />
      </Card>

      {/* Latest Evaluation */}
      <LatestEvaluationPanel evaluation={latest as AIEvaluation} reloadKey={reloadKey} />

      {/* History Table */}
      <HistoryTable
        workflowId={aiWorkflowId}
        onEvalClick={() => {}}
        reloadKey={reloadKey}
      />
    </div>
  )
}
