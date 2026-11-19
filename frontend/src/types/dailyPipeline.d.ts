export interface DailyPipelineSchedule {
  cron: string | null
  timeZone: string
  executionTimes: string[]
  timeOptions: string[]
}

export interface DailyCase {
  autoDisabled?: boolean
  manualEnabled?: boolean
  processingFailureId?: string | null
  nightlyCaseId?: string
  id: string
  name: string
  hardware: string
  testType: string
  tags: string[]
  status: '启用' | '禁用'
  branch: string
  successCount?: number
  failureCount?: number
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
  result: '触发成功' | '触发失败'
}

export interface DailyTriggerHistoryDetail extends DailyTriggerHistoryItem {
  repository: string
  sourceBranch: string
  runId: number | null
  workflow: string
  cron: string | null
  dailyCases: Pick<DailyCase, 'id' | 'name' | 'hardware' | 'testType'>[]
  logs: { time: string; level: 'INFO' | 'ERROR'; message: string }[]
}

export interface DailyTriggerHistoryPage {
  items: DailyTriggerHistoryItem[]
  total: number
}

export type DailyExecutionResult = '排队中' | '执行中' | '成功' | '失败' | '已取消' | '已跳过' | '未获取'

export interface DailyCollectionRun {
  id: string
  runId: number
  startedAt: string | null
  endedAt: string | null
  triggerHistoryId: string | null
  issue: { number: number; state: 'open' | 'closed' } | null
  lastCollectedAt: string | null
  result: DailyExecutionResult
}

export type DailyCollectionCaseGroup = 'Daily' | 'E2E' | 'UT' | 'UT/E2E'

export interface DailyCollectionNode {
  id: string
  parentId: string | null
  nodeType: 'workflow_run' | 'matrix' | 'workflow_call' | 'job' | 'test_job'
  sourceId: string | null
  name: string
  result: DailyExecutionResult
  group?: DailyCollectionCaseGroup | null
  hardware?: string | null
  vllmVersion?: 'main' | 'release' | null
}

export interface DailyCollectionDetail extends DailyCollectionRun {
  nodes?: DailyCollectionNode[]
  jobs: {
    id: string
    name: string
    result: DailyExecutionResult
    group: 'run-all-tests' | 'other'
    hardware?: string
    vllmVersion?: 'main' | 'release'
  }[]
  cases: {
    id: string
    jobId: string
    name: string
    group: DailyCollectionCaseGroup
    hardware: string
    vllmVersion?: 'main' | 'release'
    result: DailyExecutionResult
  }[]
}

export interface DailyFailedCase extends Omit<DailyCollectionDetail['cases'][number], 'id' | 'jobId' | 'result'> {
  failureLog?: string | null
  id: string
  caseId: string
  result: '失败'
  runId: number
  collectedAt: string | null
  processingProgress: '待处理' | '处理中' | '已解决' | '未解决' | '已结束'
  problemCategory: string | null
  versionFlowId?: string | null
  versionFlowSize?: number | null
  issue: DailyCollectionRun['issue']
}

export interface DailyIssueDetail {
  url: string
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
  stageCode?: string
  problemCategory?: DailyManualNodeInput["result"]["category"] | null
  canManuallyComplete?: boolean
  resultData?: DailyManualNodeInput["result"] & { original_pr_url?: string } | null
  startedAt?: string | null
  status: 'pending' | 'waiting' | 'running' | 'completed' | 'failed' | 'ended'
  result: string | null
  updatedAt: string | null
  records: { id: string; author: string; createdAt: string | null; content: string; source?: "auto" | "manual"; status?: DailyProcessingStage["status"]; resultData?: DailyManualNodeInput["result"] & { original_pr_url?: string } | null }[]
}

export interface DailyFailedCaseDetail extends DailyFailedCase {
  processingFlow: {
    currentStageId: DailyProcessingStage['id'] | null
    stages: DailyProcessingStage[]
    versionFlow?: DailyVersionFlow | null
  }
}

export interface DailyVersionFlow {
  failureId: string
  status: 'waiting_resolution' | 'waiting_pr' | 'waiting_build' | 'building' | 'build_failed' | 'resolved'
  message: string
  triggerUrl: string | null
  sourceBranch: string | null
  memberCount: number
  updatedAt: string | null
  canEdit: boolean
  resolution: {
    note?: string
    references?: Partial<Record<'repair' | 'revert', { pr_url: string; pr_number: number }>>
    merged_pr?: { pr_url: string; pr_number: number }
    merged_kind?: 'repair' | 'revert'
  }
  build: { url?: string; run_id?: number; conclusion?: string | null }
}

export interface DailyVersionFlowInput {
  action: 'external_recovered' | 'track_pr' | 'track_build'
  repair_url?: string
  revert_url?: string
  build_url?: string
  message?: string
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
  trend: { date: string; success: number; failed: number; running: number; other: number; successRate: number | null }[]
  recentRuns: (Pick<DailyCollectionRun, 'id' | 'startedAt' | 'endedAt' | 'result'> & { issue: { number: number } | null })[]
  categories: { name: string; count: number }[]
  processing: { type: string; resolved: number; unresolved: number; running: number; ended: number }[]
  hardware: { hardware: string; enabled: number; disabled: number }[]
}


export interface DailyManualNodeInput {
  success: boolean
  message: string
  result: {
    category?: 'code' | 'version' | 'environment'
    pr_repository?: string
    pr_url?: string
    pr_number?: number
    run_id?: number
  }
}


export interface DailyNotificationRecipient {
  id: number
  githubName: string
  name: string
}

export interface DailyNotificationRecipientInput {
  githubName: string
  name: string
  account?: string
}


export type DailyNotificationConsumptionStatus = 'pending' | 'processing' | 'completed' | 'ended'

export type DailyNotificationConsumptionResult = 'waiting' | 'sent' | 'failed' | 'unknown' | 'ended'

export interface DailyNotificationConsumptionRecord {
  id: string
  notificationName: string
  status: DailyNotificationConsumptionStatus
  createdAt: string | null
  githubName: string
  name: string
  content: string
  consumer: string
  claimedAt: string | null
  confirmedAt: string | null
  result: DailyNotificationConsumptionResult
}

export interface DailyNotificationConsumptionPage {
  items: DailyNotificationConsumptionRecord[]
  total: number
  page: number
  pageSize: number
}
