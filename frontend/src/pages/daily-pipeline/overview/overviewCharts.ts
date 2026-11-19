import type { EChartsOption } from 'echarts'
import type { DailyOverviewData } from '../../../types/dailyPipeline'

const BLUE = '#1677ff'
const textStyle = { fontFamily: 'Arial, Microsoft YaHei, sans-serif', color: '#667085', fontSize: 12 }
export const overviewCategoryColors: Record<string, string> = { 代码问题: BLUE, 版本配套问题: '#9270ca', 环境问题: '#35b7a3', 未分类: '#b6c2d2' }

export const runSeries = [
  { key: 'success', label: '成功', color: BLUE },
  { key: 'failed', label: '失败', color: '#f87171' },
  { key: 'running', label: '排队或执行中', color: '#f5bd52' },
  { key: 'other', label: '取消或跳过', color: '#b6c2d2' },
] as const
export const processingSeries = [
  { key: 'resolved', label: '已解决', color: '#35b787' },
  { key: 'unresolved', label: '未解决', color: '#f87171' },
  { key: 'running', label: '待处理或处理中', color: '#f5bd52' },
  { key: 'ended', label: '已结束', color: '#b6c2d2' },
] as const

export function overviewMini(data: DailyOverviewData, mode: 'count' | 'rate'): EChartsOption {
  return {
    animationDuration: 200, grid: { left: 5, right: 5, top: 4, bottom: 2 },
    xAxis: { type: 'category', show: false, boundaryGap: mode === 'count', data: data.trend.map((point) => point.date) },
    yAxis: { type: 'value', show: false, min: 0, max: mode === 'rate' ? 100 : undefined },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 }, axisPointer: { type: 'none' },
      valueFormatter: (value) => value == null ? '—' : mode === 'rate' ? `${value}%` : String(value) },
    series: mode === 'count' ? [{ name: '执行次数', type: 'bar', barMaxWidth: 10, itemStyle: { color: '#69a5ff', borderRadius: [2, 2, 0, 0] }, data: data.trend.map((point) => point.success + point.failed + point.running + point.other) }] :
      [{ name: '成功率', type: 'line', symbolSize: 5, itemStyle: { color: '#36af91' }, lineStyle: { width: 2 }, areaStyle: { color: '#d5f4e9' }, data: data.trend.map((point) => point.successRate) }],
  }
}

export function overviewTrend(data: DailyOverviewData, mode: 'count' | 'rate'): EChartsOption {
  return {
    animationDuration: 250, textStyle,
    tooltip: { trigger: 'axis', axisPointer: { type: mode === 'count' ? 'shadow' : 'line' },
      valueFormatter: (value) => value == null ? '—' : mode === 'rate' ? `${value}%` : String(value) },
    grid: { top: 16, left: 50, right: 16, bottom: 30 },
    xAxis: { type: 'category', boundaryGap: true, data: data.trend.map((point) => point.date.slice(5)),
      axisLine: { lineStyle: { color: '#e4eaf1' } }, axisTick: { show: false }, axisLabel: { ...textStyle, hideOverlap: true, margin: 12 } },
    yAxis: { type: 'value', minInterval: 1, min: 0, max: mode === 'rate' ? 100 : undefined,
      axisLabel: { ...textStyle, formatter: mode === 'rate' ? '{value}%' : '{value}' }, splitLine: { lineStyle: { color: '#e9eef5', type: 'dashed' } } },
    series: mode === 'count'
      ? runSeries.map((item) => ({ name: item.label, type: 'bar', stack: 'runs', barMaxWidth: 28,
        itemStyle: { color: item.color }, data: data.trend.map((point) => point[item.key]) }))
      : [{ name: '成功率', type: 'line', symbolSize: 6, itemStyle: { color: BLUE }, lineStyle: { width: 2 },
        areaStyle: { color: '#e6f4ff' }, data: data.trend.map((point) => point.successRate) }],
  }
}

export function overviewCategories(data: DailyOverviewData): EChartsOption {
  return {
    animationDuration: 250, textStyle,
    tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
    title: { text: String(data.summary.failedCases), subtext: '失败用例', left: 'center', top: '35%', itemGap: 6,
      textStyle: { color: '#1f2937', fontSize: 30, fontWeight: 500 }, subtextStyle: textStyle },
    series: [{ type: 'pie', radius: ['60%', '77%'], center: ['50%', '49%'], label: { show: false },
      itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 4 }, emphasis: { scaleSize: 3 },
      data: data.categories.map((item) => ({ name: item.name, value: item.count, itemStyle: { color: overviewCategoryColors[item.name] ?? '#bfbfbf' } })) }],
  }
}

export function overviewProcessing(data: DailyOverviewData): EChartsOption {
  return {
    animationDuration: 250, textStyle,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { top: 10, left: 66, right: 12, bottom: 24 },
    xAxis: { type: 'value', minInterval: 1, axisLabel: textStyle, splitLine: { lineStyle: { color: '#e9eef5', type: 'dashed' } } },
    yAxis: { type: 'category', inverse: true, data: data.processing.map((item) => item.type),
      axisTick: { show: false }, axisLine: { show: false }, axisLabel: textStyle },
    series: processingSeries.map((item) => ({ name: item.label, type: 'bar', stack: 'processing', barMaxWidth: 18,
      itemStyle: { color: item.color }, data: data.processing.map((row) => row[item.key]) })),
  }
}

export function overviewRunDurations(
  samples: { id: string; seconds: number }[],
  formatDuration: (seconds: number) => string,
): EChartsOption {
  const formatMinutes = (minutes: number) => formatDuration(Math.round(minutes * 60))

  return {
    animationDuration: 250, textStyle,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (value) => typeof value === 'number' ? formatMinutes(value) : '—' },
    grid: { top: 8, left: 0, right: 72, bottom: 0, containLabel: true },
    xAxis: { type: 'value', minInterval: 1, min: 0, axisLabel: { ...textStyle, formatter: '{value} 分' },
      splitLine: { lineStyle: { color: '#e9eef5', type: 'dashed' } } },
    yAxis: { type: 'category', inverse: true, data: samples.map((item) => item.id),
      axisTick: { show: false }, axisLine: { show: false }, axisLabel: textStyle },
    series: [{
      name: '耗时', type: 'bar', barMaxWidth: 18,
      itemStyle: { color: '#5b8ff9', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', distance: 8, color: '#344054',
        formatter: ({ value }) => typeof value === 'number' ? formatMinutes(value) : '—' },
      data: samples.map((item) => item.seconds / 60),
    }],
  }
}
