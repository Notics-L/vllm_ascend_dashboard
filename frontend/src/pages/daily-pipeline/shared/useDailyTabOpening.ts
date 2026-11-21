import { useLayoutEffect, useState } from 'react'

const TAB_LOADING_FEEDBACK_MS = 350

export function useDailyTabOpening(active: boolean) {
  const [opening, setOpening] = useState(active)
  useLayoutEffect(() => {
    setOpening(active)
    if (!active) return
    // Keep the mask visible through fast responses on tab entry.
    const timer = window.setTimeout(() => setOpening(false), TAB_LOADING_FEEDBACK_MS)
    return () => window.clearTimeout(timer)
  }, [active])
  return opening
}
