import type { DailyNotificationConsumptionResult, DailyNotificationConsumptionStatus } from '../types/dailyPipeline'
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { rollbackDailyProcessing, shareDailyProcessingFlow, updateDailyVersionFlow } from '../services/dailyPipeline'
import { getDailyFailureNotificationConfig, saveDailyFailureNotificationConfig, getDailyNotificationRecipients, saveDailyNotificationRecipient, deleteDailyNotificationRecipient, getDailyNotificationRecords, retryDailyProcessing, manuallyCompleteDailyNode, getDailyOverview, closeDailyIssues, deleteDailyIssues, addDailyCasesFromNightly, deleteDailyCases, deleteDailyTriggerHistory, deleteDailyCollectionRuns, getDailyCases, getDailySchedule, getDailyCollectionRuns, getDailyCollectionDetail, getDailyFailedCases, getDailyFailedCase, getDailyIssueDetail, getDailyIssues, recollectDailyRun, getDailyTriggerConfig, getDailyTriggerHistory, getDailyTriggerHistoryDetail, previewDailySchedule, saveDailyTriggerConfig, testDailyTriggerConnection, triggerDailyPipeline, saveDailySchedule, saveDailyCaseSelection, updateDailyCase, updateDailyCaseStatuses } from '../services/dailyPipeline'

const DAILY_CASES_QUERY_KEY = ['daily-pipeline', 'cases']

type DailyListQueryOptions = { enabled?: boolean; staleTime?: number }

export const useDailyCases = (
  isDaily = true,
  options?: DailyListQueryOptions,
) => useQuery({
  queryKey: [...DAILY_CASES_QUERY_KEY, isDaily],
  queryFn: () => getDailyCases(isDaily),
  retry: false,
  ...options,
})

export const useDailyCaseMutations = () => {
  const queryClient = useQueryClient()
  const refreshCases = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: DAILY_CASES_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: ['daily-pipeline', 'overview'] }),
  ])

  return {
    saveSelection: useMutation({ mutationFn: saveDailyCaseSelection, onSuccess: refreshCases }),
    addNightlyCases: useMutation({ mutationFn: addDailyCasesFromNightly, onSuccess: refreshCases }),
    updateCase: useMutation({ mutationFn: updateDailyCase, onSuccess: refreshCases }),
    updateStatuses: useMutation({ mutationFn: updateDailyCaseStatuses, onSuccess: refreshCases }),
    deleteCases: useMutation({ mutationFn: deleteDailyCases, onSuccess: refreshCases }),
  }
}

const DAILY_SCHEDULE_QUERY_KEY = ['daily-pipeline', 'schedule']

export const useDailySchedule = () => useQuery({
  queryKey: DAILY_SCHEDULE_QUERY_KEY,
  queryFn: getDailySchedule,
  retry: false,
  refetchOnReconnect: false,
})

export const useSaveDailySchedule = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: saveDailySchedule,
    onSuccess: async (schedule) => {
      queryClient.setQueryData(DAILY_SCHEDULE_QUERY_KEY, schedule)
      await queryClient.invalidateQueries({ queryKey: DAILY_CASES_QUERY_KEY })
    },
  })
}

const DAILY_TRIGGER_CONFIG_QUERY_KEY = ['daily-pipeline', 'trigger-config']

export const useDailyTriggerConfig = (active: boolean) => useQuery({
  queryKey: DAILY_TRIGGER_CONFIG_QUERY_KEY,
  queryFn: getDailyTriggerConfig,
  enabled: active,
  staleTime: 0,
})

export const useSaveDailyTriggerConfig = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: saveDailyTriggerConfig,
    onSuccess: async (config) => {
      queryClient.setQueryData(DAILY_TRIGGER_CONFIG_QUERY_KEY, config)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: DAILY_SCHEDULE_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: DAILY_CASES_QUERY_KEY }),
      ])
    },
  })
}

export const useTestDailyTriggerConnection = () => useMutation({
  mutationFn: testDailyTriggerConnection,
})

