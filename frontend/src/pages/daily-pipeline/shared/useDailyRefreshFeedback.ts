import { useState } from 'react'

const MIN_REFRESH_FEEDBACK_MS = 400

export function useDailyRefreshFeedback(refresh: () => unknown) {
  const [pending, setPending] = useState(false)

  const run = async () => {
    setPending(true)
    const visible = new Promise<void>((resolve) => window.setTimeout(resolve, MIN_REFRESH_FEEDBACK_MS))
    try {
      await refresh()
    } finally {
      await visible
      setPending(false)
    }
  }

  return { pending, run }
}
