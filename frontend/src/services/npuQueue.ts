import api from './api'

export type QueuePool = {
  key: string
  name: string
  capacity: number | null
  waitingJobs: number
  waitingCards: number
  runningJobs: number
  runningCards: number
  todayCardHours: number
  queueP50: number | null
  queueP90: number | null
  samples: number
  physicalRunningCards: number | null
  collectedAt: string
}

export type QueueTrendPoint = { timestamp: string; waitingCards: number; runningCards: number; waitingJobs: number; runningJobs: number }
export type QueueItem = { key: string; waitMinutes: number; run: string; title: string; job: string; repository: string; pool: string; cards: number; createdAt: string; state: 'waiting' | 'short' }
export type DailyQueuePoint = { date: string; p50: number | null; p90: number | null; samples: number; abandoned: number }
export type QueueDelaySummary = { startDate: string; endDate: string; p50: number | null; p90: number | null; samples: number }
export type DailyUsagePoint = { date: string; cardHours: number; jobs: number; failedCardHours: number; partial?: boolean }
export type PrCost = { key: string; title: string; branch: string; cardHours: number; allCardHours: number; runs: number; successfulRuns: number }

export type NpuQueueDashboard = {
  generatedAt: string
  pools: QueuePool[]
  trend: QueueTrendPoint[]
  dailyQueue: DailyQueuePoint[]
  queueDelaySummary: QueueDelaySummary
  dailyUsage: DailyUsagePoint[]
  queue: QueueItem[]
  prCosts: PrCost[]
  dataQuality: { matched_jobs: number; unmatched_jobs: number }
}

export async function getNpuQueueDashboard() {
  const response = await api.get<NpuQueueDashboard>('/npu-queue/dashboard')
  return response.data
}

export async function getNpuQueueDiagnostics() {
  const response = await api.get('/npu-queue/diagnostics')
  return response.data
}
