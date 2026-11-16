import type { ReactElement } from 'react'
import { Button } from 'antd'
import type { TableProps } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import './DailyTableRefresh.css'

type PaginationConfig = Exclude<TableProps['pagination'], undefined>

export function dailyTableRefresh(refresh: () => unknown, refreshing: boolean, pagination: PaginationConfig) {
  const refreshButton = <Button className="daily-table-refresh-button" type="text" size="small" aria-label="刷新表格" icon={<ReloadOutlined />} loading={refreshing} onClick={() => { if (!refreshing) void refresh() }} />
  return {
    options: false as const,
    pagination: pagination === false ? false as const : {
      ...pagination,
      showTotal: (total: number, range: [number, number]) => <span className="daily-table-pagination-total">
        {refreshButton}
        <span>{pagination.showTotal ? pagination.showTotal(total, range) : `第 ${range[0]}–${range[1]} 条，共 ${total} 条`}</span>
      </span>,
    },
    tableViewRender: (_props: unknown, table: ReactElement) => {
      return <div className="daily-table-refresh-layout">
        {table}
        <div className="daily-table-refresh-action">{refreshButton}</div>
      </div>
    },
  }
}
