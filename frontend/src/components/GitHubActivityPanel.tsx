import { Card, Col, Row, Tag, Space, Typography, Tooltip, Empty } from 'antd'
import {
  GithubOutlined,
  PullRequestOutlined,
  IssuesCloseOutlined,
  CheckCircleOutlined,
  ArrowRightOutlined,
  CalendarOutlined,
} from '@ant-design/icons'
import { useDailyData } from '../hooks/useDailySummary'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { useNavigate } from 'react-router-dom'
import './GitHubActivityPanel.css'

const { Text, Title } = Typography

dayjs.locale('zh-cn')

interface GitHubActivityCardProps {
  title: string
  color: string
  projectName: string
  date: string
}

// 渲染单个项目的动态卡片
function GitHubActivityCard({ title, color, projectName, date }: GitHubActivityCardProps) {
  const navigate = useNavigate()
  const { data, isLoading, error } = useDailyData(projectName, date)

  const handleCardClick = () => {
    navigate(`/github-activity/${projectName}`)
  }

  // 获取统计数据
  const counts = data?.counts || { prs: 0, issues: 0, commits: 0 }

  // 格式化日期显示
  const dateDisplay = dayjs(date).format('YYYY-MM-DD')

  return (
    <Card
      hoverable
      onClick={handleCardClick}
      className="stripe-activity-card"
      title={
        <Space className="stripe-activity-card-title">
          <GithubOutlined style={{ color }} className="stripe-activity-icon" />
          <span>{title}</span>
          <Tag color="default" style={{ marginLeft: 8 }}>
            <CalendarOutlined style={{ marginRight: 4 }} />
            {dateDisplay}
          </Tag>
        </Space>
      }
      extra={
        <Tooltip title="查看详情">
          <ArrowRightOutlined className="stripe-activity-arrow" />
        </Tooltip>
      }
      size="small"
      loading={isLoading}
    >
      {error ? (
        <Text type="danger">
          加载失败：{(error as Error).message}
        </Text>
      ) : !data?.has_data ? (
        <Empty description="当日无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Row gutter={[16, 16]} className="stripe-activity-row">
          {/* 统计数字 */}
          <Col span={8}>
            <div className="stripe-activity-stat">
              <div className="stripe-activity-stat-header">
                <PullRequestOutlined className="stripe-activity-stat-icon stripe-activity-stat-icon-pr" />
                <span>新增 PR</span>
              </div>
              <div className="stripe-activity-stat-value stripe-activity-stat-value-pr">
                {counts.prs}
              </div>
            </div>
          </Col>

          <Col span={8}>
            <div className="stripe-activity-stat">
              <div className="stripe-activity-stat-header">
                <IssuesCloseOutlined className="stripe-activity-stat-icon stripe-activity-stat-icon-issue" />
                <span>新增 Issue</span>
              </div>
              <div className="stripe-activity-stat-value stripe-activity-stat-value-issue">
                {counts.issues}
              </div>
            </div>
          </Col>

          <Col span={8}>
            <div className="stripe-activity-stat">
              <div className="stripe-activity-stat-header">
                <CheckCircleOutlined className="stripe-activity-stat-icon stripe-activity-stat-icon-commit" />
                <span>Commit</span>
              </div>
              <div className="stripe-activity-stat-value stripe-activity-stat-value-commit">
                {counts.commits}
              </div>
            </div>
          </Col>
        </Row>
      )}
    </Card>
  )
}

/**
 * GitHub 动态展示组件 - 展示 vLLM Ascend 和 vLLM 项目的动态
 * 默认显示前一天的数据（从数据库获取）
 */
function GitHubActivityPanel() {
  // 默认显示前一天的数据
  const yesterday = dayjs().subtract(1, 'day').format('YYYY-MM-DD')

  return (
    <Row gutter={16}>
      <Col span={12}>
        <GitHubActivityCard
          title="vLLM Ascend"
          color="#1890ff"
          projectName="ascend"
          date={yesterday}
        />
      </Col>
      <Col span={12}>
        <GitHubActivityCard
          title="vLLM"
          color="#722ed1"
          projectName="vllm"
          date={yesterday}
        />
      </Col>
    </Row>
  )
}

export default GitHubActivityPanel