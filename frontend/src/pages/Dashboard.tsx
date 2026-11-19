import { Card, Col, Row, Tag, Typography, Space, Skeleton, Progress, Timeline, Button, Divider } from 'antd'
import {
  AlertOutlined,
  ArrowRightOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  GithubOutlined,
  SyncOutlined,
  FileTextOutlined,
  ExperimentOutlined
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import api from '../services/api'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'
import { formatTimezone } from '../utils/timezone'
import { renderStatusTag, renderConclusionTag, formatDuration } from '../utils/ciRenderers'
import { SystemStatus } from '../types/systemConfig'
import { useState, useEffect } from 'react'
import { useSyncProgress } from '../hooks/useCI'
import GitHubActivityPanel from '../components/GitHubActivityPanel'
import './Dashboard.css'

const { Text, Title } = Typography

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

interface WorkflowLatestResult {
  workflow_name: string
  hardware: string | null
  latest_run: {
    run_id: number
    status: string
    conclusion: string | null
    started_at: string | null
    duration_seconds: number | null
    github_html_url?: string
  } | null
}

interface ModelLatestResult {
  model_id: number
  model_name: string
  series: string
  report_id?: number | null
  status?: string | null
  accuracy?: number | null
  throughput?: number | null
  first_token_latency?: number | null
  created_at?: string | null
  github_html_url?: string | null
}

function Dashboard() {
  const navigate = useNavigate()

  // 获取系统状态（包含下次同步时间）
  const { data: systemStatus } = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: async () => {
      const response = await api.get<SystemStatus>('/system/config/status')
      return response.data
    },
    retry: false,
  })

  // 倒计时状态
  const [isSyncing, setIsSyncing] = useState<boolean>(false)

  // 获取同步进度（每 2 秒轮询）
  const { data: progress } = useSyncProgress(isSyncing)

  // 监听同步状态变化
  useEffect(() => {
    if (progress?.status === 'completed') {
      setIsSyncing(false)
      // 同步完成后刷新页面数据
      setTimeout(() => {
        window.location.reload()
      }, 1000)
    } else if (progress?.status === 'failed') {
      setIsSyncing(false)
    }
  }, [progress])

  // 获取每个 workflow 最近一次的 job 结果
  const {
    data: workflowResults,
    isLoading: resultsLoading,
    isError: resultsError,
    refetch: refetchWorkflowResults,
  } = useQuery<WorkflowLatestResult[]>({
    queryKey: ['workflow-latest-results'],
    queryFn: async () => {
      const response = await api.get<WorkflowLatestResult[]>('/ci/workflows/latest')
      return response.data
    },
  })

  // 获取模型最新结果
  const {
    data: modelResults,
    isLoading: modelResultsLoading,
    isError: modelResultsError,
    refetch: refetchModelResults,
  } = useQuery<ModelLatestResult[]>({
    queryKey: ['models-latest-results'],
    queryFn: async () => {
      const response = await api.get<ModelLatestResult[]>('/models/latest-results')
      return response.data
    },
  })

  // 处理卡片点击
  const handleCardClick = (runId: number) => {
    navigate(`/ci/runs/${runId}`)
  }

  // 处理报告页面点击
  const handleReportClick = (date: string) => {
    navigate(`/ci/reports/${date}`)
  }

  // 处理模型报告页面点击
  const handleModelReportClick = (date: string) => {
    navigate(`/models/reports/${date}`)
  }

  // 处理 GitHub 图标点击（阻止冒泡）
  const handleGithubIconClick = (e: React.MouseEvent, url?: string) => {
    e.preventDefault()
    e.stopPropagation()
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }

  // 获取今日日期 (YYYY-MM-DD 格式)
  const today = dayjs().format('YYYY-MM-DD')
  const failedConclusions = new Set(['failure', 'timed_out', 'startup_failure', 'action_required'])
  const workflowTotal = workflowResults?.length || 0
  const workflowSuccess = workflowResults?.filter((item) => item.latest_run?.conclusion === 'success').length || 0
  const workflowFailures = workflowResults?.filter((item) => failedConclusions.has(item.latest_run?.conclusion || '')).length || 0
  const workflowRunning = workflowResults?.filter((item) => item.latest_run?.status === 'in_progress').length || 0
  const modelTotal = modelResults?.length || 0
  const modelSuccess = modelResults?.filter((item) => item.status === 'success').length || 0
  const healthScore = workflowTotal ? Math.round((workflowSuccess / workflowTotal) * 100) : null
  const nextSync = systemStatus?.scheduler.tasks.ci_sync.next_sync
  const lastUpdated = systemStatus?.timestamp

  return (
    <div className="stripe-dashboard-page">
      <div className="dashboard-hero">
        <div>
          <div className="dashboard-eyebrow">COMMUNITY OPERATIONS</div>
          <Title level={2} className="stripe-dashboard-title">社区运营总览</Title>
          <Text className="stripe-dashboard-subtitle">
            聚焦当前风险、交付质量与模型验证状态，快速决定下一步行动。
          </Text>
        </div>
        <div className="dashboard-hero-meta">
          <span className={`system-health-pill ${systemStatus?.database.connected ? 'is-healthy' : 'is-warning'}`}>
            {systemStatus?.database.connected ? <CheckCircleFilled /> : <AlertOutlined />}
            {systemStatus?.database.connected ? '核心服务正常' : '服务状态待确认'}
          </span>
          <span className="dashboard-updated-at">
            <ClockCircleOutlined />
            {lastUpdated ? `更新于 ${dayjs(lastUpdated).fromNow()}` : '正在获取最新状态'}
          </span>
        </div>
      </div>

      <section className="executive-summary" aria-label="关键运营指标">
        <button className="executive-metric executive-metric-primary" onClick={() => navigate('/ci')}>
          <span className="executive-metric-label">CI 健康度</span>
          <strong>{healthScore === null ? '—' : `${healthScore}%`}</strong>
          <span>{workflowSuccess}/{workflowTotal || 0} 个 Workflow 最近运行成功</span>
          <ArrowRightOutlined className="executive-metric-arrow" />
        </button>
        <button className={`executive-metric ${workflowFailures ? 'executive-metric-danger' : ''}`} onClick={() => navigate('/ci')}>
          <span className="executive-metric-label">需要关注</span>
          <strong>{workflowFailures}</strong>
          <span>{workflowFailures ? '失败或阻塞的 CI 运行' : '当前没有阻塞项'}</span>
          <ArrowRightOutlined className="executive-metric-arrow" />
        </button>
        <button className="executive-metric" onClick={() => navigate('/models')}>
          <span className="executive-metric-label">模型验证</span>
          <strong>{modelSuccess}<small> / {modelTotal}</small></strong>
          <span>最新验证成功的模型</span>
          <ArrowRightOutlined className="executive-metric-arrow" />
        </button>
        <button className="executive-metric" onClick={() => navigate('/ci')}>
          <span className="executive-metric-label">运行态</span>
          <strong>{workflowRunning}</strong>
          <span>{nextSync ? `下次同步 ${dayjs(nextSync).format('HH:mm')}` : '等待同步计划'}</span>
          <ArrowRightOutlined className="executive-metric-arrow" />
        </button>
      </section>

      <div className={`attention-banner ${workflowFailures ? 'has-risk' : 'is-clear'}`}>
        <div className="attention-banner-icon">
          {workflowFailures ? <AlertOutlined /> : <CheckCircleFilled />}
        </div>
        <div className="attention-banner-copy">
          <strong>{workflowFailures ? `${workflowFailures} 个交付风险需要处理` : '当前交付链路保持稳定'}</strong>
          <span>{workflowFailures ? '建议先检查失败 CI，再进入 AI 问题定位完成根因分析。' : '可以继续关注 PR 推进和模型验证趋势。'}</span>
        </div>
        <Button
          type={workflowFailures ? 'primary' : 'default'}
          onClick={() => navigate(workflowFailures ? '/issue-diagnosis' : '/pr-pipeline')}
        >
          {workflowFailures ? '开始诊断' : '查看 PR 流水线'}
        </Button>
      </div>

      {/* Sync Progress Card */}
      <Row gutter={16} className="stripe-dashboard-row">
        <Col span={24}>
          {isSyncing && progress?.status === 'running' ? (
            <Card className="stripe-card stripe-sync-card">
              <div className="stripe-card-header">
                <Space>
                  <SyncOutlined spin className="stripe-sync-icon" />
                  <span className="stripe-card-title">正在同步 CI 数据</span>
                </Space>
              </div>
              
              <div className="stripe-progress-section">
                <Progress
                  percent={progress?.progress_percentage || 0}
                  format={() => `${progress?.completed_workflows || 0}/${progress?.total_workflows || 0} workflows`}
                  strokeColor={{
                    '0%': '#533afd',
                    '100%': '#665efd',
                  }}
                />
              </div>

              <div className="stripe-stats-section">
                <Space size="large" className="stripe-stats-container">
                  <div className="stripe-stat-item">
                    <div className="stripe-stat-value">{progress?.total_collected || 0}</div>
                    <div className="stripe-stat-label">已采集记录</div>
                  </div>
                  <div className="stripe-stat-item">
                    <div className="stripe-stat-value">{progress?.completed_workflows || 0}</div>
                    <div className="stripe-stat-label">已完成 workflow</div>
                  </div>
                </Space>
              </div>

              {progress?.current_workflow && (
                <div className="stripe-current-workflow">
                  <Text type="secondary">
                    当前处理：<Text strong>{progress.current_workflow}</Text>
                  </Text>
                </div>
              )}

              {progress?.workflow_details && Object.keys(progress.workflow_details).length > 0 && (
                <Card title="Workflow 详情" size="small" className="stripe-sub-card">
                  <Timeline
                    items={Object.entries(progress.workflow_details).map(([name, detail]: [string, any]) => ({
                      color: detail.status === 'completed' ? '#15be53' : detail.status === 'failed' ? '#ff4d4f' : '#533afd',
                      children: (
                        <Space direction="vertical" size={0} className="stripe-timeline-item">
                          <div className="stripe-timeline-header">
                            <Text strong className="stripe-timeline-title">{name}</Text>
                            <Tag className={`stripe-tag-${detail.status}`}>
                              {detail.status === 'completed' ? '完成' : detail.status === 'failed' ? '失败' : '进行中'}
                            </Tag>
                          </div>
                          <Text type="secondary" className="stripe-timeline-meta">
                            采集 {detail.collected} 条记录 | {new Date(detail.updated_at).toLocaleTimeString()}
                          </Text>
                        </Space>
                      ),
                    }))}
                  />
                </Card>
              )}
            </Card>
          ) : null}
        </Col>
      </Row>

      {/* CI Latest Results */}
      <div className="stripe-dashboard-section">
        <Title level={4} className="stripe-section-title">
          <FileTextOutlined className="stripe-section-icon" />
          CI 最新结果
        </Title>
        <Card className="stripe-card stripe-section-card">
          <div className="stripe-card-header-with-action">
            <Space>
              <Button
                type="default"
                size="small"
                icon={<FileTextOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  handleReportClick(today)
                }}
                className="stripe-btn-ghost stripe-btn-sm"
              >
                今日报告详情
              </Button>
            </Space>
          </div>

        {resultsError ? (
          <div className="dashboard-error-state" role="alert">
            <AlertOutlined />
            <div><strong>CI 数据暂时不可用</strong><span>这不是“暂无数据”，请重试或检查后端服务。</span></div>
            <Button onClick={() => refetchWorkflowResults()}>重新加载</Button>
          </div>
        ) : resultsLoading ? (
          <Row gutter={16} className="stripe-results-grid">
            {[1, 2, 3, 4].map((i) => (
              <Col xs={24} sm={12} xl={6} key={i}>
                <Skeleton active />
              </Col>
            ))}
          </Row>
        ) : workflowResults && workflowResults.length > 0 ? (
          <Row gutter={[16, 16]} className="stripe-results-grid">
            {workflowResults.map((result) => {
              // 根据结论确定卡片的强调色
              const getAccentColor = (conclusion: string | null | undefined) => {
                if (conclusion === 'success') return '#52c41a'
                if (conclusion === 'failure' || conclusion === 'timed_out' || conclusion === 'startup_failure')
                  return '#ff4d4f'
                if (conclusion === 'cancelled' || conclusion === 'action_required') return '#faad14'
                return '#1890ff'
              }

              const accentColor = getAccentColor(result.latest_run?.conclusion)

              return (
                <Col xs={24} sm={12} xl={6} key={result.workflow_name}>
                  <Card
                    hoverable
                    size="small"
                    className="stripe-result-card"
                    style={{ borderTop: `3px solid ${accentColor}` }}
                    onClick={() => result.latest_run?.run_id && handleCardClick(result.latest_run.run_id)}
                    bodyStyle={{ padding: '0' }}
                  >
                    <div className="stripe-result-card-content">
                      {/* Title Area */}
                      <div className="stripe-result-card-header">
                        <div className="stripe-result-card-title-row">
                          <Text
                            strong
                            ellipsis
                            className="stripe-result-card-title"
                            title={result.workflow_name}
                          >
                            {result.workflow_name}
                          </Text>
                          {result.latest_run?.github_html_url && (
                            <a
                              href={result.latest_run.github_html_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => handleGithubIconClick(e, result.latest_run?.github_html_url)}
                              className="stripe-github-link"
                              title="在 GitHub 上查看"
                            >
                              <GithubOutlined />
                            </a>
                          )}
                        </div>
                        {/* Hardware Tag */}
                        {result.hardware && result.hardware !== 'unknown' && (
                          <Tag
                            color={result.hardware === 'A2' ? 'green' : 'purple'}
                            className="stripe-hardware-tag"
                          >
                            {result.hardware}
                          </Tag>
                        )}
                      </div>
                    </div>

                    {/* Status and Results */}
                    <div className="stripe-result-card-body">
                      <div className="stripe-result-metrics">
                        <div className="stripe-metric-item">
                          <div className="stripe-metric-label">状态</div>
                          <div>{renderStatusTag(result.latest_run?.status || 'unknown')}</div>
                        </div>
                        <div className="stripe-metric-item">
                          <div className="stripe-metric-label">结果</div>
                          <div>{renderConclusionTag(result.latest_run?.conclusion || null)}</div>
                        </div>
                      </div>
                    </div>

                    {/* Divider */}
                    <Divider className="stripe-result-divider" />

                    {/* Footer Info */}
                    <div className="stripe-result-card-footer">
                      <div className="stripe-result-footer-row">
                        <div>
                          <div className="stripe-footer-label">耗时</div>
                          <Text className="stripe-footer-value">
                            {formatDuration(result.latest_run?.duration_seconds || null)}
                          </Text>
                        </div>
                        {result.latest_run?.started_at && (
                          <div className="stripe-footer-item-right">
                            <div className="stripe-footer-label">运行时间</div>
                            <Text className="stripe-footer-value">
                              {formatTimezone(result.latest_run.started_at, 'MM-DD HH:mm')}
                            </Text>
                          </div>
                        )}
                      </div>
                    </div>
                  </Card>
                </Col>
              )
            })}
          </Row>
        ) : (
          <div className="stripe-empty-state">
            <FileTextOutlined className="stripe-empty-icon" />
            <p className="stripe-empty-text">暂无 Workflow 数据</p>
          </div>
        )}
        </Card>
      </div>

      {/* Model Latest Results */}
      <div className="stripe-dashboard-section">
        <Title level={4} className="stripe-section-title">
          <ExperimentOutlined className="stripe-section-icon" />
          模型最新结果
        </Title>
        <Card
          className="stripe-card stripe-section-card stripe-models-card"
        >
          <div className="stripe-card-header-with-action">
            <Space>
              <Button
                type="default"
                size="small"
                icon={<ExperimentOutlined />}
                onClick={() => handleModelReportClick(dayjs().format('YYYY-MM-DD'))}
                className="stripe-btn-ghost stripe-btn-sm"
              >
                今日报告详情
              </Button>
            </Space>
          </div>

        {modelResultsError ? (
          <div className="dashboard-error-state" role="alert">
            <AlertOutlined />
            <div><strong>模型结果暂时不可用</strong><span>服务请求失败，请稍后重试。</span></div>
            <Button onClick={() => refetchModelResults()}>重新加载</Button>
          </div>
        ) : modelResultsLoading ? (
          <Row gutter={[16, 16]} className="stripe-results-grid">
            {[1, 2, 3, 4].map((i) => (
              <Col xs={24} sm={12} xl={6} key={i}>
                <Skeleton active />
              </Col>
            ))}
          </Row>
        ) : modelResults && modelResults.length > 0 ? (
          <Row gutter={[16, 16]} className="stripe-results-grid">
            {modelResults.map((model) => {
              // 根据状态确定卡片的强调色
              const getAccentColor = (status: string | null | undefined) => {
                if (status === 'success') return '#52c41a'
                if (status === 'failure') return '#ff4d4f'
                if (status === 'running') return '#1890ff'
                return '#d9d9d9'
              }

              const accentColor = getAccentColor(model.status)

              return (
                <Col xs={24} sm={12} xl={6} key={model.model_id}>
                  <Card
                    hoverable
                    size="small"
                    className="stripe-model-card"
                    style={{ borderTop: `3px solid ${accentColor}` }}
                    onClick={() => {
                      if (model.report_id) {
                        navigate(`/models/${model.model_id}`)
                      }
                    }}
                    bodyStyle={{ padding: 0 }}
                  >
                    <div className="stripe-model-card-body">
                      {/* Model Name */}
                      <div className="stripe-model-card-header">
                        <Text strong className="stripe-model-card-title" title={model.model_name}>
                          {model.model_name}
                        </Text>
                        {model.series && (
                          <div className="stripe-model-series">系列：{model.series}</div>
                        )}
                      </div>

                      {/* Status */}
                      <div className="stripe-model-status">
                        <div className="stripe-model-status-label">状态</div>
                        {model.status === 'success' && <Tag color="success">成功</Tag>}
                        {model.status === 'failure' && <Tag color="error">失败</Tag>}
                        {model.status === 'running' && <Tag color="processing" icon={<SyncOutlined spin />}>运行中</Tag>}
                        {!model.status && <Tag>暂无报告</Tag>}
                      </div>

                      {/* Metrics */}
                      {model.accuracy !== null && model.accuracy !== undefined && (
                        <div className="stripe-model-metric">
                          <div className="stripe-model-metric-label">准确率</div>
                          <Text strong className="stripe-model-metric-value">{(model.accuracy * 100).toFixed(2)}%</Text>
                        </div>
                      )}
                      {model.throughput !== null && model.throughput !== undefined && (
                        <div className="stripe-model-metric">
                          <div className="stripe-model-metric-label">吞吐量</div>
                          <Text strong className="stripe-model-metric-value">{model.throughput.toFixed(2)} tok/s</Text>
                        </div>
                      )}
                      {model.first_token_latency !== null && model.first_token_latency !== undefined && (
                        <div className="stripe-model-metric">
                          <div className="stripe-model-metric-label">首 Token 延迟</div>
                          <Text strong className="stripe-model-metric-value">{model.first_token_latency.toFixed(2)} ms</Text>
                        </div>
                      )}

                      {/* Footer */}
                      <div className="stripe-model-card-footer">
                        {model.github_html_url && (
                          <a
                            href={model.github_html_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => {
                              e.preventDefault()
                              e.stopPropagation()
                              if (model.github_html_url) {
                                window.open(model.github_html_url, '_blank', 'noopener,noreferrer')
                              }
                            }}
                            className="stripe-model-github-link"
                          >
                            <GithubOutlined /> 查看运行
                          </a>
                        )}
                        {model.created_at && (
                          <div className="stripe-model-time">
                            <div className="stripe-model-time-label">运行时间</div>
                            <Text>{dayjs(model.created_at).format('MM-DD HH:mm')}</Text>
                          </div>
                        )}
                      </div>
                    </div>
                  </Card>
                </Col>
              )
            })}
          </Row>
        ) : (
          <div className="stripe-empty-state">
            <ExperimentOutlined className="stripe-empty-icon" />
            <p className="stripe-empty-text">暂无模型数据</p>
          </div>
        )}
        </Card>
      </div>

      <div className="stripe-dashboard-section dashboard-activity-section">
        <Title level={4} className="stripe-section-title">
          <GithubOutlined className="stripe-section-icon" />
          社区项目动态
        </Title>
        <GitHubActivityPanel />
      </div>
    </div>
  )
}

export default Dashboard
