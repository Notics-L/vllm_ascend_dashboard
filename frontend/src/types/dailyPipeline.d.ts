export interface DailyPipelineSchedule {
  cron: string | null
  timeZone: string
  executionTimes: string[]
  timeOptions: string[]
}

export interface DailyCase {
  nightlyCaseId?: string
  id: string
  name: string
  hardware: string
  testType: string
  tags: string[]
  status: '启用' | '禁用'
  branch: string
  executionTimes: string[]
}

export type DailyCaseInput = Pick<DailyCase, 'name' | 'hardware' | 'testType' | 'tags' | 'status'>
export type DailyCaseUpdate = Pick<DailyCase, 'tags' | 'status'>

export interface NightlyCase extends Omit<DailyCase, 'executionTimes' | 'nightlyCaseId'> {
  dailyCaseId?: string
}

export interface DailySelectionResult {
  added: number
  removed: number
}

export interface DailyTriggerConfig {
  cron: string | null
  githubTokenConfigured: boolean
}

export interface DailyTriggerConfigInput {
  cron: string
  githubToken?: string
}

export interface DailyTriggerHistoryItem {
  id: string
  triggeredAt: string
  event: '定时触发' | '手动触发'
  result: '触发成功' | '触发失败' | '触发中'
}

export interface DailyTriggerHistoryDetail extends DailyTriggerHistoryItem {
  workflow: string
  cron: string
  dailyCases: Pick<DailyCase, 'id' | 'name' | 'hardware' | 'testType'>[]
  logs: { time: string; level: 'INFO' | 'ERROR'; message: string }[]
}

export interface DailyTriggerHistoryPage {
  items: DailyTriggerHistoryItem[]
  total: number
}

export interface DailyCollectionRun {
  id: string
  runId: number
  startedAt: string
  endedAt: string | null
  triggerHistoryId: string
  issue: { number: number; state: 'open' | 'closed' } | null
  lastCollectedAt: string | null
  status: '等待采集' | '采集中' | '已完成' | '采集失败'
  result: '执行中' | '成功' | '失败' | '已取消'
  collectedCases: number
  expectedCases: number
  failedCases: number
}

export interface DailyCollectionDetail extends DailyCollectionRun {
  branch: string
  commit: string
  jobs: {
    id: string
    name: string
    result: '成功' | '失败' | '执行中' | '排队中' | '已跳过' | '已取消' | '未获取'
  }[]
  children: {
    workflow: string
    runId: number | null
    association: '已关联' | '待关联'
    result: '执行中' | '成功' | '失败' | '未获取'
  }[]
  cases: {
    id: string
    name: string
    group: 'Daily' | 'E2E' | 'UT'
    hardware: string
    vllmVersion?: 'main' | 'release'
    result: '成功' | '失败' | '执行中'
    failureLog: string | null
  }[]
  logs: string[]
}

export interface DailyFailedCase extends Omit<DailyCollectionDetail['cases'][number], 'id' | 'result'> {
  id: string
  caseId: string
  result: '失败'
  runId: number
  collectedAt: string | null
  processingProgress: '待处理' | '处理中' | '已解决' | '未解决'
  problemCategory: string | null
  issue: DailyCollectionRun['issue']
}

export interface DailyIssueDetail {
  number: number
  title: string
  state: 'open' | 'closed'
  body: string
  comments: {
    id: string
    author: string
    createdAt: string
    body: string
  }[]
}

export type DailyIssueItem = Pick<DailyIssueDetail, 'number' | 'title' | 'state'> & {
  runId: number | null
  processingProgress: DailyFailedCase['processingProgress'] | null
}

export interface DailyProcessingStage {
  id: string
  name: string
  status: 'pending' | 'waiting' | 'running' | 'completed' | 'failed'
  result: string | null
  updatedAt: string | null
  records: { id: string; author: string; createdAt: string | null; content: string }[]
}

export interface DailyFailedCaseDetail extends DailyFailedCase {
  processingFlow: {
    currentStageId: DailyProcessingStage['id'] | null
    stages: DailyProcessingStage[]
  }
}

export interface DailyOverviewData {
  summary: {
    totalCases: number
    enabledCases: number
    totalRuns: number
    successfulRuns: number
    runningRuns: number
    completedRuns: number
    successRate: number | null
    failedCases: number
    unresolvedCases: number
    openIssues: number
    closedIssues: number
  }
  trend: { date: string; success: number; failed: number; running: number; successRate: number | null }[]
  categories: { name: string; count: number }[]
  processing: { type: string; success: number; failed: number; running: number }[]
  hardware: { hardware: string; enabled: number; disabled: number }[]
}
