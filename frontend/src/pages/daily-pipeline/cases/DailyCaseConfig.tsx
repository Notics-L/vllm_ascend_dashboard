import { Alert, Button, Form, Space, message } from 'antd'
import { isAxiosError } from 'axios'
import { useNavigate } from 'react-router-dom'
import { useDailyCaseMutations, useNightlyCases } from '../../../hooks/useDailyPipeline'
import NightlyCaseTreeTransfer from './NightlyCaseTreeTransfer'
import DailyCasePage from './DailyCasePage'
import DailyExecutionTimeField from '../shared/DailyExecutionTimeField'
import './DailyCases.css'

interface DailyCaseConfigValues {
  nightlyCaseIds?: string[]
}

function DailyCaseConfig() {
  const navigate = useNavigate()
  const { data: cases = [], isLoading, isFetching, isError, refetch } = useNightlyCases()
  const { saveSelection } = useDailyCaseMutations()
  const [form] = Form.useForm<DailyCaseConfigValues>()
  const draftIds: string[] | undefined = Form.useWatch('nightlyCaseIds', form)
  const originalIds = cases.filter((item) => item.dailyCaseId).map((item) => item.id)
  const selectedIds = draftIds ?? originalIds
  const addedCount = selectedIds.filter((id) => !originalIds.includes(id)).length
  const removedCount = originalIds.filter((id) => !selectedIds.includes(id)).length
  const returnToCases = () => navigate('/daily-pipeline/cases', { replace: true })

  const handleSave = async ({ nightlyCaseIds = originalIds }: DailyCaseConfigValues) => {
    try {
      const result = await saveSelection.mutateAsync(nightlyCaseIds)
      message.success(`配置已保存：新增 ${result.added} 条，移出 ${result.removed} 条`)
      returnToCases()
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '保存失败，请重试'
        : '保存失败，请重试')
      const refreshed = await refetch()
      if (refreshed.data) {
        form.setFieldValue('nightlyCaseIds', nightlyCaseIds.filter((id) => (
          refreshed.data.some((item) => item.id === id)
        )))
      }
    }
  }

  return (
    <DailyCasePage title="Daily 用例配置" saving={saveSelection.isPending} onBack={returnToCases}>
      <Form<DailyCaseConfigValues>
        form={form}
        className="daily-case-config-form"
        layout="horizontal"
        labelCol={{ flex: '128px' }}
        wrapperCol={{ flex: 'auto' }}
        labelAlign="left"
        colon
        onFinish={handleSave}
        disabled={saveSelection.isPending}
      >
        {isError && (
          <Form.Item>
            <Alert type="error" showIcon message="Nightly 用例加载失败" action={
              <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
            } />
          </Form.Item>
        )}
        <Form.Item
          name="nightlyCaseIds"
          label="Daily 用例"
          extra={`已选 ${selectedIds.length} 条，本次新增 ${addedCount} 条、移出 ${removedCount} 条，保存后生效。`}
        >
          <NightlyCaseTreeTransfer
            cases={cases}
            loading={isLoading || isFetching}
            disabled={isError || saveSelection.isPending}
          />
        </Form.Item>
        <DailyExecutionTimeField />
        <Form.Item>
          <Space className="daily-cases-actions" size={8}>
            <Button type="primary" htmlType="submit" loading={saveSelection.isPending} disabled={isLoading || isError}>保存</Button>
            <Button onClick={returnToCases} disabled={saveSelection.isPending}>取消</Button>
          </Space>
        </Form.Item>
      </Form>
    </DailyCasePage>
  )
}

export default DailyCaseConfig
