import { Tag } from 'antd'

const formatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
})
export const formatCollectionTime = (time: string | null) => time ? formatter.format(new Date(time)) : '—'

const resultColors: Record<string, string> = { 执行中: 'processing', 成功: 'green', 失败: 'red', 已取消: 'default', 未获取: 'default', 已跳过: 'default', 排队中: 'default' }
export const executionResult = (result: string) => <Tag color={resultColors[result]}>{result}</Tag>
