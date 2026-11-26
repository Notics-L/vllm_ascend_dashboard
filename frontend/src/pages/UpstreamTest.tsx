import { useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowRightOutlined,
  BugOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ExperimentOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import {
  getUpstreamOverview,
  listUpstreamIssues,
  syncUpstreamTest,
  type UpstreamTestsStat,
} from '../services/upstreamTest'
import { ISSUE_TABLE_WIDTH } from './upstream-test/constants'
import { issueColumns } from './upstream-test/columns'
import { FailureReasonList, StatusBar } from './upstream-test/shared'

const { Text, Title } = Typography

const CONCLUSION_META: Record<string, { label: string; color: string }> = {
  success: { label: '成功', color: 'success' },
  failure: { label: '失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
  in_progress: { label: '运行中', color: 'processing' },
  queued: { label: '排队中', color: 'warning' },
}

function ConclusionTag({ value }: { value: string | null | undefined }) {
  if (!value) return <Tag color="processing">运行中</Tag>
  const meta = CONCLUSION_META[value] || { label: value, color: 'default' }
  return <Tag color={meta.color}>{meta.label}</Tag>
}

/** 百分比展示：分母缺失或无意义时显示 "-" */
function formatPercent(numerator: number, denominator: number | null | undefined) {
  if (!denominator || denominator <= 0) return '-'
  return `${((numerator / denominator) * 100).toFixed(1)}%`
}

/**
 * 总览：最新分析版本的用例规模与两个核心比率。
 *
 * 口径（页面上要写清楚，否则数字无法解读）：
 * - 总用例：vLLM 主社区仓库 vllm-project/vllm 在对应版本 tag 下 tests/ 目录递归
 *   所有以 test_ 开头、.py 结尾的文件个数（Git 树 blob 计数）
 * - 可迁移率 = CI 用例数量 / 总用例数量
 * - 可迁移通过率 = CI 用例通过数 / CI 用例数量
 */
function OverviewStats({
  versionName,
  ciTotal,
  ciPassed,
  versionTotal,
  openIssues,
  upstreamTests,
}: {
  versionName: string | null
  ciTotal: number
  ciPassed: number
  versionTotal: number
  openIssues: number
  upstreamTests?: UpstreamTestsStat
}) {
  const totalUpstream = upstreamTests?.available ? upstreamTests.count ?? null : null
  return (
    <Space direction="vertical" size={10} style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>最新分析版本</Text>}
            value={versionName || '-'}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>总用例（上游 tests）</Text>}
            value={totalUpstream ?? '-'}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>可迁移率</Text>}
            value={formatPercent(ciTotal, totalUpstream)}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>可迁移通过率</Text>}
            value={formatPercent(ciPassed, ciTotal)}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>新增用例</Text>}
            value={versionTotal}
            valueStyle={{ fontSize: 20 }}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <Statistic
            title={<Text style={{ fontSize: 12 }}>开启中 issue</Text>}
            value={openIssues}
            valueStyle={{ fontSize: 20, color: '#cf1322' }}
          />
        </Col>
      </Row>
      <Text type="secondary" style={{ fontSize: 12 }}>
        统计口径：总用例为{' '}
        <a
          href={`https://github.com/${upstreamTests?.repo || 'vllm-project/vllm'}/tree/${
            upstreamTests?.ref || 'main'
          }/tests`}
          target="_blank"
          rel="noreferrer"
        >
          {upstreamTests?.repo || 'vllm-project/vllm'}
          @{upstreamTests?.ref || 'main'}
        </a>{' '}
        的 tests 目录下所有 test_*.py 文件数；
        可迁移率 = CI 用例数（{ciTotal}）/ 总用例数；
        可迁移通过率 = CI 通过数（{ciPassed}）/ CI 用例数（{ciTotal}）。
        {upstreamTests && !upstreamTests.available && ` 总用例未统计成功：${upstreamTests.reason || '未知原因'}`}
        {upstreamTests?.truncated && ' 注意：仓库文件树过大被 API 截断，总用例数偏小。'}
      </Text>
    </Space>
  )
}

