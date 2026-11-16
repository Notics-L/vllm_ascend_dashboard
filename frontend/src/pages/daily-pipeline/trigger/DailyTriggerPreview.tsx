import { useLayoutEffect, useRef, useState } from 'react'
import { Alert, Button, Descriptions, Space, Tabs, Tag, Typography, theme } from 'antd'
import { ProTable } from '@ant-design/pro-components'
import type { ProColumns } from '@ant-design/pro-components'
import { useDailyCases } from '../../../hooks/useDailyPipeline'
import type { DailyCase } from '../../../types/dailyPipeline'
import DailyCaseTag from '../cases/DailyCaseTag'

const columns: ProColumns<DailyCase>[] = [
  { title: '用例名称', dataIndex: 'name', ellipsis: true },
  { title: '硬件类型', dataIndex: 'hardware', width: 130, render: (_, row) => <Tag color="blue">{row.hardware}</Tag> },
  { title: '测试类型', dataIndex: 'testType', width: 120 },
  {
    title: '标签',
    dataIndex: 'tags',
    width: 220,
    render: (_, row) => <Space size={4} wrap>{row.tags.map((tag) => <DailyCaseTag key={tag} label={tag} />)}</Space>,
  },
]

function measurePanelContent(content: HTMLElement, width: number) {
  const panel = content.closest<HTMLElement>('[role="tabpanel"]')
  if (!panel || window.getComputedStyle(panel).display !== 'none') {
    return content.getBoundingClientRect().height
  }
  // Measure an inactive panel outside the normal flow, then restore its styles
  // before the browser paints. Native Tabs remain responsible for visibility.
  const originalStyle = panel.getAttribute('style')
  try {
    panel.style.setProperty('position', 'absolute')
    panel.style.setProperty('visibility', 'hidden')
    panel.style.setProperty('width', `${width}px`)
    panel.style.setProperty('display', 'block', 'important')
    return content.getBoundingClientRect().height
  } finally {
    if (originalStyle === null) panel.removeAttribute('style')
    else panel.setAttribute('style', originalStyle)
  }
}

function DailyTriggerPreview() {
  const { token } = theme.useToken()
  const tabsRef = useRef<HTMLDivElement>(null)
  const dailyRef = useRef<HTMLDivElement>(null)
  const e2eRef = useRef<HTMLDivElement>(null)
  const [panelHeight, setPanelHeight] = useState(0)
  useLayoutEffect(() => {
    const host = tabsRef.current
    const daily = dailyRef.current
    const e2e = e2eRef.current
    if (!host || !daily || !e2e) return
    let frame = 0
    const measure = () => {
      const width = host.clientWidth
      if (!width) return
      const height = Math.ceil(Math.max(measurePanelContent(daily, width), measurePanelContent(e2e, width)))
      setPanelHeight((previous) => previous === height ? previous : height)
    }
    const scheduleMeasure = () => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(measure)
    }
    measure()
    const observer = new ResizeObserver(scheduleMeasure)
    observer.observe(host)
    observer.observe(daily)
    observer.observe(e2e)
    return () => {
      observer.disconnect()
      window.cancelAnimationFrame(frame)
    }
  }, [])
  const panelStyle = { minHeight: panelHeight || undefined, background: token.colorBgContainer, borderRadius: token.borderRadiusLG }
  const { data: cases = [], isLoading, isFetching, isError, refetch } = useDailyCases()
  const enabledCases = cases.filter((item) => item.status === '启用')
  return (
    <section className="daily-trigger-preview" aria-label="流水线触发内容预览">
      <Typography.Text>内容预览</Typography.Text>
      <div ref={tabsRef}>
      <Tabs size="small" animated={false} destroyOnHidden={false} defaultActiveKey="daily" items={[
        {
          key: 'daily',
          forceRender: true,
          style: panelStyle,
          label: 'Daily 用例',
          children: <div ref={dailyRef}>{isError ? <Alert type="error" showIcon message="Daily 用例加载失败" action={
            <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
          } /> : <ProTable<DailyCase>
              search={false}
              headerTitle={false}
              cardProps={false}
              options={false}
              toolBarRender={false}
              size="small"
              rowKey="id"
              columns={columns}
              dataSource={enabledCases}
              loading={isLoading || isFetching}
              pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false }}
              scroll={{ x: 740 }}
            />}</div>,
        },
        {
          key: 'e2e',
          forceRender: true,
          style: panelStyle,
          label: 'run-all-tests',
          children: <div ref={e2eRef}>
            <Descriptions bordered size="small" column={1} colon items={[
            { key: 'scope', label: '执行范围', children: <Space direction="vertical" size={0}><span>tests/ut</span><span>tests/e2e/pull_request</span></Space> },
            ]} />
          </div>,
        },
      ]} />
      </div>
    </section>
  )
}

export default DailyTriggerPreview