export const useTriggerDailyPipeline = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: triggerDailyPipeline,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['daily-pipeline', 'trigger-history'] }),
  })
}

const CRON_PREVIEW_DELAY_MS = 200

export const useDailySchedulePreview = (cron: string | undefined, active: boolean) => {
  const normalized = cron?.trim().replace(/\s+/g, ' ') ?? ''
  const [debouncedCron, setDebouncedCron] = useState('')
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedCron(normalized), CRON_PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timeout)
  }, [normalized])
  return useQuery({
    queryKey: ['daily-pipeline', 'schedule-preview', normalized],
    queryFn: ({ signal }) => previewDailySchedule(normalized, signal),
    enabled: active && Boolean(normalized) && normalized === debouncedCron,
    staleTime: 0,
    retry: false,
    refetchInterval: 60_000,
  })
}

export const useDailyTriggerHistory = (page: number, pageSize: number, enabled: boolean) => useQuery({
  queryKey: ['daily-pipeline', 'trigger-history', page, pageSize],
  queryFn: () => getDailyTriggerHistory(page, pageSize),
  retry: false,
  enabled,
  staleTime: 0,
})

export const useDailyTriggerHistoryDetail = (id: string) => useQuery({
  queryKey: ['daily-pipeline', 'trigger-history-detail', id],
  queryFn: () => getDailyTriggerHistoryDetail(id),
  retry: false,
})

export const useDeleteDailyTriggerHistory = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteDailyTriggerHistory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['daily-pipeline', 'trigger-history'] }),
  })
}

export const useDailyCollectionRuns = () => useQuery({
  queryKey: ['daily-pipeline', 'collection-runs'], queryFn: getDailyCollectionRuns,
  staleTime: 0,
  retry: false,
})

export const useDailyCollectionDetail = (id: string | null) => useQuery({
  queryKey: ['daily-pipeline', 'collection-detail', id],
  queryFn: () => getDailyCollectionDetail(id!), enabled: Boolean(id),
  retry: false,
})

export const useRecollectDailyRun = () => {
  const client = useQueryClient()
  return useMutation({ mutationFn: recollectDailyRun, onSuccess: async (_result, id) => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'overview'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'collection-runs'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'collection-detail', id] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-cases'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-case-detail'] }),
    ])
  } })
}

export const useDeleteDailyCollectionRuns = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: deleteDailyCollectionRuns,
    onSuccess: () => Promise.all([
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'overview'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'collection-runs'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'collection-detail'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-cases'] }),
      client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-case-detail'] }),
    ]),
  })
}

export const useDailyFailedCases = (options?: DailyListQueryOptions) => useQuery({
  queryKey: ['daily-pipeline', 'failed-cases'],
  queryFn: getDailyFailedCases,
  retry: false,
  ...options,
})

export const useDailyIssueDetail = (number: number, enabled: boolean) => useQuery({
  queryKey: ['daily-pipeline', 'issue-detail', number],
  queryFn: () => getDailyIssueDetail(number),
  enabled,
})

export const useDailyIssues = (options?: DailyListQueryOptions) => useQuery({
  queryKey: ['daily-pipeline', 'issues'],
  queryFn: getDailyIssues,
  retry: false,
  ...options,
})

export const useDailyFailedCase = (id: string | undefined) => useQuery({
  queryKey: ['daily-pipeline', 'failed-case-detail', id],
  queryFn: () => getDailyFailedCase(id!),
  enabled: Boolean(id),
})

export const useDailyIssueMutations = () => {
  const client = useQueryClient()
  const refresh = () => Promise.all([
    'overview', 'issues', 'issue-detail', 'collection-runs', 'collection-detail', 'failed-cases', 'failed-case-detail',
  ].map((key) => client.invalidateQueries({ queryKey: ['daily-pipeline', key] })))
  return {
    closeIssues: useMutation({ mutationFn: closeDailyIssues, onSuccess: refresh }),
    deleteIssues: useMutation({ mutationFn: deleteDailyIssues, onSuccess: refresh }),
  }
}

