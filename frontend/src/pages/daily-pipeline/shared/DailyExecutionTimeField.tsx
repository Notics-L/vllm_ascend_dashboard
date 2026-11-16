import { Button, Form, Select } from 'antd'
import { useDailySchedule } from '../../../hooks/useDailyPipeline'

function DailyExecutionTimeField() {
  const { data, isLoading, isError, isFetching, refetch } = useDailySchedule()
  const times = data?.timeOptions ?? []
  return (
    <Form.Item
      label="执行时间"
      extra={isError ? <Button type="link" size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button> : undefined}
    >
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
