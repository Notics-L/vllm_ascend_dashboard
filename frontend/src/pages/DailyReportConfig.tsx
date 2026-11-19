import { useState } from 'react'
import {
  Card, Form, Input, Switch, Button, Space, Tag, Table,
  Alert, Descriptions, Modal, Typography, Divider, Spin, Empty, Row, Col,
  Tooltip, Badge, Select,
} from 'antd'
import {
  MailOutlined, SendOutlined, SettingOutlined, HistoryOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined, InfoCircleOutlined
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  useReportConfig, useUpdateReportConfig,
  useReportHistory, useLatestReport, useGenerateReportDraft, useSendReportDraft,
} from '../hooks/useDailyReport'
import type { DailyReportConfigUpdate, DailyReportHistoryItem } from '../services/dailyReport'
import { getReportDraftPreview } from '../services/dailyReport'
import { useCurrentUser } from '../hooks/useCurrentUser'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './DailyReportConfig.tsx.css'

const { Text, Title } = Typography

const reportStatusLabel = (status: string) => ({
  generating: '生成中',
  draft: '草稿待确认',
  sending: '发送中',
  sent: '已发送',
  failed: '失败',
}[status] || status)

function DailyReportConfigPage() {
  const { data: currentUser } = useCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'

  const { data: config, isLoading: configLoading } = useReportConfig()
  const updateConfig = useUpdateReportConfig()
  const generateDraft = useGenerateReportDraft()
  const sendDraft = useSendReportDraft()
  const { data: historyData, isLoading: historyLoading } = useReportHistory(20, 0)
  const { data: latestData, isLoading: latestLoading } = useLatestReport()

  const [configForm] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [triggerResult, setTriggerResult] = useState<{ success: boolean; message: string } | null>(null)
  const [triggerDate, setTriggerDate] = useState<string>('')
  const [emailPreviewHtml, setEmailPreviewHtml] = useState('')
  const [emailPreviewOpen, setEmailPreviewOpen] = useState(false)
  const [emailPreviewLoading, setEmailPreviewLoading] = useState(false)

  const handlePreviewDraft = async (reportId: number) => {
    setEmailPreviewLoading(true)
    try {
      setEmailPreviewHtml(await getReportDraftPreview(reportId))
      setEmailPreviewOpen(true)
    } catch (err: unknown) {
      Modal.error({ title: '预览失败', content: err instanceof Error ? err.message : '无法生成最终邮件预览' })
    } finally {
      setEmailPreviewLoading(false)
    }
  }

  const handleGenerateDraft = async () => {
    setTriggering(true)
    setTriggerResult(null)
    try {
      const result = await generateDraft.mutateAsync(triggerDate || undefined)
      setTriggerResult({ success: result.success, message: result.message })
      if (result.success) Modal.success({ title: '草稿已生成', content: '请检查最新报告，确认内容后再发送。' })
    } catch (err: unknown) {
      setTriggerResult({ success: false, message: err instanceof Error ? err.message : '生成草稿失败' })
    } finally {
      setTriggering(false)
    }
  }

  const handleSendDraft = (reportId: number) => {
    Modal.confirm({
      title: '确认发送这份日报？',
      content: '邮件将使用当前预览内容，不会重新调用 AI 生成。',
      okText: '确认发送',
      cancelText: '继续检查',
      onOk: async () => {
        const result = await sendDraft.mutateAsync(reportId)
        if (!result.success) throw new Error(result.message)
      },
    })
  }

  const handleSaveConfig = async () => {
    try {
      const values = await configForm.validateFields()
      setSaving(true)
      const update: DailyReportConfigUpdate = {}
      if (values.report_recipients !== config?.report_recipients) update.report_recipients = values.report_recipients
      if (values.report_cc_recipients !== config?.report_cc_recipients) update.report_cc_recipients = values.report_cc_recipients
      if (values.report_subject_template !== config?.report_subject_template) update.report_subject_template = values.report_subject_template
      if (values.report_schedule_hour !== config?.report_schedule_hour) update.report_schedule_hour = values.report_schedule_hour
      if (values.report_schedule_minute !== config?.report_schedule_minute) update.report_schedule_minute = values.report_schedule_minute
      if (values.report_enabled !== config?.report_enabled) update.report_enabled = values.report_enabled

      if (Object.keys(update).length === 0) {
        Modal.info({ title: '无变更', content: '配置没有变化，无需保存' })
        setSaving(false)
        return
      }

      await updateConfig.mutateAsync(update)
      Modal.success({ title: '保存成功', content: '报告配置已更新' })
    } catch {
      Modal.error({ title: '保存失败', content: '请检查网络连接后重试。' })
    } finally {
      setSaving(false)
    }
  }

  const renderSummaryCard = (label: string, value: string | number, className = '') => (
    <div className={`report-summary-card ${className}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  )

  const renderLatestReport = () => {
    if (latestLoading) return <Spin />
    if (!latestData || ('message' in latestData && latestData.message)) return <Empty description="暂无报告记录" />
    const report = latestData as Exclude<typeof latestData, { message: string; data: null }>
    const ci = report.ci_summary as Record<string, unknown> || {}
    const model = report.model_summary as Record<string, unknown> || {}
    const gh = report.github_summary as Record<string, unknown> || {}

    return (
      <Card className="stripe-card" title={<Space><HistoryOutlined />最新报告详情</Space>} size="small">
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="报告日期">{report.report_date}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={report.status === 'sent' ? 'green' : report.status === 'failed' ? 'red' : 'orange'}>
              {reportStatusLabel(report.status)}
            </Tag>
          </Descriptions.Item>
        </Descriptions>
        <Divider orientation="left" plain>CI 概况</Divider>
        <div className="report-summary-grid">
          {renderSummaryCard('总运行数', ci.total_runs as number || 0)}
          {renderSummaryCard('通过', ci.success_runs as number || 0, 'success')}
          {renderSummaryCard('失败', ci.failure_runs as number || 0, 'danger')}
          {renderSummaryCard('通过率', `${(ci.success_rate as number || 0).toFixed(1)}%`,
            (ci.success_rate as number || 0) >= 90 ? 'success' : (ci.success_rate as number || 0) >= 70 ? 'warning' : 'danger')}
        </div>
        <Divider orientation="left" plain>模型验证</Divider>
        <div className="report-summary-grid">
          {renderSummaryCard('报告数', model.total_reports as number || 0)}
          {renderSummaryCard('Pass', model.pass_count as number || 0, 'success')}
          {renderSummaryCard('Fail', model.fail_count as number || 0, 'danger')}
          {renderSummaryCard('通过率', `${(model.pass_rate as number || 0).toFixed(1)}%`,
            (model.pass_rate as number || 0) >= 90 ? 'success' : 'warning')}
        </div>
        <Divider orientation="left" plain>GitHub 活动</Divider>
        <div className="report-summary-grid">
          {renderSummaryCard('PR', gh.pr_count as number || 0)}
          {renderSummaryCard('Issue', gh.issue_count as number || 0)}
          {renderSummaryCard('Commit', gh.commit_count as number || 0)}
          {renderSummaryCard('AI 概要', (gh.ai_summary_snippet || report.ai_report_content) ? '有' : '无', (gh.ai_summary_snippet || report.ai_report_content) ? 'success' : '')}
        </div>
        {report.ai_report_content && (
          <div style={{ marginTop: 16 }}>
            <div style={{ 
              fontSize: 14, 
              fontWeight: 600, 
              color: '#1e293b', 
              marginBottom: 8,
              paddingBottom: 8,
              borderBottom: '2px solid #f1f5f9'
            }}>
              📊 LLM 运行报告
            </div>
            <div style={{ 
              maxHeight: 600, 
              overflowY: 'auto',
              padding: 16,
              background: '#f8fafc',
              borderRadius: 8,
              border: '1px solid #e2e8f0'
            }}>
              <ReactMarkdown 
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({node: _node, ...props}) => <h1 style={{fontSize: 20, fontWeight: 700, color: '#1e293b', margin: '20px 0 12px', paddingBottom: 8, borderBottom: '2px solid #e5e7eb'}} {...props} />,
                  h2: ({node: _node, ...props}) => <h2 style={{fontSize: 17, fontWeight: 600, color: '#1e293b', margin: '18px 0 10px'}} {...props} />,
                  h3: ({node: _node, ...props}) => <h3 style={{fontSize: 15, fontWeight: 600, color: '#334155', margin: '14px 0 8px'}} {...props} />,
                  p: ({node: _node, ...props}) => <p style={{margin: '8px 0', lineHeight: 1.7}} {...props} />,
                  ul: ({node: _node, ...props}) => <ul style={{margin: '8px 0', paddingLeft: 24}} {...props} />,
                  ol: ({node: _node, ...props}) => <ol style={{margin: '8px 0', paddingLeft: 24}} {...props} />,
                  li: ({node: _node, ...props}) => <li style={{margin: '4px 0'}} {...props} />,
                  table: ({node: _node, ...props}) => <table style={{width: '100%', borderCollapse: 'collapse', margin: '12px 0', fontSize: 13}} {...props} />,
                  th: ({node: _node, ...props}) => <th style={{background: '#f1f5f9', padding: '8px 12px', textAlign: 'left', border: '1px solid #e2e8f0', fontWeight: 600, color: '#1e293b'}} {...props} />,
                  td: ({node: _node, ...props}) => <td style={{padding: '8px 12px', border: '1px solid #e2e8f0'}} {...props} />,
                  code: ({node: _node, ...props}) => <code style={{background: '#f1f5f9', padding: '2px 6px', borderRadius: 3, fontSize: 13, color: '#d97706'}} {...props} />,
                  pre: ({node: _node, ...props}) => <pre style={{background: '#1e293b', color: '#e2e8f0', padding: 14, borderRadius: 6, overflowX: 'auto', margin: '12px 0'}} {...props} />,
                  blockquote: ({node: _node, ...props}) => <blockquote style={{borderLeft: '4px solid #635bff', padding: '8px 16px', margin: '12px 0', background: '#f8fafc', color: '#475569'}} {...props} />,
                  strong: ({node: _node, ...props}) => <strong style={{fontWeight: 700, color: '#1e293b'}} {...props} />,
                  a: ({node: _node, ...props}) => <a style={{color: '#635bff', textDecoration: 'none'}} {...props} />,
                }}
              >
                {report.ai_report_content}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </Card>
    )
  }

  const historyColumns: ColumnsType<DailyReportHistoryItem> = [
    {
      title: '报告日期',
      dataIndex: 'report_date',
      key: 'report_date',
      width: 120,
      sorter: (a, b) => a.report_date.localeCompare(b.report_date),
      defaultSortOrder: 'descend',
    },
    {
      title: '主题',
      dataIndex: 'subject',
      key: 'subject',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Space size={4}>
          <span className={`report-status-dot ${status}`} />
          <Tag color={status === 'sent' ? 'green' : status === 'failed' ? 'red' : 'orange'}>
            {reportStatusLabel(status)}
          </Tag>
        </Space>
      ),
    },
    {
      title: '收件人',
      dataIndex: 'recipients',
      key: 'recipients',
      ellipsis: true,
      width: 200,
    },
    {
      title: '发送时间',
      dataIndex: 'sent_at',
      key: 'sent_at',
      width: 180,
      render: (v: string | null) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      key: 'error_message',
      ellipsis: true,
      render: (v: string | null) => v ? <Text type="danger" ellipsis>{v}</Text> : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (v: string) => dayjs(v).format('YYYY-MM-DD HH:mm:ss'),
    },
  ]

  return (
    <div className="stripe-page-container">
      <div className="stripe-page-header">
        <div className="report-page-heading">
          <span className="report-page-kicker">REPORTING OPERATIONS</span>
          <Title level={3} className="stripe-page-title">每日运行报告</Title>
          <Text>把分散的工程信号整理成可审核、可行动、可追踪的社区日报。</Text>
        </div>
        {isSuperAdmin && (
          <Space>
            <Tooltip title="生成草稿，不会立即发送邮件">
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={triggering}
                onClick={handleGenerateDraft}
                className="stripe-btn-primary"
              >
                生成日报草稿
              </Button>
            </Tooltip>
          </Space>
        )}
      </div>

      <section className="report-overview" aria-label="日报运行状态">
        <div className="report-overview-item">
          <span className="report-overview-icon"><ClockCircleOutlined /></span>
          <div><small>计划发送</small><strong>{config?.report_enabled ? `${String(config.report_schedule_hour).padStart(2, '0')}:${String(config.report_schedule_minute).padStart(2, '0')}` : '未启用'}</strong><em>Asia / Shanghai</em></div>
        </div>
        <div className="report-overview-item">
          <span className="report-overview-icon"><MailOutlined /></span>
          <div><small>收件范围</small><strong>{config?.report_recipients ? config.report_recipients.split(',').filter(Boolean).length : 0} 人</strong><em>{config?.report_cc_recipients ? '包含抄送人' : '无抄送'}</em></div>
        </div>
        <div className="report-overview-item">
          <span className="report-overview-icon"><HistoryOutlined /></span>
          <div><small>最新报告</small><strong>{latestData && !('message' in latestData) ? reportStatusLabel(latestData.status) : '暂无记录'}</strong><em>{latestData && !('message' in latestData) ? latestData.report_date : '等待首次生成'}</em></div>
        </div>
      </section>

      {config?.report_enabled && (
        <Alert
          type="info"
          showIcon
          icon={<ClockCircleOutlined />}
          message={`定时推送已启用：每天 ${config.report_schedule_hour}:${String(config.report_schedule_minute).padStart(2, '0')} (Asia/Shanghai) 自动发送`}
          style={{ marginBottom: 16 }}
        />
      )}

      {triggerResult && (
        <Alert
          type={triggerResult.success ? 'success' : 'error'}
          showIcon
          icon={triggerResult.success ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
          message={triggerResult.message}
          closable
          onClose={() => setTriggerResult(null)}
          style={{ marginBottom: 16 }}
        />
      )}

      {isSuperAdmin && (
        <Card
          className="stripe-card report-config-section"
          title={<Space><SettingOutlined />邮件推送配置</Space>}
          loading={configLoading}
        >
          <Form
            form={configForm}
            layout="vertical"
            initialValues={config || {}}
            onFinish={handleSaveConfig}
          >
            <Alert
              type="info"
              showIcon
              icon={<InfoCircleOutlined />}
              message={
                <span>
                  SMTP 邮件服务器配置已提取至独立页面，每日报告和告警规则共用。
                  <a href="/admin/smtp-config" style={{ marginLeft: 8 }}>前往配置 →</a>
                </span>
              }
              style={{ marginBottom: 16 }}
            />

            <Divider orientation="left" plain>发送时间</Divider>
            <Form.Item label="定时发送" help="每天定时发送日报的时间">
              <Space>
                <Form.Item name="report_schedule_hour" noStyle>
                  <Select style={{ width: 80 }}
                    options={Array.from({length:24},(_,i)=>({value:i, label:String(i).padStart(2,'0')}))}
                  />
                </Form.Item>
                <span>:</span>
                <Form.Item name="report_schedule_minute" noStyle>
                  <Select style={{ width: 80 }}
                    options={Array.from({length:12},(_,i)=>({value:i*5, label:String(i*5).padStart(2,'0')}))}
                  />
                </Form.Item>
              </Space>
            </Form.Item>
            <Form.Item label="启用状态" name="report_enabled" valuePropName="checked">
              <Switch checkedChildren="开" unCheckedChildren="关" />
            </Form.Item>

            <Divider orientation="left" plain>邮件内容</Divider>
<Form.Item label="收件人" name="report_recipients" rules={[{ required: true, message: '请输入收件人' }]}>
                  <Input placeholder="admin1@example.com, admin2@example.com" />
                </Form.Item>
                <div className="smtp-password-hint"><InfoCircleOutlined /> 多个收件人用逗号分隔</div>
                <Form.Item label="抄送" name="report_cc_recipients">
                  <Input placeholder="cc1@example.com, cc2@example.com" />
                </Form.Item>
                <Form.Item label="邮件主题模板" name="report_subject_template">
                  <Input placeholder="vLLM Ascend 运行报告 - {date}" />
                </Form.Item>
                <div className="smtp-password-hint"><InfoCircleOutlined /> {'{date}'} 会替换为报告日期</div>

            <Divider />
            <Space>
              <Button type="primary" htmlType="submit" loading={saving} icon={<SettingOutlined />}>
                保存配置
              </Button>
              <Button onClick={() => configForm.resetFields()}>
                重置
              </Button>
            </Space>
          </Form>
        </Card>
      )}

      <Row gutter={[20, 20]} className="report-workspace">
        <Col xs={24} xl={isSuperAdmin ? 14 : 24}>
          <Card
            className="stripe-card"
            title={<Space><HistoryOutlined />发送历史</Space>}
            extra={<Badge count={historyData?.total || 0} style={{ backgroundColor: '#635bff' }} />}
          >
            <Table
              className="report-history-table"
              dataSource={historyData?.items || []}
              columns={historyColumns}
              rowKey="id"
              loading={historyLoading}
              size="small"
              pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
            />
          </Card>
        </Col>
        {isSuperAdmin && (
          <Col xs={24} xl={10}>
            <Space direction="vertical" style={{ width: '100%' }} size={16}>
              <Card className="stripe-card" title={<Space><SendOutlined />手动触发</Space>} size="small">
                <Form layout="inline">
                  <Form.Item label="报告日期" style={{ flex: 1 }}>
                    <Input
                      placeholder="留空则默认昨天，格式 YYYY-MM-DD"
                      value={triggerDate}
                      onChange={(e) => setTriggerDate(e.target.value)}
                    />
                  </Form.Item>
                  <Form.Item>
                    <Button
                      type="primary"
                      icon={<SendOutlined />}
                      loading={triggering}
                      onClick={handleGenerateDraft}
                    >
                      生成草稿
                    </Button>
                  </Form.Item>
                </Form>
                <div className="smtp-password-hint" style={{ marginTop: 8 }}>
                  <InfoCircleOutlined /> 先生成草稿并检查内容，确认后才会发送邮件
                </div>
              </Card>
              {latestData && !('message' in latestData) && latestData.status === 'draft' && (
                <Alert
                  type="warning"
                  showIcon
                  message="草稿等待确认"
                  description="请检查下方数据和 AI 内容。发送时会使用当前预览，不会重新生成正文。"
                  action={<Space>
                    <Button loading={emailPreviewLoading} onClick={() => handlePreviewDraft(latestData.id)}>预览最终邮件</Button>
                    <Button type="primary" icon={<SendOutlined />} loading={sendDraft.isPending} onClick={() => handleSendDraft(latestData.id)}>确认并发送</Button>
                  </Space>}
                />
              )}
              {renderLatestReport()}
            </Space>
          </Col>
        )}
      </Row>
      <Modal
        title="最终邮件预览"
        open={emailPreviewOpen}
        onCancel={() => setEmailPreviewOpen(false)}
        footer={null}
        width={820}
        destroyOnClose
      >
        <iframe
          title="最终邮件预览"
          srcDoc={emailPreviewHtml}
          sandbox=""
          style={{ width: '100%', height: '72vh', border: 0, background: '#f5f7fa' }}
        />
      </Modal>
    </div>
  )
}

export default DailyReportConfigPage