export const useDailyVersionFlowMutations = () => {
  const client = useQueryClient()
  const refresh = () => Promise.all([
    'failed-cases', 'failed-case-detail', 'issues', 'cases', 'overview', 'notification-records',
  ].map((key) => client.invalidateQueries({ queryKey: ['daily-pipeline', key] })))
  return {
    share: useMutation({ mutationFn: shareDailyProcessingFlow, onSuccess: refresh }),
    update: useMutation({ mutationFn: updateDailyVersionFlow, onSuccess: refresh }),
  }
}

export const useDailyOverview = (days: 7 | 14) => useQuery({
  queryKey: ['daily-pipeline', 'overview', days],
  queryFn: () => getDailyOverview(days),
  placeholderData: (previous) => previous,
})


export const useManuallyCompleteDailyNode = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: manuallyCompleteDailyNode,
    onSuccess: () => Promise.all([
      'failed-cases', 'failed-case-detail', 'cases', 'issues', 'overview',
    ].map((key) => client.invalidateQueries({ queryKey: ['daily-pipeline', key] }))),
    onError: () => client.invalidateQueries({ queryKey: ['daily-pipeline', 'failed-case-detail'] }),
  })
}


export const useRollbackDailyProcessing = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: rollbackDailyProcessing,
    onSuccess: () => client.invalidateQueries({ queryKey: ['daily-pipeline'] }),
  })
}

export const useRetryDailyProcessing = () => {
  const client = useQueryClient()
  return useMutation({ mutationFn: retryDailyProcessing, onSettled: () => Promise.all([
    'failed-cases', 'failed-case-detail', 'overview',
  ].map((key) => client.invalidateQueries({ queryKey: ['daily-pipeline', key] }))) })
}


export const useDailyNotificationRecipients = () => useQuery({
  queryKey: ['daily-pipeline', 'notification-recipients'], queryFn: getDailyNotificationRecipients, retry: false,
})

export const useDailyNotificationRecipientMutations = () => {
  const client = useQueryClient()
  const refresh = () => Promise.all([
    'notification-recipients', 'failed-case-detail', 'failed-cases',
  ].map((key) => client.invalidateQueries({ queryKey: ['daily-pipeline', key] })))
  return {
    save: useMutation({ mutationFn: saveDailyNotificationRecipient, onSuccess: refresh }),
    remove: useMutation({ mutationFn: deleteDailyNotificationRecipient, onSuccess: refresh }),
  }
}


const FAILURE_NOTIFICATION_CONFIG_QUERY_KEY = ['daily-pipeline', 'failure-notification-config']

export const useDailyFailureNotificationConfig = (active: boolean) => useQuery({
  queryKey: FAILURE_NOTIFICATION_CONFIG_QUERY_KEY,
  queryFn: getDailyFailureNotificationConfig,
  enabled: active,
  staleTime: 0,
  retry: false,
})

export const useSaveDailyFailureNotificationConfig = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: saveDailyFailureNotificationConfig,
    onSuccess: (config) => {
      client.setQueryData(FAILURE_NOTIFICATION_CONFIG_QUERY_KEY, config)
      return Promise.all(['notification-records', 'failed-cases', 'failed-case-detail'].map((key) =>
        client.invalidateQueries({ queryKey: ['daily-pipeline', key] })))
    },
  })
}

export const useDailyNotificationRecords = (
  page: number,
  pageSize: number,
  result: DailyNotificationConsumptionResult | undefined,
  status: DailyNotificationConsumptionStatus | undefined,
  active: boolean,
) => useQuery({
  queryKey: ['daily-pipeline', 'notification-records', page, pageSize, result, status],
  queryFn: () => getDailyNotificationRecords(page, pageSize, result, status),
  enabled: active,
  staleTime: 0,
  retry: false,
  placeholderData: (previous) => previous,
})
