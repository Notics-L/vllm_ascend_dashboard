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
  const locatingNeedsPr = (stage.problemCategory ?? selectedCategory) !== 'environment'
  const code = stage.stageCode
  const classify = code === 'classify'
  const legacyClassification = code === 'locating' && !stage.problemCategory
  const prRequired = code === 'revert_pr' || code === 'pr_merge'
  const codeLocating = code === 'locating' && (stage.problemCategory ?? selectedCategory) === 'code'
  const revertStep = codeLocating || code === 'revert_pr'
  return <Spin spinning={pending}>
      <Form form={form} layout="vertical" size="small" requiredMark={false} initialValues={{ outcome: "success", revert_mode: 'auto', pr_number: stage.resultData?.pr_number, run_id: stage.resultData?.run_id }} onFinish={onSubmit} disabled={pending}>
        {!classify && code !== 'failure_notification' && <Form.Item name="outcome" label="处理结果" rules={[{ required: true }]}>
          <Select options={[{ value: 'success', label: '已完成' }, { value: 'failure', label: '处理失败' }]} />
        </Form.Item>}
        {(classify || legacyClassification) && <Form.Item name="category" label="问题分类" rules={[{ required: (classify || legacyClassification) && success, message: '请选择问题分类' }]}>
          <Select options={categories} />
        </Form.Item>}
        {(prRequired || (code === 'locating' && locatingNeedsPr)) && <Form.Item name="pr_number"
          label={code === 'revert_pr' ? '回退 PR 编号' : code === 'pr_merge' ? '合入 PR 编号' : '问题 PR 编号'}
          rules={[{ required: (prRequired || code === "locating") && success, message: '请填写 PR 编号' }]}>
          <InputNumber min={1} precision={0} max={Number.MAX_SAFE_INTEGER} style={{ width: '100%' }} />
        </Form.Item>}
        {success && codeLocating && <Form.Item name="revert_mode"
          label="回退 PR 创建方式" preserve={false} rules={[{ required: true, message: '请选择回退 PR 创建方式' }]}>
          <Radio.Group options={[{ value: 'auto', label: '系统创建' }, { value: 'manual', label: '用户创建' }]} />
        </Form.Item>}
        {code === 'image_rebuild' && <Form.Item name="run_id" label="构建 Run ID" rules={[{ required: success, message: '请填写构建 Run ID' }]}>
          <InputNumber min={1} precision={0} max={Number.MAX_SAFE_INTEGER} style={{ width: '100%' }} />
        </Form.Item>}
        {!classify && !(success && revertStep) && <Form.Item name="message"
          label={revertStep ? '失败原因（选填）' : code === 'locating' ? '定位结果' : '处理说明'}
          rules={[{ required: !revertStep, whitespace: !revertStep, message: '请填写说明' }, { max: 10000 }]}>
          <Input.TextArea rows={4} maxLength={10000} />
        </Form.Item>}
        <div className="daily-cases-actions">
          <Button type="primary" htmlType="submit" icon={<CheckOutlined />} loading={pending}>
            {code === 'revert_pr' && success ? '登记回退 PR' : '提交'}
          </Button>
        </div>
      </Form>
  </Spin>
}
