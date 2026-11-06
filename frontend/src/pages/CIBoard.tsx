import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import dayjs from 'dayjs'
import { Alert, Card, Space, Statistic, Row, Col, Typography, Tabs, Button, message, Modal } from 'antd'
import {
  GithubOutlined,
  BarChartOutlined,
  RobotOutlined,
  ExclamationCircleOutlined,
  SettingOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { useCIStats, useRuns } from '../hooks/useCI'
import { useAnalyzeBatch } from '../hooks/useFailureAnalysis'
import JobBoard from './JobBoard'
import DailyFailureTracking from './DailyFailureTracking'
import NightlyTestCaseConfig from './NightlyTestCaseConfig'
import CIDailyComprehensiveReport from './CIDailyComprehensiveReport'
import WorkflowTestExecutionTable from '../components/WorkflowTestExecutionTable'
import CIBuildDurationChart from '../components/CIBuildDurationChart'
import CIBuildTimeRange, { type CIBuildTimeRangeValue } from '../components/CIBuildTimeRange'
import { toBuildStatsSummary } from '../utils/ciBuildStats'
import './CIBoard.css'

const { Text, Title } = Typography

const CI_BOARD_TABS = ['workflow', 'job', 'daily-failure', 'daily-report', 'test-case-config'] as const
type CIBoardTab = typeof CI_BOARD_TABS[number]
const CI_BOARD_TAB_STORAGE_KEY = 'ci-board-active-tab'

function isCIBoardTab(value: string | null): value is CIBoardTab {
  return value !== null && (CI_BOARD_TABS as readonly string[]).includes(value)
}

function CIBoard() {
  const [searchParams] = useSearchParams()
  const [buildTimeRange, setBuildTimeRange] = useState<CIBuildTimeRangeValue>(() => {
    const end = dayjs().endOf('day')
    return { start: end.subtract(13, 'day').startOf('day'), end, preset: '14d' }
  })

  // 根据 URL 参数设置默认 Tab
  const [activeTab, setActiveTab] = useState(() => {
    const requestedTab = searchParams.get('tab')
    const storedTab = typeof window !== 'undefined'
      ? window.localStorage.getItem(CI_BOARD_TAB_STORAGE_KEY)
      : null
    const tab = requestedTab || storedTab
    return isCIBoardTab(tab) ? tab : 'workflow'
  })

  useEffect(() => {
    window.localStorage.setItem(CI_BOARD_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  const timeRangeParams = {
    start_time: buildTimeRange.start.toISOString(),
    end_time: buildTimeRange.end.toISOString(),
  }
  const { data: stats, isLoading: statsLoading, isError: statsError } = useCIStats(timeRangeParams)
  const buildStats = toBuildStatsSummary(stats)
  const { data: buildRuns = [], isLoading: buildRunsLoading } = useRuns({
    ...timeRangeParams,
    limit: 5000,
  })

  const analyzeBatchMutation = useAnalyzeBatch()

  const handleBatchAnalyze = () => {
    Modal.confirm({
      title: '批量失败分析',
      content: '确定要对最近 7 天的失败 Job 进行批量 AI 分析吗？这可能需要较长时间。',
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        analyzeBatchMutation.mutate({ daysBack: 7 }, {
          onSuccess: (data) => {
            message.success(data.message || '批量分析完成')
          },
          onError: (error: unknown) => {
            const detail = (error as { response?: { data?: { detail?: string } } })
              .response?.data?.detail
            message.error(detail || '批量分析失败')
          },
        })
      },
    })
  }

  return (
    <div className="stripe-ci-page">
      {/* 页面标题 */}
      <div className="stripe-page-header">
        <Title level={3} className="stripe-page-title">
          CI 看板
        </Title>
        <Text className="stripe-page-description">
          查看 CI 运行状态和统计信息
        </Text>
      </div>

      <Tabs
          activeKey={activeTab}
        onChange={(tab) => {
          if (isCIBoardTab(tab)) setActiveTab(tab)
        }}
        items={[
          {
            key: 'workflow',
            label: (
              <Space>
                <GithubOutlined />
                <span>Workflow 运行</span>
              </Space>
            ),
            children: (
              <div>
                {/* 页面标题和操作区 */}
                <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <Title level={3} style={{ margin: 0 }}>
                      Workflow 运行
                    </Title>
                    <Text type="secondary">
                      展示各 Workflow 的运行状态和趋势
                    </Text>
                  </div>
                  <Space>
                    <Button
                      icon={<RobotOutlined />}
                      loading={analyzeBatchMutation.isPending}
                      onClick={handleBatchAnalyze}
                    >
                      批量失败分析
                    </Button>
                  </Space>
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 20 }}>
                  <CIBuildTimeRange value={buildTimeRange} onApply={setBuildTimeRange} />
                </div>

                {statsError && (
                  <Alert
                    type="error"
                    showIcon
                    message="Workflow 统计加载失败"
                    description="运行记录仍可继续查看，请稍后刷新重试。"
                    style={{ marginBottom: 16 }}
                  />
                )}

                {/* Workflow 统计摘要。统计对象为 Workflow Runs，范围遵循启用 Workflow 的配置。 */}
                <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={statsLoading}>
                      <Statistic
                        title="Total Workflows"
                        value={buildStats.totalRuns}
                      />
                      {buildStats.otherRuns > 0 && (
                        <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>
                          Other：{buildStats.otherRuns}
                        </div>
                      )}
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={statsLoading}>
                      <Statistic
                        title="Pass Rate"
                        value={buildStats.passRate}
                        suffix="%"
                        valueStyle={{
                          color: buildStats.passRate >= 90 ? '#3f8600' :
                                 buildStats.passRate >= 70 ? '#1890ff' : '#cf1322',
                        }}
                      />
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={statsLoading}>
                      <Statistic title="Passed" value={buildStats.passedRuns} valueStyle={{ color: '#00a76f' }} />
                    </Card>
                  </Col>
                  <Col xs={24} sm={12} lg={6}>
                    <Card loading={statsLoading}>
                      <Statistic title="Failed" value={buildStats.failedRuns} valueStyle={{ color: '#ff4d4f' }} />
                    </Card>
                  </Col>
                </Row>

                <CIBuildDurationChart runs={buildRuns} loading={buildRunsLoading} />

                <WorkflowTestExecutionTable enabled={activeTab === 'workflow'} />

              </div>
            ),
          },
          {
            key: 'job',
            label: (
              <Space>
                <BarChartOutlined />
                <span>Job 统计</span>
              </Space>
            ),
            children: <JobBoard />,
          },
          {
            key: 'daily-failure',
            label: (
              <Space>
                <ExclamationCircleOutlined />
                <span>每日失败追踪</span>
              </Space>
            ),
            children: <DailyFailureTracking />,
          },
          {
            key: 'daily-report',
            label: (
              <Space>
                <FileTextOutlined />
                <span>每日综合报告</span>
              </Space>
            ),
            children: <CIDailyComprehensiveReport />,
          },
          {
            key: 'test-case-config',
            label: (
              <Space>
                <SettingOutlined />
                <span>用例配置</span>
              </Space>
            ),
            children: <NightlyTestCaseConfig />,
          },
        ]}
      />
    </div>
  )
}

export default CIBoard
