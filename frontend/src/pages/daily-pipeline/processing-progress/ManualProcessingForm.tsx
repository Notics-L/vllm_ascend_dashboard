import { Button, Form, Input, InputNumber, Radio, Select, Spin } from 'antd'
import { CheckOutlined } from '@ant-design/icons'
import type { DailyManualNodeInput, DailyProcessingStage } from '../../../types/dailyPipeline'

const categories = [
  { value: 'code', label: '代码问题' },
  { value: 'version', label: '版本配套问题' },
  { value: 'environment', label: '环境问题' },
]

export interface ManualProcessingFormValues {
  outcome?: "success" | "failure"
  category?: DailyManualNodeInput['result']['category']
  revert_mode?: DailyManualNodeInput['result']['revert_mode']
  pr_number?: number
  run_id?: number
  message?: string
}

export default function ManualProcessingForm({ stage, pending, onSubmit }: {
  stage: DailyProcessingStage
  pending: boolean
  onSubmit: (values: ManualProcessingFormValues) => Promise<void>
}) {
  const [form] = Form.useForm<ManualProcessingFormValues>()
  const success = Form.useWatch('outcome', form) !== 'failure'
  const selectedCategory = Form.useWatch('category', form)
  const code = stage.stageCode
  const classify = code === 'classify'
  const legacyClassification = code === 'locating' && !stage.problemCategory
  const category = classify ? selectedCategory : (stage.problemCategory ?? selectedCategory)
  const needsPr = code === 'revert_pr' || code === 'pr_merge'
    || (code === 'locating' && category === 'code')
  const environmentNote = category === 'environment'
    && (classify || code === 'locating' || code === 'mark_success')
  return <Spin spinning={pending}>
      <Form form={form} layout="vertical" size="small" requiredMark={false} initialValues={{ outcome: "success", revert_mode: 'auto', pr_number: stage.resultData?.pr_number, run_id: stage.resultData?.run_id }} onFinish={onSubmit} disabled={pending}>
        {!classify && code !== 'failure_notification' && <Form.Item name="outcome" label="处理结果" rules={[{ required: true }]}>
          <Select options={[{ value: 'success', label: '已完成' }, { value: 'failure', label: '处理失败' }]} />
        </Form.Item>}
        {(classify || (legacyClassification && success)) && <Form.Item name="category" label="问题分类" preserve={false}
          rules={[{ required: true, message: '请选择问题分类' }]}>
          <Select options={categories} />
        </Form.Item>}
        {success && needsPr && <Form.Item name="pr_number" preserve={false}
          label={code === 'revert_pr' ? '回退 PR 编号' : code === 'pr_merge' ? '合入 PR 编号' : '问题 PR 编号'}
          rules={[{ required: true, message: '请填写 PR 编号' }]}>
          <InputNumber min={1} precision={0} max={Number.MAX_SAFE_INTEGER} style={{ width: '100%' }} />
        </Form.Item>}
        {success && code === 'locating' && category === 'code' && <Form.Item name="revert_mode"
          label="回退 PR 创建方式" preserve={false} rules={[{ required: true, message: '请选择回退 PR 创建方式' }]}>
          <Radio.Group options={[{ value: 'auto', label: '系统创建' }, { value: 'manual', label: '用户创建' }]} />
        </Form.Item>}
        {success && code === 'image_rebuild' && <Form.Item name="run_id" label="构建 Run ID" preserve={false}
          rules={[{ required: true, message: '请填写构建 Run ID' }]}>
          <InputNumber min={1} precision={0} max={Number.MAX_SAFE_INTEGER} style={{ width: '100%' }} />
        </Form.Item>}
        {(!success || environmentNote) && <Form.Item name="message"
          label={success ? '备注说明（选填）' : '失败原因（选填）'} preserve={false}>
          <Input.TextArea rows={4} maxLength={10000} />
        </Form.Item>}
        <div className="daily-cases-actions">
          <Button type="primary" htmlType="submit" icon={<CheckOutlined />} loading={pending}>
            {code === 'failure_notification' ? '确认已通知' : code === 'revert_pr' && success ? '登记回退 PR' : '提交'}
          </Button>
        </div>
      </Form>
  </Spin>
}
