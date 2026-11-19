import { expect, it } from 'vitest'
import { processingTimelinePaths } from './processingTimelinePaths'

it('joins adjacent time points on the same row', () => {
  expect(processingTimelinePaths([{ x: 20, y: 45, rowTop: 0, rowBottom: 90 },
    { x: 320, y: 45, rowTop: 0, rowBottom: 90 }], 640)).toEqual(['M 27 45 H 313'])
})

it('routes a wrapped connection through the row gap and back to the first point of the next row', () => {
  const paths = processingTimelinePaths([
    { x: 20, y: 45, rowTop: 0, rowBottom: 90 },
    { x: 320, y: 45, rowTop: 0, rowBottom: 90 },
    { x: 20, y: 163, rowTop: 118, rowBottom: 208 },
    { x: 320, y: 163, rowTop: 118, rowBottom: 208 },
  ], 640)
  expect(paths).toHaveLength(3)
  expect(paths[1]).toMatch(/^M 327 45 /)
  expect(paths[1]).toContain('Q 636 104 630 104')
  expect(paths[1]).toContain('Q 4 104 4 110')
  expect(paths[1]).toMatch(/H 13$/)
  expect(paths[2]).toBe('M 27 163 H 313')
})

it('keeps every connection when each time point wraps to its own row', () => {
  const points = Array.from({ length: 5 }, (_, index) => ({ x: 20, y: index * 120 + 45, rowTop: index * 120, rowBottom: index * 120 + 90 }))
  expect(processingTimelinePaths(points, 280)).toHaveLength(4)
  expect(processingTimelinePaths([], 280)).toEqual([])
  expect(processingTimelinePaths(points.slice(0, 1), 280)).toEqual([])
})
