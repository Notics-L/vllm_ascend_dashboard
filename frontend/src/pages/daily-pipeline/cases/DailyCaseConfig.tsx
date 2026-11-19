import { Button, Form, Space, Spin, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { isAxiosError } from 'axios'
import { useNavigate } from 'react-router-dom'
import { useDailyCaseMutations, useDailyCases } from '../../../hooks/useDailyPipeline'
import type { NightlyCase } from '../../../types/dailyPipeline'
import NightlyCaseTreeTransfer from './NightlyCaseTreeTransfer'
import DailyCasePage from './DailyCasePage'
import { useDailyRefreshFeedback } from '../shared/useDailyRefreshFeedback'
import DailyExecutionTimeField from '../shared/DailyExecutionTimeField'
import './DailyCases.css'

interface DailyCaseConfigValues {
  nightlyCaseIds?: string[]
}

function DailyCaseConfig() {
  const navigate = useNavigate()
  const daily = useDailyCases(true)
  const nightly = useDailyCases(false)
  const isLoading = daily.isLoading || nightly.isLoading
  const isFetching = daily.isFetching || nightly.isFetching
  const isError = daily.isError || nightly.isError
  const byId = new Map<string, NightlyCase>((nightly.data ?? []).map((item) => [item.id, item]))
  for (const item of daily.data ?? []) {
    byId.set(item.id, { ...item, dailyCaseId: item.id })
  }
  const cases = [...byId.values()]
  const { saveSelection } = useDailyCaseMutations()
  const [form] = Form.useForm<DailyCaseConfigValues>()
  const draftIds: string[] | undefined = Form.useWatch('nightlyCaseIds', form)
  const originalIds = cases.filter((item) => item.dailyCaseId).map((item) => item.id)
  const selectedIds = draftIds ?? originalIds
  const addedCount = selectedIds.filter((id) => !originalIds.includes(id)).length
  const removedCount = originalIds.filter((id) => !selectedIds.includes(id)).length
  const returnToCases = () => navigate('/daily-pipeline/cases', { replace: true })

  const { pending: refreshPending, run: handleRefresh } = useDailyRefreshFeedback(async () => {
    const [left, right] = await Promise.all([daily.refetch(), nightly.refetch()])
    if (left.isSuccess && right.isSuccess) {
      form.resetFields(['nightlyCaseIds'])
    } else {
      message.error('刷新失败，请重试')
    }
  })
  const loading = isFetching || refreshPending

  const handleSave = async ({ nightlyCaseIds = originalIds }: DailyCaseConfigValues) => {
    try {
      const result = await saveSelection.mutateAsync(nightlyCaseIds)
      message.success(`配置已保存：新增 ${result.added} 条，移出 ${result.removed} 条`)
      form.resetFields(['nightlyCaseIds'])
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '保存失败，请重试'
        : '保存失败，请重试')
      const [left, right] = await Promise.all([daily.refetch(), nightly.refetch()])
      if (left.isSuccess && right.isSuccess) {
        const validIds = new Set([...left.data, ...right.data].map((item) => item.id))
        form.setFieldValue('nightlyCaseIds', nightlyCaseIds.filter((id) => validIds.has(id)))
      }
    }
  }

  return (
    <Spin spinning={loading} wrapperClassName="daily-case-config-loading">
      <DailyCasePage title="Daily 用例配置" saving={loading || saveSelection.isPending} onBack={returnToCases}>
        <Form<DailyCaseConfigValues>
          form={form}
          className="daily-case-config-form"
          layout="horizontal"
          labelCol={{ flex: '128px' }}
          wrapperCol={{ flex: 'auto' }}
          labelAlign="left"
          colon
          onFinish={handleSave}
          disabled={loading || saveSelection.isPending}
        >
          <Form.Item
            name="nightlyCaseIds"
            label="Daily 用例"
            extra={`已选 ${selectedIds.length} 条，本次新增 ${addedCount} 条、移出 ${removedCount} 条，保存后生效。`}
          >
            <NightlyCaseTreeTransfer
              cases={cases}
              disabled={isError || loading || saveSelection.isPending}
            />
          </Form.Item>
          <DailyExecutionTimeField />
          <Form.Item>
            <Space className="daily-cases-actions" size={8}>
              <Button type="primary" htmlType="submit" loading={saveSelection.isPending} disabled={loading || isLoading || isError}>保存</Button>
              <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading} disabled={loading || saveSelection.isPending}>刷新</Button>
            </Space>
          </Form.Item>
        </Form>
      </DailyCasePage>
    </Spin>
  )
}

export default DailyCaseConfig
