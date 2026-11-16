import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getDailyOverview, closeDailyIssues, deleteDailyIssues, addDailyCasesFromNightly, deleteDailyCases, deleteDailyTriggerHistory, deleteDailyCollectionRuns, getDailyCases, getDailySchedule, getDailyCollectionRuns, getDailyCollectionDetail, getDailyFailedCases, getDailyFailedCase, getDailyIssueDetail, getDailyIssues, recollectDailyRun, getDailyTriggerConfig, getDailyTriggerHistory, getDailyTriggerHistoryDetail, previewDailySchedule, getNightlyCases, saveDailyTriggerConfig, testDailyTriggerConnection, saveDailySchedule, saveDailyCaseSelection, updateDailyCase, updateDailyCaseStatuses } from '../services/dailyPipeline'

const DAILY_CASES_QUERY_KEY = ['daily-pipeline', 'cases']
const NIGHTLY_CASES_QUERY_KEY = ['daily-pipeline', 'nightly-cases']

export const useDailyCases = () => useQuery({
  queryKey: DAILY_CASES_QUERY_KEY,
  queryFn: getDailyCases,
})

export const useNightlyCases = () => useQuery({
  queryKey: NIGHTLY_CASES_QUERY_KEY,
  queryFn: getNightlyCases,
})

export const useDailyCaseMutations = () => {
  const queryClient = useQueryClient()
  const refreshCases = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: DAILY_CASES_QUERY_KEY }),
    queryClient.invalidateQueries({ queryKey: NIGHTLY_CASES_QUERY_KEY }),
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
  refetchInterval: 60_000,
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

export const useDailyTriggerConfig = () => useQuery({
  queryKey: DAILY_TRIGGER_CONFIG_QUERY_KEY,
  queryFn: getDailyTriggerConfig,
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

const CRON_PREVIEW_DELAY_MS = 200

export const useDailySchedulePreview = (cron: string | undefined) => {
  const normalized = cron?.trim().replace(/\s+/g, ' ') ?? ''
  const [debouncedCron, setDebouncedCron] = useState('')
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedCron(normalized), CRON_PREVIEW_DELAY_MS)
    return () => window.clearTimeout(timeout)
  }, [normalized])
  return useQuery({
    queryKey: ['daily-pipeline', 'schedule-preview', normalized],
    queryFn: ({ signal }) => previewDailySchedule(normalized, signal),
    enabled: Boolean(normalized) && normalized === debouncedCron,
    retry: false,
    refetchInterval: 60_000,
  })
}

export const useDailyTriggerHistory = (page: number, pageSize: number, enabled: boolean) => useQuery({
  queryKey: ['daily-pipeline', 'trigger-history', page, pageSize],
  queryFn: () => getDailyTriggerHistory(page, pageSize),
  enabled,
})

export const useDailyTriggerHistoryDetail = (id: string) => useQuery({
  queryKey: ['daily-pipeline', 'trigger-history-detail', id],
  queryFn: () => getDailyTriggerHistoryDetail(id),
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
})

export const useDailyCollectionDetail = (id: string | null) => useQuery({
  queryKey: ['daily-pipeline', 'collection-detail', id],
  queryFn: () => getDailyCollectionDetail(id!), enabled: Boolean(id),
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

export const useDailyFailedCases = () => useQuery({
  queryKey: ['daily-pipeline', 'failed-cases'],
  queryFn: getDailyFailedCases,
})

export const useDailyIssueDetail = (number: number, enabled: boolean) => useQuery({
  queryKey: ['daily-pipeline', 'issue-detail', number],
  queryFn: () => getDailyIssueDetail(number),
  enabled,
})

export const useDailyIssues = () => useQuery({
  queryKey: ['daily-pipeline', 'issues'],
  queryFn: getDailyIssues,
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

export const useDailyOverview = (days: 7 | 14) => useQuery({
  queryKey: ['daily-pipeline', 'overview', days],
  queryFn: () => getDailyOverview(days),
  placeholderData: (previous) => previous,
})
