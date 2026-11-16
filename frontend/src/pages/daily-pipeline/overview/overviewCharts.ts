import type { EChartsOption } from 'echarts'
import type { DailyOverviewData } from '../../../types/dailyPipeline'

const BLUE = '#1677ff'
const textStyle = { fontFamily: 'Arial, Microsoft YaHei, sans-serif', color: '#8c8c8c', fontSize: 12 }
export const overviewCategoryColors: Record<string, string> = { 代码问题: '#5b8ff9', 版本配套问题: '#9270ca', 环境问题: '#5ad8a6', 未分类: '#bfbfbf' }

export function overviewMini(data: DailyOverviewData, mode: 'count' | 'rate'): EChartsOption {
  return {
    animationDuration: 300, grid: { left: 0, right: 0, top: 3, bottom: 0 },
    xAxis: { type: 'category', show: false, boundaryGap: mode === 'count', data: data.trend.map((point) => point.date) },
    yAxis: { type: 'value', show: false, min: 0, max: mode === 'rate' ? 100 : undefined },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 }, axisPointer: { type: 'none' } },
    series: mode === 'count' ? [{ name: '执行次数', type: 'bar', barMaxWidth: 11, itemStyle: { color: BLUE }, data: data.trend.map((point) => point.success + point.failed + point.running) }] :
      [{ name: '成功率', type: 'line', smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#975fe4' }, areaStyle: { color: '#d9c7f2', opacity: .65 }, data: data.trend.map((point) => point.successRate) }],
  }
}

export function overviewTrend(data: DailyOverviewData, mode: 'count' | 'rate'): EChartsOption {
  return {
    animationDuration: 350, textStyle,
    tooltip: { trigger: 'axis', axisPointer: { type: mode === 'count' ? 'shadow' : 'line' }, textStyle: { color: '#262626', fontSize: 13 } },
    grid: { top: 22, left: 38, right: 20, bottom: 34 },
    xAxis: { type: 'category', data: data.trend.map((point) => point.date.slice(5)), axisLine: { lineStyle: { color: '#e8e8e8' } }, axisTick: { show: false }, axisLabel: { ...textStyle, interval: data.trend.length > 7 ? 1 : 0, margin: 14 } },
    yAxis: { type: 'value', minInterval: 1, min: 0, max: mode === 'rate' ? 100 : undefined,
      axisLabel: { ...textStyle, formatter: mode === 'rate' ? '{value}%' : '{value}' }, splitLine: { lineStyle: { color: '#f0f0f0' } } },
    series: mode === 'count' ? [{ name: '执行次数', type: 'bar', barMaxWidth: 30, itemStyle: { color: '#5b8ff9' }, data: data.trend.map((point) => point.success + point.failed + point.running) }] :
      [{ name: '成功率', type: 'line', smooth: true, symbolSize: 5, itemStyle: { color: BLUE }, lineStyle: { width: 2 }, areaStyle: { color: '#e6f4ff', opacity: .55 }, data: data.trend.map((point) => point.successRate) }],
  }
}

export function overviewCategories(data: DailyOverviewData): EChartsOption {
  const total = data.categories.reduce((sum, item) => sum + item.count, 0)
  return {
    animationDuration: 350, textStyle,
    tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
    title: { text: String(total), subtext: '失败用例', left: 'center', top: '35%', itemGap: 6,
      textStyle: { color: '#262626', fontSize: 30, fontWeight: 400 }, subtextStyle: textStyle },
    series: [{ type: 'pie', radius: ['58%', '76%'], center: ['50%', '49%'], label: { show: false },
      itemStyle: { borderColor: '#fff', borderWidth: 2 }, emphasis: { scaleSize: 3 },
      data: data.categories.map((item) => ({ name: item.name, value: item.count, itemStyle: { color: overviewCategoryColors[item.name] ?? '#bfbfbf' } })) }],
  }
}

export function overviewProcessing(data: DailyOverviewData): EChartsOption {
  return {
    animationDuration: 350, textStyle,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { top: 18, left: 76, right: 12, bottom: 24 },
    xAxis: { type: 'value', minInterval: 1, axisLabel: textStyle, splitLine: { lineStyle: { color: '#f0f0f0' } } },
    yAxis: { type: 'category', data: data.processing.map((item) => item.type), axisTick: { show: false }, axisLine: { show: false }, axisLabel: { ...textStyle, color: '#595959' } },
    series: [
      { name: '成功', type: 'bar', stack: 'processing', barWidth: 15, itemStyle: { color: '#52c41a' }, data: data.processing.map((item) => item.success) },
      { name: '失败', type: 'bar', stack: 'processing', itemStyle: { color: '#ff4d4f' }, data: data.processing.map((item) => item.failed) },
      { name: '执行中', type: 'bar', stack: 'processing', itemStyle: { color: '#faad14' }, data: data.processing.map((item) => item.running) },
    ],
  }
}
