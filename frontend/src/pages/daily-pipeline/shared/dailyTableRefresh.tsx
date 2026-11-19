import { createContext, useContext, type ReactElement } from 'react'
import { Button, Spin } from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useDailyRefreshFeedback } from './useDailyRefreshFeedback'
import './DailyTableRefresh.css'

type PaginationConfig = Exclude<TableProps['pagination'], undefined>
const RefreshContext = createContext<(() => void) | undefined>(undefined)

function RefreshButton({ disabled }: { disabled: boolean }) {
  const refresh = useContext(RefreshContext)
  return <Button className="daily-table-refresh-button" type="text" size="small" aria-label="刷新表格"
    icon={<ReloadOutlined />} aria-disabled={disabled || !refresh} onClick={disabled ? undefined : refresh} />
}

function RefreshLayout({ table, refresh, refreshing }: {
  table: ReactElement
  refresh: () => unknown
  refreshing: boolean
}) {
  const { pending, run } = useDailyRefreshFeedback(refresh)
  const busy = pending || refreshing
  return (
    <RefreshContext.Provider value={busy ? undefined : run}>
      <div className="daily-table-refresh-layout" aria-busy={busy}>
        {table}
        <div className="daily-table-refresh-action"><RefreshButton disabled={busy} /></div>
        {busy && <div className="daily-table-loading-mask" role="status" aria-label="正在加载表格"><Spin /></div>}
      </div>
    </RefreshContext.Provider>
  )
}

export function dailyTableRefresh(refresh: () => unknown, refreshing: boolean, pagination: PaginationConfig) {
  return {
    options: false as const,
    pagination: pagination === false ? false as const : {
      ...pagination,
      showTotal: (total: number, range: [number, number]) => <span className="daily-table-pagination-total">
        <RefreshButton disabled={refreshing} />
        <span>{pagination.showTotal ? pagination.showTotal(total, range) : `第 ${range[0]}–${range[1]} 条，共 ${total} 条`}</span>
      </span>,
    },
    tableViewRender: (_props: unknown, table: ReactElement) => (
      <RefreshLayout table={table} refresh={refresh} refreshing={refreshing} />
    ),
  }
}
