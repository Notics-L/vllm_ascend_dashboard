import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Space, message } from 'antd'
import { isAxiosError } from 'axios'
import { ApiOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useDailySchedulePreview, useDailyTriggerConfig, useSaveDailyTriggerConfig, useTestDailyTriggerConnection } from '../../../hooks/useDailyPipeline'
import type { DailyTriggerConfigInput } from '../../../types/dailyPipeline'
import DailyCasePage from '../cases/DailyCasePage'
import CronExpressionInput from './CronExpressionInput'
import DailyTriggerPreview from './DailyTriggerPreview'
import DailyTriggerHistory from './DailyTriggerHistory'
import './Trigger.css'

function DailyPipelineTrigger() {
  const { data, isLoading, isError, isFetching, refetch } = useDailyTriggerConfig()
  const saveConfig = useSaveDailyTriggerConfig()
  const testConnection = useTestDailyTriggerConnection()
  const [form] = Form.useForm<DailyTriggerConfigInput>()
  const [historyOpen, setHistoryOpen] = useState(false)
  const draftCron = Form.useWatch('cron', form) as string | undefined
  const preview = useDailySchedulePreview(draftCron)
  const hasCron = Boolean(draftCron?.trim())
  const previewError = hasCron && preview.isError
    ? isAxiosError<{ detail?: string }>(preview.error)
      ? preview.error.response?.data?.detail || '执行时间预览失败，请重试'
      : '执行时间预览失败，请重试'
    : undefined
  useEffect(() => {
    form.setFieldsValue({ cron: data?.cron ?? '' })
  }, [data?.cron, form])
  const handleTestConnection = async () => {
    try {
      await form.validateFields(['githubToken'])
    } catch {
      return
    }
    try {
      const result = await testConnection.mutateAsync(form.getFieldValue('githubToken')?.trim() || undefined)
      message.info(result.detail)
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '连通性测试失败，请重试' : '连通性测试失败，请重试')
    }
  }
  const handleRefresh = async () => {
    const refreshed = await refetch()
    if (refreshed.isError) {
      message.error('刷新失败，请重试')
      return
    }
    form.setFieldsValue({ cron: refreshed.data?.cron ?? '', githubToken: '' })
    if (refreshed.data?.cron && refreshed.data.cron === draftCron?.trim().replace(/\s+/g, ' ')) {
      void preview.refetch()
    }
  }
  const handleSave = async ({ cron, githubToken }: DailyTriggerConfigInput) => {
    try {
      const saved = await saveConfig.mutateAsync({
        cron,
        ...(githubToken?.trim() ? { githubToken: githubToken.trim() } : {}),
      })
      form.setFieldsValue({ cron: saved.cron ?? '', githubToken: '' })
      message.success('配置已保存')
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '保存失败，请重试' : '保存失败，请重试')
    }
  }
  return (
    <DailyCasePage title="流水线触发" saving={saveConfig.isPending}>
      <Form<DailyTriggerConfigInput>
        id="daily-trigger-config"
        form={form}
        className="daily-case-config-form daily-trigger-form"
        layout="horizontal"
        labelCol={{ flex: '112px' }}
        wrapperCol={{ flex: 'auto' }}
        labelAlign="left"
        requiredMark={false}
        colon
        disabled={isLoading || isError || saveConfig.isPending || testConnection.isPending}
        onFinish={handleSave}
      >
        {isError && <Form.Item><Alert type="error" showIcon message="配置加载失败" action={
          <Button size="small" loading={isFetching} onClick={() => void refetch()}>重试</Button>
        } /></Form.Item>}
        <Form.Item
          name="githubToken"
          label="GitHub Token"
          rules={[{ required: !data?.githubTokenConfigured, whitespace: true, message: '请输入 GitHub Token' }]}
        >
          <Input.Password
            autoComplete="new-password"
            placeholder={data?.githubTokenConfigured ? '已配置，输入可替换' : '请输入 GitHub Token'}
            maxLength={4096}
          />
        </Form.Item>
        <Form.Item
          name="cron"
          label="Cron 表达式"
          validateStatus={previewError ? 'error' : undefined}
          help={previewError ? <span role="alert">{previewError}</span> : undefined}
          extra={hasCron && !preview.isError && preview.data ? (
            <div className="daily-trigger-cron-time" aria-live="polite">
              {preview.data.executionTimes.map((time) => <span key={time}>{time}</span>)}
              <span>（北京时间）</span>
            </div>
          ) : undefined}
          rules={[{ required: true, whitespace: true, message: '请输入 Cron 表达式' }]}
        >
          <CronExpressionInput placeholder="请输入 Cron 表达式" maxLength={120} />
        </Form.Item>
      </Form>
      <div className="daily-trigger-history-entry">
        <Button type="link" className="daily-trigger-history-link" onClick={() => setHistoryOpen(true)}>查看历史记录</Button>
      </div>
      <DailyTriggerPreview />
      <Space className="daily-cases-actions daily-trigger-actions">
          <Button
            type="primary"
            htmlType="submit"
            form="daily-trigger-config"
            icon={<SaveOutlined />}
            loading={saveConfig.isPending}
            disabled={isLoading || isError || testConnection.isPending || (hasCron && (preview.isPending || preview.isFetching || preview.isError))}
          >保存</Button>
          <Button
            className="daily-trigger-refresh"
            icon={<ReloadOutlined />}
            loading={isFetching}
            disabled={isLoading || saveConfig.isPending || testConnection.isPending}
            onClick={() => void handleRefresh()}
          >刷新</Button>
          <Button
            className="daily-trigger-test"
            icon={<ApiOutlined />}
            loading={testConnection.isPending}
            disabled={isLoading || isError || isFetching || saveConfig.isPending}
            onClick={() => void handleTestConnection()}
          >连通性测试</Button>
      </Space>
      <DailyTriggerHistory open={historyOpen} onClose={() => setHistoryOpen(false)} />
    </DailyCasePage>
  )
}

export default DailyPipelineTrigger
