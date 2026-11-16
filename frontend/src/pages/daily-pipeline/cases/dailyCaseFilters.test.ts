import { describe, expect, it } from 'vitest'
import type { DailyCase } from '../../../types/dailyPipeline'
import { filterDailyCases } from './dailyCaseFilters'

const cases: DailyCase[] = [
  { id: 'kimi', name: 'Kimi-K2.6', hardware: 'A3-560T', testType: '单节点', tags: ['量化'], status: '启用', branch: 'main', executionTimes: ['08:00', '12:00', '16:00'] },
  { id: 'deepseek', name: 'DeepSeek-V4', hardware: 'A2', testType: '单节点', tags: ['量化', '性能'], status: '禁用', branch: 'main', executionTimes: ['08:00', '12:00', '16:00'] },
  { id: 'qwen', name: 'Qwen3-32B', hardware: 'A3-560T', testType: '多卡', tags: ['精度'], status: '启用', branch: 'main', executionTimes: ['08:00', '12:00', '16:00'] },
]

const ids = (rows: DailyCase[]) => rows.map((item) => item.id)

describe('Daily case query filters', () => {
  it('searches names without case sensitivity and trims whitespace', () => {
    expect(ids(filterDailyCases(cases, { name: '  dEEpsEEK  ' }))).toEqual(['deepseek'])
  })

  it('combines fields with AND and allows multiple choices within a field', () => {
    expect(ids(filterDailyCases(cases, {
      hardware: ['A2', 'A3-560T'], testType: ['单节点'], status: ['禁用'],
    }))).toEqual(['deepseek'])
    expect(ids(filterDailyCases(cases, { hardware: ['A2'], testType: ['多卡'] }))).toEqual([])
  })

  it('matches any selected tag and combines it with hardware', () => {
    expect(ids(filterDailyCases(cases, { tags: ['性能', '精度'] }))).toEqual(['deepseek', 'qwen'])
    expect(ids(filterDailyCases(cases, { tags: ['性能', '精度'], hardware: ['A3-560T'] }))).toEqual(['qwen'])
  })

  it('reset and cleared fields restore enabled and disabled cases', () => {
    expect(filterDailyCases(cases, {})).toEqual(cases)
    expect(filterDailyCases(cases, { name: ' ', hardware: [], testType: [], tags: [], status: [] })).toEqual(cases)
  })

  it('does not change source data while querying', () => {
    const original = JSON.stringify(cases)
    filterDailyCases(cases, { status: ['禁用'] })
    expect(JSON.stringify(cases)).toBe(original)
  })
})
