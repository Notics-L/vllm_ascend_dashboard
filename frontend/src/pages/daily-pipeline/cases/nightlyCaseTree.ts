import type { NightlyCase } from '../../../types/dailyPipeline'

export type DailyTransferChangeGroup = 'original' | 'added' | 'available' | 'removed'

export interface NightlyCaseTreeNode {
  key: string
  title: string
  disabled: boolean
  selectableKeys: string[]
  children?: NightlyCaseTreeNode[]
  source?: NightlyCase
  changeGroup?: DailyTransferChangeGroup
}

export function buildNightlyCaseTree(cases: NightlyCase[], namespace = ''): NightlyCaseTreeNode[] {
  const hardwareGroups = new Map<string, Map<string, NightlyCase[]>>()
  for (const item of cases) {
    let typeGroups = hardwareGroups.get(item.hardware)
    if (!typeGroups) {
      typeGroups = new Map()
      hardwareGroups.set(item.hardware, typeGroups)
    }
    const group = typeGroups.get(item.testType) || []
    group.push(item)
    typeGroups.set(item.testType, group)
  }

  return [...hardwareGroups].map(([hardware, typeGroups]) => {
    const children = [...typeGroups].map(([testType, items]) => {
      const selectableKeys = items.map((item) => item.id)
      return {
        key: `${namespace}group:${JSON.stringify([hardware, testType])}`,
        title: testType,
        selectableKeys,
        disabled: !selectableKeys.length,
        children: items.map((item) => ({
          key: item.id,
          title: item.name,
          disabled: false,
          selectableKeys: [item.id],
          source: item,
        })),
      }
    })
    const selectableKeys = children.flatMap((item) => item.selectableKeys)
    return {
      key: `${namespace}group:${JSON.stringify([hardware])}`,
      title: hardware,
      children,
      selectableKeys,
      disabled: !selectableKeys.length,
    }
  })
}

export function getNightlyPoolKeys(cases: NightlyCase[], dailySelection: string[]): string[] {
  const selected = new Set(dailySelection)
  return cases.filter((item) => !selected.has(item.id)).map((item) => item.id)
}

export function getDailySelectionFromPool(cases: NightlyCase[], nightlyPoolKeys: string[]): string[] {
  const pool = new Set(nightlyPoolKeys)
  return cases.filter((item) => !pool.has(item.id)).map((item) => item.id)
}

export function buildTransferChangeTree(cases: NightlyCase[], side: 'daily' | 'nightly'): NightlyCaseTreeNode[] {
  const groups: { state: DailyTransferChangeGroup; title: string; original: boolean }[] = side === 'daily'
    ? [
      { state: 'original', title: '原有用例', original: true },
      { state: 'added', title: '待新增', original: false },
    ]
    : [
      { state: 'available', title: '可选用例', original: false },
      { state: 'removed', title: '待移出', original: true },
    ]

  return groups.map(({ state, title, original }) => {
    const items = cases.filter((item) => Boolean(item.dailyCaseId) === original)
    const key = `change:${side}:${state}`
    return {
      key,
      title: `${title}（${items.length}）`,
      changeGroup: state,
      disabled: !items.length,
      selectableKeys: items.map((item) => item.id),
      children: buildNightlyCaseTree(items, `${key}:`),
    }
  })
}
