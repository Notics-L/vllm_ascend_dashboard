import { QueryClient, QueryClientProvider, QueryObserver, useQuery } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import { getDailyNotificationRecords } from '../../../services/dailyPipeline'
import NotificationConfig from './NotificationConfig'

vi.mock('@tanstack/react-query', async (load) => {
  const query = await load<typeof import('@tanstack/react-query')>()
  return { ...query, useQuery: vi.fn(query.useQuery) }
})

vi.mock('../../../services/dailyPipeline', async (load) => ({
  ...await load<typeof import('../../../services/dailyPipeline')>(),
  getDailyNotificationRecords: vi.fn(),
}))

it('shows consumption history without token configuration or recipient management', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['daily-pipeline', 'notification-records', 1, 10, undefined, undefined], {
    items: [{ id: '27', status: 'completed', createdAt: '2026-11-17T11:59:00Z', githubName: 'author', name: 'Test User', content: 'Daily test notification',
      consumer: 'consumer', claimedAt: '2026-11-17T12:00:00Z', confirmedAt: '2026-11-17T12:01:00Z', result: 'sent' }],
    total: 1, page: 1, pageSize: 10,
  })
  const originalError = console.error
  const error = vi.spyOn(console, 'error').mockImplementation((message, ...args) => {
    if (typeof message === 'string' && message.includes('useLayoutEffect does nothing on the server')) return
    originalError(message, ...args)
  })
  try {
    const html = renderToStaticMarkup(<QueryClientProvider client={client}><NotificationConfig active /></QueryClientProvider>)
    const text = html.replace(/<[^>]*>/g, '')
    for (const label of ['领取时间', 'GitHub 用户', '通知内容', '消费状态', '已消费', '消费结果', '确认时间', '发送成功']) expect(text).toContain(label)
    expect(text).toContain('Daily test notification')
    for (const removed of ['Token', '保存', '新增', '删除', '工号']) expect(text).not.toContain(removed)
    expect(html).not.toContain('type="password"')
    expect(html).toContain('aria-label="刷新表格"')
  } finally { error.mockRestore(); client.clear() }
})


it('fetches on each notification tab activation while retaining the last response during loading', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } })
  const page = { items: [], total: 0, page: 1, pageSize: 10 }
  vi.mocked(getDailyNotificationRecords).mockResolvedValue(page)
  const originalError = console.error
  const error = vi.spyOn(console, 'error').mockImplementation((message, ...args) => {
    if (typeof message === 'string' && message.includes('useLayoutEffect does nothing on the server')) return
    originalError(message, ...args)
  })
  const optionsFor = (active: boolean) => {
    vi.mocked(useQuery).mockClear()
    renderToStaticMarkup(<QueryClientProvider client={client}><NotificationConfig active={active} /></QueryClientProvider>)
    return vi.mocked(useQuery).mock.calls[0][0]
  }
  try {
    const inactiveOptions = optionsFor(false)
    const activeOptions = optionsFor(true)
    const observer = new QueryObserver(client, inactiveOptions)
    const unsubscribe = observer.subscribe(() => undefined)
    try {
      expect(getDailyNotificationRecords).not.toHaveBeenCalled()
      observer.setOptions(activeOptions)
      expect(observer.getCurrentResult().isFetching).toBe(true)
      await vi.waitFor(() => expect(observer.getCurrentResult().data).toEqual(page))
      observer.setOptions(inactiveOptions)

      observer.setOptions(activeOptions)
      expect(observer.getCurrentResult().isFetching).toBe(true)
      expect(observer.getCurrentResult().data).toEqual(page)
      await vi.waitFor(() => expect(observer.getCurrentResult().isFetching).toBe(false))
      expect(getDailyNotificationRecords).toHaveBeenCalledTimes(2)
    } finally {
      unsubscribe()
    }
  } finally {
    error.mockRestore()
    vi.mocked(getDailyNotificationRecords).mockReset()
    client.clear()
  }
})
