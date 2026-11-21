import { expect, it } from 'vitest'
import type { DailyCollectionDetail } from '../../../types/dailyPipeline'
import { runAllResultRows } from './collectionResultRows'

const job: DailyCollectionDetail['jobs'][number] = {
  id: '11', name: 'run-all-tests (main)', group: 'run-all-tests', result: '失败',
}
const testCase: DailyCollectionDetail['cases'][number] = {
  id: '21', jobId: '133175765336', name: 'cpu-0 card-(part 1-1)',
  group: 'UT', hardware: 'CPU', vllmVersion: 'main', result: '失败',
}

it('does not turn scheduling jobs into test execution rows', () => {
  const coverageJob = { ...job, id: '12', name: 'upload-coverage-to-obs' }
  expect(runAllResultRows({ jobs: [job, coverageJob], cases: [] })).toEqual([])
})

it('shows actual failed test execution jobs without duplicating their parent', () => {
  expect(runAllResultRows({ jobs: [job], cases: [testCase] })).toEqual([testCase])
})

it('keeps UT, E2E and mixed execution jobs with their version and result', () => {
  const cases: DailyCollectionDetail['cases'] = [
    testCase,
    { ...testCase, id: '22', group: 'E2E', hardware: 'A3-560T', result: '成功' },
    { ...testCase, id: '23', group: 'UT/E2E', vllmVersion: 'release', result: '执行中' },
  ]
  expect(runAllResultRows({ jobs: [], cases })).toEqual(cases)
})

it('keeps Daily cases out of the run-all-tests tab and handles unloaded data', () => {
  expect(runAllResultRows({ jobs: [], cases: [{ ...testCase, group: 'Daily' }] })).toEqual([])
  expect(runAllResultRows()).toEqual([])
})
