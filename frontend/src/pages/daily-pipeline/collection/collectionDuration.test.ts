import { describe, expect, it } from 'vitest'
import { collectionDurationSeconds } from './collectionElapsed'

describe('collection run duration', () => {
  it('uses execution end time instead of collection time, including across midnight', () => {
    expect(collectionDurationSeconds({ startedAt: '2026-11-12T23:50:00+08:00', endedAt: '2026-11-13T00:20:05+08:00' })).toBe(1805)
  })
  it('does not calculate duration when execution end time is missing', () => {
    expect(collectionDurationSeconds({ startedAt: '2026-11-13T09:00:00+08:00', endedAt: null })).toBeNull()
  })
  it('retains a zero duration and rejects malformed or reversed timestamps', () => {
    const start = '2026-11-13T09:00:00+08:00'
    expect(collectionDurationSeconds({ startedAt: start, endedAt: start })).toBe(0)
    expect(collectionDurationSeconds({ startedAt: 'invalid', endedAt: start })).toBeNull()
    expect(collectionDurationSeconds({ startedAt: start, endedAt: 'invalid' })).toBeNull()
    expect(collectionDurationSeconds({ startedAt: start, endedAt: '2026-11-13T08:00:00+08:00' })).toBeNull()
  })
})
