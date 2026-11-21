import { Input, Tooltip } from 'antd'
import type { InputProps } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'

function CronExpressionInput(props: InputProps) {
  return (
    <div className="daily-trigger-cron-control">
      <Input {...props} />
      <Tooltip trigger={['hover', 'focus']} title={<>
        <div>Cron 表达式规则</div>
        <div>五段格式：分 时 日 月 周</div>
        <div>取值：0–59、0–23、1–31、1–12、0–7（0 和 7 表示周日）</div>
        <div>* 任意值；, 多个值；- 范围；/ 步长</div>
        <div>示例：0 8,12,16 * * *，每天 08:00、12:00、16:00 执行。</div>
      </>}>
        <InfoCircleOutlined className="daily-trigger-cron-info" tabIndex={0} aria-label="Cron 表达式规则" />
      </Tooltip>
    </div>
  )
}

export default CronExpressionInput
