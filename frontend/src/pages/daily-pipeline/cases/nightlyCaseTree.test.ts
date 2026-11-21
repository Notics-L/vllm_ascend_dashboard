import { describe, expect, it } from 'vitest'
import type { NightlyCase } from '../../../types/dailyPipeline'
import { buildNightlyCaseTree, buildTransferChangeTree, getDailySelectionFromPool, getNightlyPoolKeys } from './nightlyCaseTree'

const cases: NightlyCase[] = [
  { id: 'available', name: 'Qwen', hardware: 'A2', testType: '单节点', tags: ['回归'], status: '启用', branch: 'main' },
  { id: 'scheduler-disabled', name: 'GLM', hardware: 'A2', testType: '单节点', tags: ['量化'], status: '禁用', branch: 'main' },
  { id: 'already-added', name: 'DeepSeek', hardware: 'A2', testType: '单节点', tags: [], status: '启用', branch: 'main', dailyCaseId: 'daily-existing' },
  { id: 'multi-card', name: 'Qwen VL', hardware: 'A2', testType: '多卡', tags: [], status: '启用', branch: 'main' },
  { id: 'other-hardware', name: 'Kimi', hardware: 'A3', testType: '双节点', tags: [], status: '启用', branch: 'main' },
]

describe('Nightly tree batch selection', () => {
  it('selects only leaf case IDs within the chosen hardware and test type', () => {
    const hardware = buildNightlyCaseTree(cases).find((node) => node.title === 'A2')!
    const singleNode = hardware.children!.find((node) => node.title === '单节点')!
    expect(singleNode.selectableKeys).toEqual(['available', 'scheduler-disabled', 'already-added'])
    expect(hardware.selectableKeys).toEqual(['available', 'scheduler-disabled', 'already-added', 'multi-card'])
    expect(hardware.selectableKeys).not.toContain('other-hardware')
  })

  it('allows scheduler-disabled and existing Daily cases to move in either direction', () => {
    const leaves = buildNightlyCaseTree(cases)[0].children![0].children!
    expect(leaves.find((node) => node.key === 'scheduler-disabled')?.disabled).toBe(false)
    expect(leaves.find((node) => node.key === 'already-added')?.disabled).toBe(false)
    const existingOnlyGroup = buildNightlyCaseTree(cases.filter((item) => item.dailyCaseId))[0]
    expect(existingOnlyGroup.disabled).toBe(false)
    expect(existingOnlyGroup.selectableKeys).toEqual(['already-added'])
  })

  it('limits group selection to visible search results and excludes transferred-out cases', () => {
    const visibleCases = cases.filter((item) => item.name === 'Qwen')
    const tree = buildNightlyCaseTree(visibleCases)
    expect(tree[0].selectableKeys).toEqual(['available'])
    expect(tree[0].children![0].selectableKeys).toEqual(['available'])
    expect(buildNightlyCaseTree([])).toEqual([])
  })
})

describe('Daily on the left and Nightly on the right', () => {
  it('initially shows existing Daily cases on the left and all other cases in the Nightly pool', () => {
    const pool = getNightlyPoolKeys(cases, ['already-added'])
    expect(pool).toEqual(['available', 'scheduler-disabled', 'multi-card', 'other-hardware'])
    expect(getDailySelectionFromPool(cases, pool)).toEqual(['already-added'])
  })

  it('moves checked Nightly cases left into Daily, including scheduler-disabled cases', () => {
    const pool = getNightlyPoolKeys(cases, ['available', 'scheduler-disabled', 'already-added'])
    expect(pool).toEqual(['multi-card', 'other-hardware'])
    expect(getDailySelectionFromPool(cases, pool)).toEqual(['available', 'scheduler-disabled', 'already-added'])
  })

  it('moves both new and existing Daily cases right back to Nightly', () => {
    const poolAfterMovingBack = ['available', 'multi-card', 'other-hardware', 'already-added']
    expect(getDailySelectionFromPool(cases, poolAfterMovingBack)).toEqual(['scheduler-disabled'])
    expect(getNightlyPoolKeys(cases, ['scheduler-disabled'])).toContain('already-added')
  })

  it('allows existing cases to move back again and supports an empty optional Daily selection', () => {
    const initialPool = getNightlyPoolKeys(cases, ['already-added'])
    const movedOut = [...initialPool, 'already-added']
    expect(getDailySelectionFromPool(cases, movedOut)).toEqual([])
    expect(getDailySelectionFromPool(cases, movedOut.filter((key) => key !== 'already-added'))).toEqual(['already-added'])
    expect(getNightlyPoolKeys(cases, [])).toEqual(cases.map((item) => item.id))
    expect(getDailySelectionFromPool(cases, [])).toEqual(cases.map((item) => item.id))
  })
})

describe('Unsaved transfer change groups', () => {
  it('separates original Daily cases from ordinary Nightly candidates on initial load', () => {
    const daily = buildTransferChangeTree(cases.filter((item) => item.dailyCaseId), 'daily')
    const nightly = buildTransferChangeTree(cases.filter((item) => !item.dailyCaseId), 'nightly')
    expect(daily.map((node) => [node.changeGroup, node.selectableKeys])).toEqual([
      ['original', ['already-added']], ['added', []],
    ])
    expect(nightly.map((node) => [node.changeGroup, node.selectableKeys])).toEqual([
      ['available', ['available', 'scheduler-disabled', 'multi-card', 'other-hardware']], ['removed', []],
    ])
  })

  it('shows simultaneous new additions on Daily and pending removals on Nightly', () => {
    const selection = new Set(['available', 'scheduler-disabled'])
    const daily = buildTransferChangeTree(cases.filter((item) => selection.has(item.id)), 'daily')
    const nightly = buildTransferChangeTree(cases.filter((item) => !selection.has(item.id)), 'nightly')
    expect(daily.find((node) => node.changeGroup === 'added')?.selectableKeys).toEqual(['available', 'scheduler-disabled'])
    expect(nightly.find((node) => node.changeGroup === 'removed')?.selectableKeys).toEqual(['already-added'])
    expect(nightly.find((node) => node.changeGroup === 'removed')?.title).toBe('待移出（1）')
  })

  it('clears pending changes when new and original cases are moved back to their original panes', () => {
    const restoredSelection = new Set(getDailySelectionFromPool(cases, getNightlyPoolKeys(cases, ['already-added'])))
    const daily = buildTransferChangeTree(cases.filter((item) => restoredSelection.has(item.id)), 'daily')
    const nightly = buildTransferChangeTree(cases.filter((item) => !restoredSelection.has(item.id)), 'nightly')
    expect(daily.find((node) => node.changeGroup === 'added')?.selectableKeys).toEqual([])
    expect(nightly.find((node) => node.changeGroup === 'removed')?.selectableKeys).toEqual([])
    expect(daily.find((node) => node.changeGroup === 'original')?.selectableKeys).toEqual(['already-added'])
  })

  it('uses distinct branch keys when original and new cases share hardware and test type', () => {
    const tree = buildTransferChangeTree(cases.filter((item) => ['available', 'already-added'].includes(item.id)), 'daily')
    const keys: string[] = []
    const collect = (nodes: typeof tree) => nodes.forEach((node) => {
      keys.push(node.key)
      if (node.children) collect(node.children)
    })
    collect(tree)
    expect(new Set(keys).size).toBe(keys.length)
    expect(tree[0].selectableKeys).toEqual(['already-added'])
    expect(tree[1].selectableKeys).toEqual(['available'])
  })
})
