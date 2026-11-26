/**
 * 上游测试页面共享常量。
 *
 * 与组件分开放在 `.ts` 文件里：`shared.tsx` 同时导出组件和常量会触发
 * `react-refresh/only-export-components`，破坏该目录的热更新。
 */

export const STATUS_META: Record<string, { label: string; color: string }> = {
  passed: { label: '通过', color: 'success' },
  failed: { label: '失败', color: 'error' },
  skipped: { label: '跳过', color: 'default' },
  unknown: { label: '未知', color: 'warning' },
}

// 分段横条配色：红色只留给"失败"，其余状态各有颜色，
// 避免整条横条被一个颜色占满而看不出各状态占比。
export const SEGMENT_COLOR: Record<string, string> = {
  passed: '#16a34a',
  failed: '#dc2626',
  skipped: '#94a3b8',
  unknown: '#d97706',
}

export const IMPACT_COLOR: Record<string, string> = {
  大: 'red',
  中: 'orange',
  小: 'blue',
}

/** 用例/issue 路径列统一宽度：太窄会导致路径按单词截断、行高拉长。 */
export const PATH_COLUMN_WIDTH = 420

/**
 * 表格横向滚动宽度必须等于各列 width 之和，否则 antd 会按容器宽度等比压缩列，
 * 设置的 width 不会生效（实测「关联用例」会被压到 32px）。
 *
 * issue 列顺序：Issue(100) | 影响程度(90) | 影响(340) | 标题(380) | 关联用例(420) | 备注(140)
 */
export const ISSUE_TABLE_WIDTH = 100 + 90 + 340 + 380 + PATH_COLUMN_WIDTH + 140
export const CASE_TABLE_WIDTH = PATH_COLUMN_WIDTH + 90 + 160 + 360
