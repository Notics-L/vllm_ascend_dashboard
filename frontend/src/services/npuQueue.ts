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

export type QueueTrendPoint = { capturedAt: string; waitingCards: number; runningCards: number; waitingJobs: number; runningJobs: number }
export type LegacyQueueTrendPoint = { timestamp: string; waitingCards: number; runningCards: number; waitingJobs: number; runningJobs: number }
export type QueueItem = { key: string; waitMinutes: number; run: string; title: string; job: string; repository: string; pool: string; cards: number; createdAt: string; state: 'waiting' | 'short' }
export type QueueTrendPod = { name: string; namespace?: string; phase: string; npu: number; cluster_id: number; cluster_name: string; created_at?: string | null; pr_number?: number | null; pr_url?: string | null }
export type QueueTrendResponse = { pool: string | null; hours: number; points: QueueTrendPoint[]; latestCapturedAt: string | null }
export type QueueTrendDetail = { pool: string | null; capturedAt: string; waitingCards: number; runningCards: number; waitingJobs: number; runningJobs: number; pods: QueueTrendPod[] }
export type DailyQueuePoint = { date: string; p50: number | null; p90: number | null; samples: number; abandoned: number }
export type QueueDelaySummary = { startDate: string; endDate: string; p50: number | null; p90: number | null; samples: number }
export type DailyUsagePoint = { date: string; cardHours: number; jobs: number; failedCardHours: number; partial?: boolean }
export type UsagePeriod = { startDate: string | null; endDate: string | null; syncIntervalMinutes: number; initialLookbackDays: number }
export type PrCost = {
  key: string
  title: string
  branch: string
  repository: string
  kind: 'pr' | 'run'
  detailUrl: string | null
  workflowRunId: number | null
  workflowName: string | null
  runNumber: number | null
  runDisplayName: string
  workflowRef: string | null
  prNumber: number | null
  event: string | null
  triggerScope: string | null
  cardHours: number
  allCardHours: number
  runs: number
  successfulRuns: number
}

export type NpuQueueDashboard = {
  generatedAt: string
  pools: QueuePool[]
  trend: LegacyQueueTrendPoint[]
  dailyQueue: DailyQueuePoint[]
  queueDelaySummary: QueueDelaySummary
  dailyUsage: DailyUsagePoint[]
  queue: QueueItem[]
  prCosts: PrCost[]
  usagePeriod: UsagePeriod
  dataQuality: { matched_jobs: number; unmatched_jobs: number }
}

export async function getNpuQueueDashboard() {
  const response = await api.get<NpuQueueDashboard>('/npu-queue/dashboard')
  return response.data
}

export async function getNpuQueueTrend(pool?: string, hours = 24) {
  const response = await api.get<QueueTrendResponse>('/npu-queue/trend', {
    params: { pool: pool || undefined, hours },
  })
  return response.data
}

export async function getNpuQueueTrendDetail(capturedAt: string, pool?: string) {
  const response = await api.get<QueueTrendDetail>('/npu-queue/trend/detail', {
    params: { captured_at: capturedAt, pool: pool || undefined },
  })
  return response.data
}

export async function getNpuQueueDiagnostics() {
  const response = await api.get('/npu-queue/diagnostics')
  return response.data
}
