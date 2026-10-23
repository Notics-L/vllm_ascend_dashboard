import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts'
import {
  Card, Row, Col, Table, Tag, Select, Space, Button, Spin, Empty,
} from 'antd'
import {
  ReloadOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  MinusOutlined,
} from '@ant-design/icons'
import {
  listWorkflows, getComparison, fmtDuration,
  type Workflow, type WorkflowComparison,
} from '../../services/releaseManagement'

const { Option } = Select

function EChartBox({ option, height = 300 }: { option: any; height?: number }) {
  const domRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!domRef.current) return
    chartRef.current?.dispose()
    chartRef.current = echarts.init(domRef.current)
    if (chartRef.current && option) {
      chartRef.current.setOption(option)
    }
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chartRef.current?.dispose()
      chartRef.current = null
    }
  }, [option])

  return <div ref={domRef} style={{ width: '100%', height }} />
}

const chartColors = ['#5b8ff9', '#5ada7a', '#fd9a31', '#e06024', '#9f7aea', '#17a2b8', '#ff4d4f', '#fac858']

export default function ComparisonTab() {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [daysBack, setDaysBack] = useState(30)
  const [loading, setLoading] = useState(false)
  const [comparison, setComparison] = useState<WorkflowComparison[]>([])

  useEffect(() => {
    listWorkflows(false).then((w) => setWorkflows(w)).catch(() => {})
  }, [])

  useEffect(() => {
    if (selectedIds.length < 2) {
      setComparison([])
      return
    }
    setLoading(true)
    getComparison(selectedIds, daysBack)
      .then((r) => setComparison(r.comparisons))
      .catch(() => setComparison([]))
      .finally(() => setLoading(false))
  }, [selectedIds, daysBack])

  const bestIn = useMemo(() => {
    if (comparison.length < 2) return {}
    const metrics: Record<string, { key: keyof typeof comparison[0]['stats']; lowerIsBetter: boolean }> = {
      success_rate: { key: 'success_rate', lowerIsBetter: false },
      avg_exec: { key: 'avg_exec', lowerIsBetter: true },
      avg_wait: { key: 'avg_wait', lowerIsBetter: true },
      cv_exec: { key: 'cv_exec', lowerIsBetter: true },
    }
    const result: Record<string, number[]> = {}
    for (const [label, { key, lowerIsBetter }] of Object.entries(metrics)) {
      const bestWfId = comparison.reduce((bestId, c) => {
        const bestVal = comparison.find((x) => x.workflow_id === bestId)?.stats[key as keyof typeof comparison[0]['stats']]
        const curVal = c.stats[key as keyof typeof comparison[0]['stats']]
        if (bestVal === undefined || bestVal === null) return c.workflow_id
        if (curVal === undefined || curVal === null) return bestId
        return (lowerIsBetter ? curVal < bestVal : curVal > bestVal) ? c.workflow_id : bestId
      }, comparison[0].workflow_id)
      result[label] = [bestWfId]
    }
    return result
  }, [comparison])

  const renderIndicator = (value: number | null, unit: string, isHigh: boolean, wfId: number, metric: string) => {
    const isBest = bestIn[metric]?.includes(wfId)
    return (
      <span style={{ fontWeight: 700, fontSize: 20, color: isHigh ? '#52c41a' : '#1890ff' }}>
        {value ?? '-'}{unit}
        {isBest && <Tag color="green" style={{ marginLeft: 4, fontSize: 10 }}>最优</Tag>}
      </span>
    )
  }

  if (workflows.length === 0) {
    return <Spin />
  }

  return (
    <div>
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col><span style={{ fontWeight: 500 }}>对比工作流:</span></Col>
          <Col span={8}>
            <Select
              mode="multiple"
              allowClear
              placeholder="选择 2+ 个工作流"
              value={selectedIds}
              onChange={setSelectedIds}
              style={{ width: '100%' }}
              maxTagCount="responsive"
              showSearch
              filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
            >
              {workflows.map((w) => (<Option key={w.id} value={w.id} label={w.workflow_name}>{w.workflow_name}</Option>))}
            </Select>
          </Col>
          <Col><span style={{ fontWeight: 500 }}>天数:</span></Col>
          <Col span={3}>
            <Select value={daysBack} onChange={setDaysBack} style={{ width: 100 }}>
              <Option value={7}>7 天</Option>
              <Option value={14}>14 天</Option>
              <Option value={30}>30 天</Option>
              <Option value={60}>60 天</Option>
              <Option value={90}>90 天</Option>
            </Select>
          </Col>
        </Row>
      </Card>

      {selectedIds.length < 2 && (
        <Empty description="请至少选择 2 个工作流进行对比" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}

      {comparison.length >= 2 && (
        <Spin spinning={loading}>
          {/* 统计卡片 — 按 workflow 分列 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            {comparison.map((c, idx) => (
              <Col
                key={c.workflow_id}
                span={Math.floor(24 / Math.max(comparison.length, 1))}
              >
                <Card
                  size="small"
                  title={
                    <Space>
                      <span style={{
                        display: 'inline-block', width: 12, height: 12, borderRadius: '50%',
                        background: chartColors[idx % chartColors.length],
                      }} />
                      {c.workflow_name}
                    </Space>
                  }
                  styles={{ body: { padding: '16px' } }}
                >
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>总运行次数</div>
                    <div style={{ fontSize: 28, fontWeight: 700, color: chartColors[idx % chartColors.length] }}>
                      {c.stats.total}
                    </div>
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>成功率</div>
                    {renderIndicator(c.stats.success_rate, '%', c.stats.success_rate >= 80, c.workflow_id, 'success_rate')}
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>平均执行耗时</div>
                    {renderIndicator(c.stats.avg_exec, '', false, c.workflow_id, 'avg_exec')}
                    <div style={{ fontSize: 11, color: '#bfbfbf', marginTop: 2 }}>
                      {fmtDuration(c.stats.avg_exec)}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: '#8c8c8c', marginBottom: 4 }}>平均排队耗时</div>
                    {renderIndicator(c.stats.avg_wait, '', false, c.workflow_id, 'avg_wait')}
                    <div style={{ fontSize: 11, color: '#bfbfbf', marginTop: 2 }}>
                      {fmtDuration(c.stats.avg_wait)}
                    </div>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>

          {/* 共享图表 — 成功率柱状图 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card bordered={false} title="成功率对比">
                <EChartBox height={280} option={{
                  tooltip: { trigger: 'axis' },
                  grid: { left: 50, right: 20, top: 20, bottom: 30 },
                  xAxis: { type: 'category', data: comparison.map((c) => c.workflow_name.length > 20 ? c.workflow_name.slice(0, 18) + '...' : c.workflow_name) },
                  yAxis: { type: 'value', name: '%', max: 100 },
                  series: [{
                    type: 'bar',
                    data: comparison.map((c, i) => ({
                      value: c.stats.success_rate,
                      itemStyle: { color: chartColors[i % chartColors.length] },
                    })),
                    label: { show: true, position: 'top', formatter: (p: any) => `${p.value}%` },
                  }],
                }} />
              </Card>
            </Col>
            <Col span={12}>
              <Card bordered={false} title="执行耗时对比（秒）">
                <EChartBox height={280} option={{
                  tooltip: { trigger: 'axis', formatter: (p: any) => `${p[0].name}\n平均 ${fmtDuration(p[0].data.value)}` },
                  grid: { left: 60, right: 20, top: 20, bottom: 60 },
                  xAxis: { type: 'category', data: comparison.map((c) => c.workflow_name.length > 15 ? c.workflow_name.slice(0, 13) + '...' : c.workflow_name), axisLabel: { rotate: 30 } },
                  yAxis: { type: 'value', name: '秒' },
                  series: [{
                    type: 'bar',
                    data: comparison.map((c, i) => ({
                      value: c.stats.avg_exec,
                      itemStyle: { color: chartColors[i % chartColors.length] },
                    })),
                    label: { show: true, position: 'top', formatter: (p: any) => fmtDuration(p.value) },
                  }],
                }} />
              </Card>
            </Col>
          </Row>

          {/* 共享图表 — 耗时波动 vs 排队 */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card bordered={false} title="耗时波动系数对比（CV%）">
                <EChartBox height={280} option={{
                  tooltip: { trigger: 'axis' },
                  grid: { left: 50, right: 20, top: 20, bottom: 30 },
                  xAxis: { type: 'category', data: comparison.map((c) => c.workflow_name.length > 15 ? c.workflow_name.slice(0, 13) + '...' : c.workflow_name) },
                  yAxis: { type: 'value', name: '%' },
                  series: [{
                    type: 'bar',
                    data: comparison.map((c, i) => ({
                      value: c.stats.cv_exec,
                      itemStyle: { color: c.stats.cv_exec > 50 ? '#ff4d4f' : c.stats.cv_exec > 30 ? '#faad14' : '#52c41a' },
                    })),
                    label: { show: true, position: 'top', formatter: (p: any) => `${p.value}%` },
                  }],
                }} />
              </Card>
            </Col>
            <Col span={12}>
              <Card bordered={false} title="构建次数对比">
                <EChartBox height={280} option={{
                  tooltip: { trigger: 'axis' },
                  grid: { left: 50, right: 20, top: 20, bottom: 30 },
                  xAxis: { type: 'category', data: comparison.map((c) => c.workflow_name.length > 15 ? c.workflow_name.slice(0, 13) + '...' : c.workflow_name) },
                  yAxis: { type: 'value', name: '次数' },
                  series: [
                    { name: '成功', type: 'bar', stack: 't', data: comparison.map((c) => c.stats.successful), itemStyle: { color: '#52c41a' } },
                    { name: '失败', type: 'bar', stack: 't', data: comparison.map((c) => c.stats.failed), itemStyle: { color: '#ff4d4f' } },
                    { name: '取消', type: 'bar', stack: 't', data: comparison.map((c) => c.stats.cancelled), itemStyle: { color: '#d9d9d9' } },
                  ],
                  legend: { data: ['成功', '失败', '取消'], top: 0 },
                }} />
              </Card>
            </Col>
          </Row>

          {/* 详细数据表格 */}
          <Card bordered={false} title="详细对比数据">
            <Table
              dataSource={comparison.map((c) => ({ ...c.stats, workflow_id: c.workflow_id, workflow_name: c.workflow_name }))}
              rowKey="workflow_id"
              size="small"
              scroll={{ x: 1200 }}
              pagination={false}
              columns={[
                { title: '工作流', dataIndex: 'workflow_name', width: 200, fixed: 'left' as const },
                { title: '总次数', dataIndex: 'total', width: 80, align: 'right' as const, sorter: (a: any, b: any) => a.total - b.total },
                { title: '成功', dataIndex: 'successful', width: 80, align: 'right' as const, render: (v: number) => <Tag color="green">{v}</Tag> },
                { title: '失败', dataIndex: 'failed', width: 80, align: 'right' as const, render: (v: number) => <Tag color="red">{v}</Tag> },
                { title: '取消', dataIndex: 'cancelled', width: 80, align: 'right' as const, render: (v: number) => <Tag color="default">{v}</Tag> },
                {
                  title: '成功率', dataIndex: 'success_rate', width: 100, align: 'right' as const,
                  sorter: (a: any, b: any) => a.success_rate - b.success_rate,
                  render: (v: number) => {
                    const icon = v >= 90 ? <ArrowUpOutlined /> : v >= 70 ? <MinusOutlined /> : <ArrowDownOutlined />
                    const color = v >= 90 ? '#52c41a' : v >= 70 ? '#faad14' : '#ff4d4f'
                    return <span style={{ color }}>{icon} {v}%</span>
                  },
                },
                { title: '平均执行', dataIndex: 'avg_exec', width: 100, align: 'right' as const, sorter: (a: any, b: any) => a.avg_exec - b.avg_exec, render: (v: number) => fmtDuration(v) },
                { title: '最短执行', dataIndex: 'min_exec', width: 100, align: 'right' as const, render: (v: number) => fmtDuration(v) },
                { title: '最长执行', dataIndex: 'max_exec', width: 100, align: 'right' as const, render: (v: number) => fmtDuration(v) },
                { title: '波动系数', dataIndex: 'cv_exec', width: 100, align: 'right' as const, sorter: (a: any, b: any) => a.cv_exec - b.cv_exec, render: (v: number) => `${v}%` },
                { title: '平均排队', dataIndex: 'avg_wait', width: 100, align: 'right' as const, sorter: (a: any, b: any) => a.avg_wait - b.avg_wait, render: (v: number) => fmtDuration(v) },
                { title: '异常慢构建', dataIndex: 'abnormal_count', width: 100, align: 'right' as const, render: (v: number) => v || '-' },
              ]}
            />
          </Card>
        </Spin>
      )}
    </div>
  )
}
