import { useParams, useNavigate } from 'react-router-dom'
import { Card, Button, Space, Typography, Tag, Descriptions, Timeline, Empty, Alert, message, Spin, Tooltip } from 'antd'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  SyncOutlined,
  GithubOutlined,
  RobotOutlined,
  FileSearchOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useJobDetail } from '../hooks/useCI'
import { useJobOwners } from '../hooks/useJobOwners'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useJobFailureAnalysis, useFailureAnalysisReport, useAnalyzeFailedJob } from '../hooks/useFailureAnalysis'
import { PROBLEM_CATEGORY_MAP, ANALYSIS_STATUS_MAP } from '../services/failureAnalysis'
import { formatTimezone } from '../utils/timezone'
import { renderStatusTag, renderConclusionTag, formatDuration, renderHardwareTag } from '../utils/ciRenderers'

const { Title, Text } = Typography

const renderStepStatus = (status: string, conclusion: string | null) => {
  if (status === 'completed') {
    if (conclusion === 'success') {
      return <Tag color="success" icon={<CheckCircleOutlined />}>成功</Tag>
    } else if (conclusion === 'failure') {
      return <Tag color="error" icon={<CloseCircleOutlined />}>失败</Tag>
    } else if (conclusion === 'skipped') {
      return <Tag color="default">跳过</Tag>
    }
    return <Tag color="warning">{conclusion || '-'}</Tag>
  } else if (status === 'in_progress') {
    return <Tag color="processing" icon={<SyncOutlined spin />}>进行中</Tag>
  } else if (status === 'queued') {
    return <Tag color="default" icon={<ClockCircleOutlined />}>等待中</Tag>
  }
  return <Tag color="default">{status}</Tag>
}