export default function UpstreamTest() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [issueState, setIssueState] = useState<'open' | 'resolved' | 'other'>('open')

  const overviewQuery = useQuery({
    queryKey: ['upstream-test', 'overview'],
    queryFn: getUpstreamOverview,
  })

  // 首页只展示前几条，取一页数据即可
  const issuesQuery = useQuery({
    queryKey: ['upstream-test', 'issues', issueState],
    queryFn: () => listUpstreamIssues({ state: issueState, page: 1, page_size: 50 }),
  })

  const syncMutation = useMutation({
    mutationFn: syncUpstreamTest,
    onSuccess: (data) => {
      if (data.success === false) {
        message.error(`同步失败：${String(data.error ?? '未知错误')}`)
      } else {
        message.success('已同步最新上游测试结果')
      }
      queryClient.invalidateQueries({ queryKey: ['upstream-test'] })
    },
    onError: (error: unknown) => {
      message.error(`同步失败：${error instanceof Error ? error.message : String(error)}`)
    },
  })

  const overview = overviewQuery.data
  const ci = overview?.ci
  const weekly = overview?.weekly
  const version = overview?.version
  const issueSummary = overview?.issues?.summary || {}
  const addedIssues = overview?.issues?.added_count

  const columns = useMemo(() => issueColumns(), [])

  const headerExtra = (
    <Space>
      {overview?.fetched_at && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          <ClockCircleOutlined /> 数据时间 {dayjs(overview.fetched_at).format('YYYY-MM-DD HH:mm')}
        </Text>
      )}
      <Button
        icon={<SyncOutlined spin={syncMutation.isPending} />}
        onClick={() => syncMutation.mutate()}
        loading={syncMutation.isPending}
      >
        立即同步
      </Button>
    </Space>
  )

  if (overviewQuery.isLoading) {
    return <Card loading />
  }

  if (overview && !overview.available) {
    return (
      <Card>
        <Empty description={overview.message || '尚未同步上游测试结果'}>
          <Button type="primary" onClick={() => syncMutation.mutate()} loading={syncMutation.isPending}>
            立即同步
          </Button>
        </Empty>
        {overview.last_error && (
          <Alert type="error" style={{ marginTop: 16 }} message="最近一次同步失败" description={overview.last_error} />
        )}
      </Card>
    )
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Row align="middle" justify="space-between" gutter={16}>
          <Col>
            <Title level={4} style={{ margin: 0 }}>
              <ExperimentOutlined /> 上游测试
            </Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              vLLM 上游用例执行情况 · 数据来源{' '}
              <a
                href={`https://github.com/${overview?.source_repo || 'vllm-ascend/upstream-test'}`}
                target="_blank"
                rel="noreferrer"
              >
                {overview?.source_repo || 'vllm-ascend/upstream-test'}
              </a>{' '}
              · 每日自动同步
            </Text>
          </Col>
          <Col>{headerExtra}</Col>
        </Row>
        {overview?.stale && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="数据可能已过期"
            description="超过 36 小时没有成功同步，请检查定时任务或手动触发一次同步。"
          />
        )}
        {(overview?.warnings?.length ?? 0) > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message="本次同步有部分数据缺失"
            description={overview?.warnings?.join('；')}
          />
        )}
      </Card>

      {/* 总览：最新分析版本的规模与两个核心比率 */}
      <Card title="总览" size="small">
        <OverviewStats
          versionName={version?.name || null}
          ciTotal={weekly?.summary?.total || 0}
          ciPassed={weekly?.summary?.passed || 0}
          versionTotal={version?.summary?.total || 0}
          openIssues={issueSummary.open ?? 0}
          upstreamTests={overview?.upstream_tests}
        />
      </Card>

      {/* 板块 1：最新版本新增用例执行情况 */}
      <Card
        title={
          <Space>
            最新版本新增用例执行情况
            {version?.name && <Tag color="blue">{version.name}</Tag>}
            {version?.previous_name && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                相对 {version.previous_name}
              </Text>
            )}
          </Space>
        }
        size="small"
        extra={
          <Button type="link" size="small" onClick={() => navigate('/upstream-test/version')}>
            详情 <ArrowRightOutlined />
          </Button>
        }
      >
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <StatusBar summary={version?.summary} />
          <FailureReasonList reasons={version?.failure_reasons} />
          <Space size={20} wrap>
            <Tooltip title="与最新版本用例关联（用例路径相同）的未关闭 issue 数">
              <Space size={6}>
                <BugOutlined style={{ color: '#cf1322' }} />
                <Text style={{ fontSize: 12 }}>新增 issue</Text>
                <Text strong style={{ fontSize: 13 }}>
                  {addedIssues ?? '-'}
                </Text>
              </Space>
            </Tooltip>
          </Space>
        </Space>
      </Card>

      {/* 板块 2：本周 CI 执行情况 */}
      <Card
        title="本周 CI 执行情况"
        size="small"
        extra={
          <Button type="link" size="small" onClick={() => navigate('/upstream-test/ci')}>
            详情 <ArrowRightOutlined />
          </Button>
        }
      >
        {ci?.available ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {ci.latest ? (
              <Space size={10} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  最近一次
                </Text>
                <a href={ci.latest.html_url || '#'} target="_blank" rel="noreferrer">
                  #{ci.latest.run_number} {ci.workflow_name || 'E2E-upstream'}
                </a>
                <ConclusionTag value={ci.latest.conclusion} />
                <Tag>{ci.latest.event || '-'}</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {ci.latest.created_at ? dayjs(ci.latest.created_at).format('YYYY-MM-DD HH:mm') : '-'}
                </Text>
              </Space>
            ) : (
              <Text type="secondary">暂无运行记录</Text>
            )}
            <StatusBar summary={weekly?.summary} />
            <FailureReasonList reasons={weekly?.failure_reasons} />
            <Space size={6}>
              <BugOutlined style={{ color: '#cf1322' }} />
              <Text style={{ fontSize: 12 }}>CI 失败用例关联的开启中 issue</Text>
              <Text strong style={{ fontSize: 13 }}>
                {weekly?.issue_count ?? 0}
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                个（按 issue 去重）
              </Text>
            </Space>
          </Space>
        ) : (
          <Alert
            type="info"
            showIcon
            message="CI 数据暂不可用"
            description={
              ci?.reason || '未配置 GITHUB_TOKEN 时 GitHub API 匿名额度可能不足，可在系统配置中设置后再同步。'
            }
          />
        )}
      </Card>

      {/* 板块 3：全量 issue 情况 */}
      <Card
        title="全量 issue 情况"
        size="small"
        extra={
          <Button type="link" size="small" onClick={() => navigate('/upstream-test/issues')}>
            查看全部 <ArrowRightOutlined />
          </Button>
        }
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={16}>
            <Col xs={8}>
              <Statistic
                title="未关闭"
                value={issueSummary.open ?? 0}
                valueStyle={{ color: '#cf1322' }}
                prefix={<BugOutlined />}
              />
            </Col>
            <Col xs={8}>
              <Statistic
                title="已修复"
                value={issueSummary.resolved ?? 0}
                valueStyle={{ color: '#16a34a' }}
                prefix={<CheckCircleOutlined />}
              />
            </Col>
            <Col xs={8}>
              <Statistic
                title="其他"
                value={issueSummary.other ?? 0}
                valueStyle={{ color: '#64748b' }}
                prefix={<CloseCircleOutlined />}
              />
            </Col>
          </Row>
          <Tabs
            size="small"
            activeKey={issueState}
            onChange={(key) => setIssueState(key as 'open' | 'resolved' | 'other')}
            items={[
              { key: 'open', label: `未关闭 ${issueSummary.open ?? 0}` },
              { key: 'resolved', label: `已修复 ${issueSummary.resolved ?? 0}` },
              { key: 'other', label: `其他 ${issueSummary.other ?? 0}` },
            ]}
          />
          <Table
            size="small"
            rowKey="id"
            loading={issuesQuery.isFetching}
            dataSource={(issuesQuery.data?.items || []).slice(0, 5)}
            columns={columns}
            pagination={false}
            scroll={{ x: ISSUE_TABLE_WIDTH }}
            locale={{ emptyText: '暂无数据' }}
          />
        </Space>
      </Card>
    </Space>
  )
}
