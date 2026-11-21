import { useEffect, useRef } from 'react'
import { message } from 'antd'

interface QueryErrorState {
  isError: boolean
  errorUpdatedAt: number
}

export function useDailyQueryError({ isError, errorUpdatedAt }: QueryErrorState, text: string, enabled = true) {
  const lastErrorAt = useRef(errorUpdatedAt)
  useEffect(() => {
    if (errorUpdatedAt === lastErrorAt.current) return
    lastErrorAt.current = errorUpdatedAt
    if (enabled && isError) {
      void message.error({ key: `daily-query-${text}`, content: text })
    }
  }, [errorUpdatedAt, isError, text, enabled])
}