function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const jobIdNum = jobId ? parseInt(jobId) : null

  const { data: job, isLoading, refetch } = useJobDetail(jobIdNum)
  const { data: jobOwners } = useJobOwners()
  const { data: analysis, isLoading: analysisLoading } = useJobFailureAnalysis(jobIdNum)
  const { data: reportData, isLoading: reportLoading } = useFailureAnalysisReport(
    analysis?.analysis_status === 'completed' ? analysis?.id : null
  )
  const analyzeMutation = useAnalyzeFailedJob()
  const { data: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.role === 'admin' || currentUser?.role === 'super_admin'

  const [showFullReport, setShowFullReport] = useState(false)

  const ownerInfo = jobOwners?.find(
    (o) => o.workflow_name === job?.workflow_name && o.job_name === job?.job_name
  )
  const displayName = ownerInfo?.display_name

  useEffect(() => {
    if (analyzeMutation.isSuccess && analyzeMutation.data) {
      message.success('分析已提交，请等待完成...')
      refetch()
    }
  }, [analyzeMutation.isSuccess, analyzeMutation.data, refetch])

  useEffect(() => {
    if (analyzeMutation.isError) {
      const errorMsg = (analyzeMutation.error as any)?.response?.data?.detail ||
        (analyzeMutation.error as any)?.message || '分析失败，请稍后重试'
      message.error(errorMsg)
    }
  }, [analyzeMutation.isError, analyzeMutation.error])

  const handleRefresh = async () => {
    try {
      await refetch()
      message.success('数据已刷新')
    } catch (error) {
      message.error('刷新失败')
    }
  }

  const handleAnalyze = () => {
    if (!jobIdNum) return
    analyzeMutation.mutate({ jobId: jobIdNum, force: true })
  }

  const handleReAnalyze = () => {
    if (!jobIdNum) return
    analyzeMutation.mutate({ jobId: jobIdNum, force: true })
  }

  const isFailed = job?.conclusion === 'failure' || job?.conclusion === 'cancelled'

  const statusInfo = analysis
    ? ANALYSIS_STATUS_MAP[analysis.analysis_status] || { color: '#64748d', label: analysis.analysis_status }
    : null

  const categoryInfo = analysis?.problem_category
    ? PROBLEM_CATEGORY_MAP[analysis.problem_category] || { color: '#64748d', label: analysis.problem_category }
    : null

  if (isLoading) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <Empty description="加载中..." />
      </div>
    )
  }

  if (!job) {
    return (
      <div style={{ padding: 24 }}>
        <Alert
          message="Job 不存在"
          description="找不到该 Job 的信息"
          type="error"
          showIcon
        />
        <Button style={{ marginTop: 16 }} onClick={() => navigate('/ci')}>
          返回 CI 看板
        </Button>
      </div>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/ci/runs/${job.run_id}`)}>
          返回 Workflow 详情
        </Button>
        {job.github_job_url && (
          <Button
            icon={<GithubOutlined />}
            href={job.github_job_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            在 GitHub 上查看
          </Button>
        )}
        <Button icon={<SyncOutlined />} onClick={handleRefresh}>
          刷新
        </Button>
      </Space>

      <Title level={2} style={{ marginBottom: 24 }}>
        Job 详情
      </Title>

      <Card style={{ marginBottom: 24 }}>
        <Descriptions column={4} bordered>
          <Descriptions.Item label="Job 名称" span={2}>
            <Space direction="vertical" size={0}>
              <Text strong>{job.job_name}</Text>
              {displayName && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {displayName}
                </Text>
              )}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="Job ID">#{job.job_id}</Descriptions.Item>
          
          <Descriptions.Item label="Workflow">{job.workflow_name}</Descriptions.Item>
          <Descriptions.Item label="Run ID">#{job.run_id}</Descriptions.Item>
          <Descriptions.Item label="硬件">
            {renderHardwareTag(job.hardware)}
          </Descriptions.Item>
          <Descriptions.Item label="Runner">
            {job.runner_name || (job.runner_labels && job.runner_labels.length > 0 ? job.runner_labels.join(', ') : '-')}
          </Descriptions.Item>
          {job.model_fo ? (
            <Descriptions.Item label="模型 FO">{job.model_fo}</Descriptions.Item>
          ) : null}

          <Descriptions.Item label="状态">
            {renderStatusTag(job.status)}
          </Descriptions.Item>
          <Descriptions.Item label="结果">
            {renderConclusionTag(job.conclusion)}
          </Descriptions.Item>
          <Descriptions.Item label="时长">
            {formatDuration(job.duration_seconds)}
          </Descriptions.Item>

          <Descriptions.Item label="开始时间">
            {job.started_at ? formatTimezone(job.started_at) : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="完成时间">
            {job.completed_at ? formatTimezone(job.completed_at) : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatTimezone(job.created_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {job.runner_labels && job.runner_labels.length > 0 && (
        <Card title="Runner 标签" style={{ marginBottom: 24 }}>
          <Space wrap>
            {job.runner_labels.map((label: string, index: number) => (
              <Tag key={index} color="blue">{label}</Tag>
            ))}
          </Space>
        </Card>
      )}

      {isFailed && (
        <Card
          title={
            <Space>
              <FileSearchOutlined />
              <span>失败智能诊断</span>
              {statusInfo && <Tag color={statusInfo.color}>{statusInfo.label}</Tag>}
              {categoryInfo && <Tag color={categoryInfo.color}>{categoryInfo.label}</Tag>
              }
            </Space>
          }
          style={{ marginBottom: 24 }}
          extra={
            isAdmin ? (
              <Space>
                <Button
                  icon={<RobotOutlined />}
                  loading={analyzeMutation.isPending}
                  onClick={analysis ? handleReAnalyze : handleAnalyze}
                  type="primary"
                  size="small"
                >
                  {analysis ? '重新分析' : '开始分析'}
                </Button>
              </Space>
            ) : null
          }
        >
          {analyzeMutation.isPending && (
            <div style={{ textAlign: 'center', padding: '40px 20px' }}>
              <Spin size="large" />
              <div style={{ marginTop: 16, color: '#8c8c8c' }}>
                <p>AI 正在分析失败原因，请耐心等待...</p>
              </div>
            </div>
          )}

          {!analyzeMutation.isPending && !analysis && (
            <Empty
              description={isAdmin ? "该失败 Job 尚未进行智能诊断分析" : "暂无分析结果（仅管理员可触发分析）"}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            >
              {isAdmin && (
                <Button type="primary" icon={<RobotOutlined />} onClick={handleAnalyze}>
                  开始分析
                </Button>
              )}
            </Empty>
          )}

          {!analyzeMutation.isPending && analysis && (
            <div>
              <Descriptions column={2} bordered size="small">
                <Descriptions.Item label="问题分类">
                  {categoryInfo ? (
                    <Tag color={categoryInfo.color}>{categoryInfo.label}</Tag>
                  ) : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="分析状态">
                  {statusInfo ? (
                    <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
                  ) : '-'}
                </Descriptions.Item>
                <Descriptions.Item label="根因摘要" span={2}>
                  {analysis.root_cause_summary || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="改进建议" span={2}>
                  {analysis.improvement_measures_summary || '-'}
                </Descriptions.Item>
                {analysis.llm_provider && (
                  <Descriptions.Item label="LLM">
                    {analysis.llm_provider}/{analysis.llm_model || '-'}
                  </Descriptions.Item>
                )}
                {analysis.generation_time_seconds && (
                  <Descriptions.Item label="耗时">
                    {analysis.generation_time_seconds.toFixed(1)}s
                  </Descriptions.Item>
                )}
                {analysis.reused_analysis_id && (
                  <Descriptions.Item label="复用分析" span={2}>
                    <Text type="secondary">复用分析 #{analysis.reused_analysis_id}（相同失败指纹）</Text>
                  </Descriptions.Item>
                )}
                {analysis.error_message && (
                  <Descriptions.Item label="错误信息" span={2}>
                    <Text type="danger">{analysis.error_message}</Text>
                  </Descriptions.Item>
                )}
              </Descriptions>

              {analysis.analysis_status === 'completed' && reportData?.content && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <Text strong>详细分析报告</Text>
                    <Button
                      size="small"
                      type="link"
                      onClick={() => setShowFullReport(!showFullReport)}
                    >
                      {showFullReport ? '收起' : '展开完整报告'}
                    </Button>
                  </div>
                  {showFullReport && (
                    <div style={{ padding: 16, background: '#fafafa', borderRadius: 8, maxHeight: '500px', overflowY: 'auto' }}>
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          table: ({ node, ...props }) => (
                            <div style={{ overflowX: 'auto', marginBottom: 16 }}>
                              <table style={{ borderCollapse: 'collapse', width: '100%' }} {...props} />
                            </div>
                          ),
                          th: ({ node, ...props }) => (
                            <th style={{ border: '1px solid #d9d9d9', padding: '8px 12px', background: '#f5f5f5', textAlign: 'left' }} {...props} />
                          ),
                          td: ({ node, ...props }) => (
                            <td style={{ border: '1px solid #d9d9d9', padding: '8px 12px', verticalAlign: 'top' }} {...props} />
                          ),
                        }}
                      >
                        {reportData.content}
                      </ReactMarkdown>
                    </div>
                  )}
                </div>
              )}

              {analysis.analysis_status === 'analyzing' && (
                <div style={{ textAlign: 'center', padding: 20 }}>
                  <Spin tip="分析进行中..." />
                  <div style={{ marginTop: 12, color: '#8c8c8c' }}>
                    <p>系统正在自动分析中，请稍后刷新查看结果</p>
                  </div>
                </div>
              )}

              {analysis.analysis_status === 'failed' && (
                <Alert
                  message="分析失败"
                  description={analysis.error_message || '请点击重新分析按钮重试'}
                  type="error"
                  showIcon
                  style={{ marginTop: 16 }}
                  action={
                    <Button size="small" onClick={handleReAnalyze} loading={analyzeMutation.isPending}>
                      重新分析
                    </Button>
                  }
                />
              )}
            </div>
          )}
        </Card>
      )}

      <Card title="Steps 详情" style={{ marginBottom: 24 }}>
        {job.steps_data && job.steps_data.length > 0 ? (
          <Timeline
            items={job.steps_data.map((step: any, index: number) => {
              const isFailure = step.conclusion === 'failure'
              
              return {
                key: index,
                color: isFailure ? 'red' : step.conclusion === 'success' ? 'green' : 'gray',
                children: (
                  <div style={{ padding: '12px 0' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <Space>
                        <Text strong>Step {step.number}: {step.name}</Text>
                        {isFailure && (
                          <Tag color="error" icon={<CloseCircleOutlined />}>失败</Tag>
                        )}
                      </Space>
                      {renderStepStatus(step.status, step.conclusion)}
                    </div>
                    
                    {isFailure && (
                      <Alert
                        message={
                          <Space>
                            <span>此步骤失败，请查看 GitHub 日志获取详细信息</span>
                            <Button
                              type="link"
                              size="small"
                              icon={<GithubOutlined />}
                              href={job.github_job_url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              在 GitHub 上查看日志
                            </Button>
                          </Space>
                        }
                        type="error"
                        showIcon
                        style={{ marginTop: 8 }}
                        icon={<CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
                      />
                    )}
                  </div>
                ),
              }
            })}
          />
        ) : (
          <Empty description="暂无 Steps 信息" />
        )}
      </Card>
    </div>
  )
}

export default JobDetail
