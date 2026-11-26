import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button, ConfigProvider, Form, Input, Modal, Space, Spin, message } from 'antd'
import { isAxiosError } from 'axios'
import { ApiOutlined, PlayCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useDailyCases, useDailySchedulePreview, useDailyTriggerConfig, useSaveDailyTriggerConfig, useTestDailyTriggerConnection, useTriggerDailyPipeline } from '../../../hooks/useDailyPipeline'
import type { DailyTriggerConfigInput } from '../../../types/dailyPipeline'
import { useDailyRefreshFeedback } from '../shared/useDailyRefreshFeedback'
import { useDailyTabOpening } from '../shared/useDailyTabOpening'
import { useDailyQueryError } from '../shared/useDailyQueryError'
import CronExpressionInput from './CronExpressionInput'
import DailyTriggerPreview from './DailyTriggerPreview'
import DailyTriggerHistory from './DailyTriggerHistory'
import './Trigger.css'

function DailyTriggerSettings({ active }: { active: boolean }) {
  const { data, isFetching, refetch } = useDailyTriggerConfig(active)
  const casesQuery = useDailyCases(true, { enabled: active, staleTime: 0 })
  useDailyQueryError(casesQuery, 'Daily 用例加载失败', active)
  const saveConfig = useSaveDailyTriggerConfig()
  const testConnection = useTestDailyTriggerConnection()
  const triggerPipeline = useTriggerDailyPipeline()
  const [form] = Form.useForm<DailyTriggerConfigInput>()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [triggerConfirmOpen, setTriggerConfirmOpen] = useState(false)
  const draftCron = Form.useWatch('cron', form) as string | undefined
  const preview = useDailySchedulePreview(draftCron, active)
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
      const token = form.getFieldValue('githubToken')?.trim() || undefined
      if (!token && !data?.githubTokenConfigured) {
        form.setFields([{ name: 'githubToken', errors: ['请输入 GitHub Token'] }])
        return
      }
      const result = await testConnection.mutateAsync(token)
      if (result.verified) message.success(result.detail)
      else message.warning(result.detail)
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '连通性测试失败，请重试' : '连通性测试失败，请重试')
    }
  }
  const refresh = useDailyRefreshFeedback(async () => {
    const [refreshed] = await Promise.all([refetch(), casesQuery.refetch()])
    if (refreshed.isError) {
      void message.error('刷新失败，请稍后重试')
      return
    }
    form.setFieldsValue({ cron: refreshed.data?.cron ?? '', githubToken: '' })
    if (refreshed.data?.cron && refreshed.data.cron === draftCron?.trim().replace(/\s+/g, ' ')) {
      await preview.refetch()
    }
  })
  const opening = useDailyTabOpening(active)
  const loading = active && (opening || isFetching || casesQuery.isFetching || refresh.pending || saveConfig.isPending)
  const triggerDisabled = !active || loading || saveConfig.isPending || testConnection.isPending || triggerPipeline.isPending
  const controls = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    if (controls.current) controls.current.inert = !active || loading
  }, [active, loading])
  const handleSave = async ({ cron, githubToken }: DailyTriggerConfigInput) => {
    try {
      const saved = await saveConfig.mutateAsync({
        cron: cron?.trim() ?? '',
        ...(githubToken?.trim() ? { githubToken: githubToken.trim() } : {}),
      })
      form.setFieldsValue({ cron: saved.cron ?? '', githubToken: '' })
      message.success('配置已保存')
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '保存失败，请重试' : '保存失败，请重试')
    }
  }
  const handleTrigger = async (includeInRecommendation: boolean) => {
    if (triggerDisabled) return
    setTriggerConfirmOpen(false)
    try {
      const result = await triggerPipeline.mutateAsync(includeInRecommendation)
      if (result.result === '触发成功') message.success('触发成功')
      else message.error(result.logs[result.logs.length - 1]?.message || '触发失败')
    } catch (error) {
      message.error(isAxiosError<{ detail?: string }>(error)
        ? error.response?.data?.detail || '触发失败，请重试' : '触发失败，请重试')
    }
  }
  return (
    <>
      <div className="daily-trigger-settings" aria-busy={loading}>
        <div ref={controls}>
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
            disabled={saveConfig.isPending || testConnection.isPending || triggerPipeline.isPending}
            onFinish={handleSave}
          >
            <Form.Item
              name="githubToken"
              label="GitHub Token"
              rules={[{ required: hasCron && !data?.githubTokenConfigured, whitespace: true, message: '请输入 GitHub Token' }]}
            >
              <Input.Password
                autoComplete="new-password"
                placeholder={data?.githubTokenConfigured ? '••••••••••••••••' : '请输入 GitHub Token'}
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
            >
              <CronExpressionInput allowClear placeholder="留空并保存可取消定时触发" maxLength={120} />
            </Form.Item>
          </Form>
          <div className="daily-cases-actions daily-trigger-manual-action">
            <Button
              color="orange"
              variant="solid"
              icon={<PlayCircleOutlined />}
              loading={triggerPipeline.isPending}
              disabled={triggerDisabled}
              onClick={() => setTriggerConfirmOpen(true)}
            >手动触发</Button>
          </div>
          <div className="daily-trigger-history-entry">
            <Button type="link" className="daily-trigger-history-link" onClick={() => setHistoryOpen(true)}>查看历史记录</Button>
          </div>
          <DailyTriggerPreview cases={casesQuery.data ?? []} />
          <Space className="daily-cases-actions daily-trigger-actions">
            <Button
              type="primary"
              htmlType="submit"
              form="daily-trigger-config"
              icon={<SaveOutlined />}
              loading={saveConfig.isPending}
              disabled={triggerPipeline.isPending || testConnection.isPending || (hasCron && (preview.isPending || preview.isError))}
            >保存</Button>
            <Button
              className="daily-trigger-refresh"
              icon={<ReloadOutlined />}
              disabled={saveConfig.isPending || testConnection.isPending || triggerPipeline.isPending}
              onClick={() => void refresh.run()}
            >刷新</Button>
            <Button
              className="daily-trigger-test"
              icon={<ApiOutlined />}
              loading={testConnection.isPending}
              disabled={saveConfig.isPending || triggerPipeline.isPending}
              onClick={() => void handleTestConnection()}
            >连通性测试</Button>
          </Space>
        </div>
        {loading && <div className="daily-trigger-settings-mask" role="status" aria-label="正在加载流水线配置">
          <Spin />
        </div>}
      </div>
      <ConfigProvider wave={{ disabled: true }}>
        <Modal
          title="手动触发 Daily"
          open={triggerConfirmOpen}
          onCancel={() => setTriggerConfirmOpen(false)}
          transitionName=""
          maskTransitionName=""
          footer={
            <Space className="daily-cases-actions" size={8}>
              <Button size="small" disabled={triggerDisabled} onClick={() => void handleTrigger(false)}>否，仅触发</Button>
              <Button size="small" type="primary" disabled={triggerDisabled} onClick={() => void handleTrigger(true)}>是，加入推荐</Button>
            </Space>
          }
        >
          此次触发是否加入到推荐系统？
        </Modal>
      </ConfigProvider>
      <DailyTriggerHistory open={historyOpen} onClose={() => setHistoryOpen(false)} />
    </>
  )
}

export default DailyTriggerSettings
