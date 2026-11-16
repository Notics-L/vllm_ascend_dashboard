import { Form, Select, Switch, Typography } from 'antd'
import type { DailyCase } from '../../../types/dailyPipeline'
import DailyCaseTag from './DailyCaseTag'
import DailyExecutionTimeField from '../shared/DailyExecutionTimeField'

interface DailyCaseFieldsProps {
  record: DailyCase
  labelOptions: { label: string; value: string }[]
}

function DailyCaseFields({ record, labelOptions }: DailyCaseFieldsProps) {
  return (
    <>
      <Form.Item label="用例名称">
        <Typography.Text className="daily-case-readonly-value">{record.name}</Typography.Text>
      </Form.Item>
      <Form.Item label="硬件类型">
        <Typography.Text className="daily-case-readonly-value">{record.hardware}</Typography.Text>
      </Form.Item>
      <Form.Item label="测试类型">
        <Typography.Text className="daily-case-readonly-value">{record.testType}</Typography.Text>
      </Form.Item>
      <Form.Item name="tags" label="标签" rules={[{ required: true, type: 'array', min: 1, message: '请至少选择一个标签' }]}>
        <Select
          className="daily-case-edit-tags"
          mode="tags"
          options={labelOptions}
          tokenSeparators={[',', '，']}
          placeholder="选择或输入标签"
          tagRender={(props) => <DailyCaseTag label={String(props.value)} closable={props.closable} onClose={props.onClose} />}
          optionRender={(option) => <DailyCaseTag label={String(option.data.value)} />}
        />
      </Form.Item>
      <Form.Item
        name="status"
        label="状态"
        rules={[{ required: true, message: '请选择状态' }]}
        getValueProps={(status: DailyCase['status']) => ({ checked: status === '启用' })}
        getValueFromEvent={(checked: boolean) => checked ? '启用' : '禁用'}
      >
        <Switch className="daily-case-edit-status" size="small" aria-label="用例启用状态" />
      </Form.Item>
      <DailyExecutionTimeField />
    </>
  )
}

export default DailyCaseFields
