import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer])

function OverviewChart({ option, label, height = 260 }: { option: EChartsOption; label: string; height?: number }) {
  const element = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.EChartsType>()
  useEffect(() => {
    if (!element.current) return
    const instance = echarts.init(element.current)
    chart.current = instance
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(element.current)
    return () => { observer.disconnect(); instance.dispose(); chart.current = undefined }
  }, [])
  useEffect(() => {
    chart.current?.setOption({ ...option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches }, { notMerge: true })
  }, [option])
  return <div ref={element} role="img" aria-label={label} className="daily-overview-chart" style={{ height }} />
}

export default OverviewChart
