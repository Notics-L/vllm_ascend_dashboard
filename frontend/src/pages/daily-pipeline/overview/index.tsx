import { useMemo, useState } from 'react'
import { Alert, Badge, Card, Col, ConfigProvider, Empty, Progress, Radio, Row, Skeleton, Tabs } from 'antd'
import { Link } from 'react-router-dom'
import { useDailyOverview } from '../../../hooks/useDailyPipeline'
import OverviewChart from './OverviewChart'
import RecentRuns from './RecentRuns'
import RunDurationCard from './RunDurationCard'
import { overviewCategories, overviewCategoryColors, overviewMini, overviewProcessing, overviewTrend, processingSeries, runSeries } from './overviewCharts'
import './Overview.css'

const formatCount = (value: number) => value.toLocaleString('zh-CN')

function DailyPipelineOverview() {
  const [days, setDays] = useState<7 | 14>(14)
  const [view, setView] = useState<'count' | 'rate' | 'recent'>('count')
  const { data, isLoading, isError, isPlaceholderData } = useDailyOverview(days)
  const runDays = data?.trend.filter((point) => runSeries.some((item) => point[item.key] > 0)).length ?? 0
  const rateDays = data?.trend.filter((point) => point.successRate !== null).length ?? 0
  const activeView = runDays < 2 ? 'recent' : view === 'rate' && rateDays < 2 ? 'count' : view
  const runCounts = runSeries.map((item) => ({
    ...item, count: data?.trend.reduce((total, point) => total + point[item.key], 0) ?? 0,
  }))
  const charts = useMemo(() => data ? {
    miniCount: overviewMini(data, 'count'), miniRate: overviewMini(data, 'rate'),
    trend: overviewTrend(data, activeView === 'rate' ? 'rate' : 'count'),
    categories: overviewCategories(data), processing: overviewProcessing(data),
  } : null, [data, activeView])
  const summary = data?.summary
  const ranking = [...(data?.hardware ?? [])].map((item) => ({ ...item, count: item.enabled + item.disabled }))
    .sort((a, b) => b.count - a.count || a.hardware.localeCompare(b.hardware))
  const metrics = summary && charts ? [
    { name: 'Daily 用例', value: formatCount(summary.totalCases), scope: '当前',
      footer: `启用 ${formatCount(summary.enabledCases)} · 禁用 ${formatCount(summary.totalCases - summary.enabledCases)}`,
      content: <Progress percent={summary.totalCases ? summary.enabledCases / summary.totalCases * 100 : 0} showInfo={false} strokeColor="#1677ff" /> },
    { name: '流水线执行', value: formatCount(summary.totalRuns), scope: `近 ${data.trend.length} 天`,
      footer: `已结束 ${formatCount(summary.completedRuns)} · 排队或执行中 ${formatCount(summary.runningRuns)}`,
      content: runDays > 1
        ? <OverviewChart option={charts.miniCount} label="每日执行次数" height={34} />
        : <div className="daily-overview-stat-badges">{runCounts.filter((item) => item.count > 0).map((item) =>
          <Badge key={item.key} color={item.color} text={`${item.label} ${formatCount(item.count)}`} />)}</div> },
    { name: '流水线成功率', value: summary.successRate === null ? '—' : `${summary.successRate}%`, scope: `近 ${data.trend.length} 天`,
      footer: `成功 ${formatCount(summary.successfulRuns)} / 已结束 ${formatCount(summary.completedRuns)}`,
      content: rateDays > 1
        ? <OverviewChart option={charts.miniRate} label="成功率变化" height={34} />
        : summary.successRate !== null && <Progress percent={summary.successRate} showInfo={false} strokeColor="#35b787" /> },
    { name: 'Open Issue', value: formatCount(summary.openIssues), scope: '当前',
      footer: `待解决用例 ${formatCount(summary.unresolvedCases)}`,
      content: <Badge color="#8c8c8c" text={`Closed ${formatCount(summary.closedIssues)}`} /> },
  ] : []

  return <ConfigProvider theme={{ token: { colorPrimary: '#1677ff', borderRadius: 8, fontSize: 14, colorText: '#1f2937', colorTextSecondary: '#667085' } }}>
    <div className="daily-overview">
      <header className="daily-overview-header">
        <h1>流水线概览</h1>
        <Radio.Group aria-label="统计范围" size="middle" value={days} onChange={(event) => setDays(event.target.value as 7 | 14)}
          options={[{ label: '近 7 天', value: 7 }, { label: '近 14 天', value: 14 }]} optionType="button" />
      </header>
      {isError && <Alert type="error" showIcon message="概览数据加载失败" />}
      {isLoading ? <Card><Skeleton active paragraph={{ rows: 8 }} /></Card> : data && charts && <div className="daily-overview-content" aria-busy={isPlaceholderData}>
        <Row gutter={[20, 16]}>
          {metrics.map((item) => <Col key={item.name} xs={24} sm={12} xl={6}>
            <Card className="daily-overview-stat" bordered={false}>
              <div className="daily-overview-stat-heading"><span>{item.name}</span><small>{item.scope}</small></div>
              <div className="daily-overview-stat-value">{item.value}</div>
              <div className="daily-overview-stat-chart">{item.content}</div>
              <div className="daily-overview-stat-footer">{item.footer}</div>
            </Card>
          </Col>)}
        </Row>
        <Card className="daily-overview-analysis" bordered={false} styles={{ body: { padding: 0 } }}
          title={runDays < 2 ? '最近执行' : undefined}
          extra={runDays < 2 && <Link to="/daily-pipeline/collection">采集记录</Link>}>
          {runDays > 1 && <Tabs activeKey={activeView} animated={false}
            onChange={(key) => setView(key as 'count' | 'rate' | 'recent')}
            items={[
              { key: 'count', label: '执行趋势' },
              ...(rateDays > 1 ? [{ key: 'rate', label: '成功率趋势' }] : []),
              { key: 'recent', label: '最近执行' },
            ]}
            tabBarExtraContent={<Link to="/daily-pipeline/collection">采集记录</Link>} />}
          <div className="daily-overview-analysis-body">
            <div className="daily-overview-main-chart">
              {activeView === 'recent' ? <RecentRuns runs={data.recentRuns ?? []} /> : <>
                {activeView === 'count' && <div className="daily-overview-legend">
                  {runSeries.map((item) => <Badge key={item.label} color={item.color} text={item.label} />)}
                </div>}
                <OverviewChart option={charts.trend}
                  label={activeView === 'count' ? '每日流水线执行次数' : '每日流水线成功率'} height={244} />
              </>}
            </div>
            <aside className="daily-overview-ranking">
              <div className="daily-overview-section-label">硬件用例分布</div>
              {ranking.length ? <ul>{ranking.map((item) => <li key={item.hardware} className="daily-overview-rank-entry">
                <div><span>{item.hardware.toUpperCase()}</span><strong>{formatCount(item.count)}</strong></div>
                <Progress percent={item.enabled / item.count * 100} showInfo={false} size="small" strokeColor="#1677ff"
                  aria-label={`${item.hardware} 启用 ${item.enabled} 条，禁用 ${item.disabled} 条`} />
                <small>启用 {formatCount(item.enabled)} · 禁用 {formatCount(item.disabled)}</small>
              </li>)}</ul> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无用例" />}
            </aside>
          </div>
        </Card>
        <Row gutter={[20, 16]}>
          <Col xs={24} lg={12}>
            <Card title="失败用例分类" className="daily-overview-detail-card" bordered={false}>
              {data.categories.length ? <div className="daily-overview-category-layout">
                <OverviewChart option={charts.categories} label="失败用例分类占比" height={220} />
                <ul className="daily-overview-category-list">{data.categories.map((item) => <li key={item.name}>
                  <Badge color={overviewCategoryColors[item.name] ?? '#bfbfbf'} text={item.name} />
                  <span className="daily-overview-category-percentage">{Math.round(item.count / data.summary.failedCases * 100)}%</span><strong>{formatCount(item.count)}</strong>
                </li>)}</ul>
              </div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无失败用例" />}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="失败用例处理进展" className="daily-overview-detail-card" bordered={false}>
              {data.processing.length ? <>
                <div className="daily-overview-legend">
                  {processingSeries.map((item) => <Badge key={item.key} color={item.color} text={item.label} />)}
                </div>
                <OverviewChart option={charts.processing} label="按用例类型统计处理进展" height={190} />
              </> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无处理记录" />}
            </Card>
          </Col>
        </Row>
        <RunDurationCard runs={data.recentRuns ?? []} />
      </div>}
    </div>
  </ConfigProvider>
}

export default DailyPipelineOverview
