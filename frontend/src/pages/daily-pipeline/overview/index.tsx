import { useMemo, useState } from 'react'
import { Alert, Badge, Button, Card, Col, ConfigProvider, Empty, Progress, Radio, Row, Skeleton, Tabs } from 'antd'
import { useDailyOverview } from '../../../hooks/useDailyPipeline'
import OverviewChart from './OverviewChart'
import { overviewCategories, overviewCategoryColors, overviewMini, overviewProcessing, overviewTrend } from './overviewCharts'
import './Overview.css'

function DailyPipelineOverview() {
  const [days, setDays] = useState<7 | 14>(14)
  const [view, setView] = useState<'count' | 'rate'>('count')
  const { data, isLoading, isError, refetch } = useDailyOverview(days)
  const charts = useMemo(() => data ? {
    miniCount: overviewMini(data, 'count'), miniRate: overviewMini(data, 'rate'),
    count: overviewTrend(data, 'count'), rate: overviewTrend(data, 'rate'),
    categories: overviewCategories(data), processing: overviewProcessing(data),
  } : null, [data])
  const ranking = [...(data?.hardware ?? [])].map((item) => ({ name: item.hardware, count: item.enabled + item.disabled })).sort((a, b) => b.count - a.count)
  const processing = data?.processing.reduce((sum, item) => ({ success: sum.success + item.success, failed: sum.failed + item.failed, running: sum.running + item.running }), { success: 0, failed: 0, running: 0 })
  const summary = data?.summary
  const metrics = summary && charts ? [
    { name: 'Daily 用例', value: summary.totalCases, footer: `启用 ${summary.enabledCases} / 禁用 ${summary.totalCases - summary.enabledCases}`,
      content: <Progress percent={summary.totalCases ? summary.enabledCases / summary.totalCases * 100 : 0} showInfo={false} strokeColor="#1677ff" trailColor="#f5f5f5" strokeWidth={8} /> },
    { name: '流水线执行', value: summary.totalRuns, footer: `已结束 ${summary.completedRuns} / 执行中 ${summary.runningRuns}`,
      content: <OverviewChart option={charts.miniCount} label="每日执行次数" height={46} /> },
    { name: '流水线成功率', value: summary.successRate === null ? '—' : `${summary.successRate}%`, footer: `成功 ${summary.successfulRuns} / 已结束 ${summary.completedRuns}`,
      content: <OverviewChart option={charts.miniRate} label="成功率变化" height={46} /> },
    { name: 'open Issue', value: summary.openIssues, footer: `close ${summary.closedIssues} / 待解决用例 ${summary.unresolvedCases}`,
      content: <div className="daily-overview-issue-stat"><Badge color="#1677ff" text={`open ${summary.openIssues}`} /><Badge color="#bfbfbf" text={`close ${summary.closedIssues}`} /></div> },
  ] : []
  return <ConfigProvider theme={{ token: { colorPrimary: '#1677ff', borderRadius: 6, fontSize: 14, colorText: '#262626', colorTextSecondary: '#8c8c8c' }, components: { Button: { fontWeight: 400, primaryShadow: 'none' } } }}>
    <div className="daily-overview">
      {isError && <Alert type="error" showIcon message="概览数据加载失败" action={<Button size="small" onClick={() => void refetch()}>重试</Button>} />}
      {isLoading ? <Card><Skeleton active paragraph={{ rows: 8 }} /></Card> : data && charts && processing && <>
        <Row gutter={[24, 24]}>
          {metrics.map((item) => <Col key={item.name} xs={24} sm={12} xl={6}>
            <Card className="daily-overview-stat" bordered={false}>
              <div className="daily-overview-stat-label">{item.name}</div>
              <div className="daily-overview-stat-value">{item.value}</div>
              <div className="daily-overview-stat-chart">{item.content}</div>
              <div className="daily-overview-stat-footer">{item.footer}</div>
            </Card>
          </Col>)}
        </Row>
        <Card className="daily-overview-analysis" bordered={false} styles={{ body: { padding: 0 } }}>
          <Tabs activeKey={view} animated={false} onChange={(key) => setView(key as 'count' | 'rate')}
            items={[{ key: 'count', label: '执行次数' }, { key: 'rate', label: '成功率' }]}
            tabBarExtraContent={<Radio.Group size="small" value={days} onChange={(event) => setDays(event.target.value as 7 | 14)}
              options={[{ label: '近 7 天', value: 7 }, { label: '近 14 天', value: 14 }]} optionType="button" />} />
          <div className="daily-overview-analysis-body">
            <div className="daily-overview-main-chart"><div className="daily-overview-section-label">{view === 'count' ? '流水线执行趋势' : '流水线成功率趋势'}</div>
              <OverviewChart option={view === 'count' ? charts.count : charts.rate} label={`近 ${days} 天${view === 'count' ? '执行次数' : '成功率'}`} height={276} /></div>
            <aside className="daily-overview-ranking"><div className="daily-overview-section-label">硬件用例数量排名</div>
              {ranking.length ? <ol>{ranking.map((item, index) => <li key={item.name}>
                <span className={'daily-overview-rank-number' + (index < 3 ? ' is-leading' : '')}>{index + 1}</span>
                <span className="daily-overview-rank-label">{item.name}</span><span>{item.count}</span>
              </li>)}</ol> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无用例" />}
            </aside>
          </div>
        </Card>
        <Row gutter={[24, 24]}>
          <Col xs={24} lg={12}>
            <Card title="失败问题分类" className="daily-overview-detail-card" bordered={false}>
              {data.categories.length ? <div className="daily-overview-category-layout">
                <OverviewChart option={charts.categories} label="失败用例问题分类占比" height={254} />
                <ul className="daily-overview-category-list">{data.categories.map((item) => <li key={item.name}>
                  <Badge color={overviewCategoryColors[item.name] ?? '#bfbfbf'} text={item.name} />
                  <span className="daily-overview-category-percentage">{data.summary.failedCases ? Math.round(item.count / data.summary.failedCases * 100) : 0}%</span><span>{item.count}</span>
                </li>)}</ul>
              </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无失败用例" />}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="用例处理结果" className="daily-overview-detail-card" bordered={false}>
              <div className="daily-overview-processing-stats">
                {[{ label: '成功', count: processing.success, color: '#52c41a' }, { label: '失败', count: processing.failed, color: '#ff4d4f' }, { label: '执行中', count: processing.running, color: '#faad14' }].map((item) =>
                  <div key={item.label}><Badge color={item.color} text={item.label} /><div>{item.count}</div></div>)}
              </div>
              <OverviewChart option={charts.processing} label="Daily、E2E、UT 用例处理结果分布" height={174} />
            </Card>
          </Col>
        </Row>
      </>}
    </div>
  </ConfigProvider>
}

export default DailyPipelineOverview
