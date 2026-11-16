const CATEGORY_COLORS = ['blue', 'geekblue', 'purple', 'magenta', 'cyan'] as const

type CategoryColor = typeof CATEGORY_COLORS[number]

// Reserve success, error and warning colors for statuses and pending changes.
const LABEL_COLORS: Readonly<Record<string, CategoryColor>> = {
  '量化': 'purple',
  '回归': 'geekblue',
  '精度': 'cyan',
  '性能': 'magenta',
  '功能': 'blue',
  '算子': 'purple',
  '多模态': 'magenta',
  '分布式': 'cyan',
}

const HASH_MULTIPLIER = 31

export function getDailyCaseTagColor(label: string): CategoryColor {
  const normalized = label.trim()
  if (Object.prototype.hasOwnProperty.call(LABEL_COLORS, normalized)) return LABEL_COLORS[normalized]
  let hash = 0
  for (const character of normalized) {
    hash = (Math.imul(hash, HASH_MULTIPLIER) + (character.codePointAt(0) || 0)) >>> 0
  }
  return CATEGORY_COLORS[hash % CATEGORY_COLORS.length]
}
