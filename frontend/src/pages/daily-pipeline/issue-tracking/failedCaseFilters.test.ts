import { expect, it } from 'vitest'
import type { DailyFailedCase } from '../../../types/dailyPipeline'
import { filterFailedCases } from './failedCaseFilters'

const base: DailyFailedCase = {
  id: 'first', caseId: 'case-1', name: 'Kimi-Example', group: 'Daily', hardware: 'A3', result: '失败', failureLog: null,
  runId: 9900002, collectedAt: '2026-11-12T12:35:00+08:00', problemCategory: '代码问题',
  processingProgress: '已解决', issue: { number: 801, state: 'open' },
}
const rows: DailyFailedCase[] = [base, { ...base, id: 'second', name: 'DeepSeek', hardware: 'A2',
  group: 'E2E', problemCategory: null, processingProgress: '处理中', collectedAt: null }]

it('combines query fields, supports multiple choices and clears all conditions on reset', () => {
  expect(filterFailedCases(rows, { name: ' KIMI ', runId: '0002', hardware: ['A2', 'A3'], group: ['Daily'],
    problemCategory: ['代码问题'], issue: ['#801-open'], processingProgress: ['已解决'] })).toEqual([base])
  expect(filterFailedCases(rows, { hardware: ['A2'], processingProgress: ['已解决'] })).toEqual([])
  expect(filterFailedCases(rows, { problemCategory: ['未分类'] })).toEqual([rows[1]])
  expect(filterFailedCases(rows, {})).toEqual(rows)
})

it('filters an inclusive time range and excludes rows without a collected time', () => {
  expect(filterFailedCases(rows, { collectedAt: ['2026-11-12 12:35:00+08:00', '2026-11-12 12:35:00+08:00'] })).toEqual([base])
  expect(filterFailedCases(rows, { collectedAt: ['2026-11-12 13:00:00+08:00', '2026-11-12 14:00:00+08:00'] })).toEqual([])
})

it('filters processing results separately from the original failed test execution', () => {
  const failed = { ...base, id: 'third', processingProgress: '未解决' as const }
  const data = [...rows, failed]
  expect(filterFailedCases(data, { processingResult: ['成功'] })).toEqual([base])
  expect(filterFailedCases(data, { processingResult: ['失败'] })).toEqual([failed])
  expect(filterFailedCases(data, { processingResult: ['执行中'] })).toEqual([rows[1]])
  expect(data.every((row) => row.result === '失败')).toBe(true)
})
