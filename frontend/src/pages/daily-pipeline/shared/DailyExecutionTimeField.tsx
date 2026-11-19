import { Form, Select } from 'antd'
import { useDailySchedule } from '../../../hooks/useDailyPipeline'

function DailyExecutionTimeField() {
  const { data, isLoading, isError } = useDailySchedule()
  const times = data?.timeOptions ?? []
  return (
    <Form.Item label="执行时间">
      <Select
        className="daily-execution-time-select daily-case-time-select"
        mode="multiple"
        disabled
        loading={isLoading}
        value={isError ? [] : times}
        options={times.map((time) => ({ label: time, value: time }))}
        maxTagCount={1}
        aria-label="执行时间（跟随流水线触发配置）"
      />
    </Form.Item>
  )
}

export default DailyExecutionTimeField
