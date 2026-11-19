import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Row, Col, Statistic, Tag, Table, Typography, Space, Button, message, DatePicker, Drawer, Tree } from 'antd'
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  UserOutlined,
  FolderOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import api from '../services/api'
import dayjs from 'dayjs'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const { Title, Text } = Typography
const { DirectoryTree } = Tree

interface FailedJob {
  job_name: string
  conclusion: string
  duration_seconds: number | null
  github_url: string | null
  owner: string | null
  owner_email: string | null
  consecutive_failures: number
}

interface CIDailyReport {
  date: string
  summary: {
    total_runs: number
    success_runs: number
    failure_runs: number
    success_rate: number
    avg_duration_seconds: number | null
  }
  workflow_results: Array<{
    workflow_name: string
    total_runs: number
    success_runs: number
    failure_runs: number
    avg_duration: number | null
    latest_run: {
      run_id: number
      status: string
      conclusion: string | null
      started_at: string | null
      duration_seconds: number | null
    } | null
    hardware: string | null
    failed_jobs: FailedJob[]
    total_jobs: number
    passed_jobs: number
  }>
  markdown_report: string
}

function CIDailyReport() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const [selectedDate, setSelectedDate] = useState(dayjs(date || dayjs().format('YYYY-MM-DD')))
  const [drawerVisible, setDrawerVisible] = useState(false)
  const [selectedWorkflow, setSelectedWorkflow] = useState<string | null>(null)

  // 获取每日报告
  const { data: report, isLoading, refetch } = useQuery<CIDailyReport>({
    queryKey: ['ci-daily-report', selectedDate.format('YYYY-MM-DD')],
    queryFn: async () => {
      const response = await api.get<CIDailyReport>(`/ci/reports/daily/${selectedDate.format('YYYY-MM-DD')}`)
      return response.data
    },
  })

  // 处理导出 Markdown
  const handleExportMarkdown = () => {
    if (!report?.markdown_report) {
      message.warning('暂无报告内容可导出')
      return
    }

    const blob = new Blob([report.markdown_report], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `CI_Daily_Report_${selectedDate.format('YYYY-MM-DD')}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    message.success('报告已导出')
  }

  // 打开失败 Job 抽屉
  const handleOpenDrawer = (workflowName: string) => {
    setSelectedWorkflow(workflowName)
    setDrawerVisible(true)
  }

  // 获取指定 workflow 的失败 job
  const getFailedJobsForWorkflow = (workflowName: string) => {
    if (!report) return []
    const workflow = report.workflow_results.find(wf => wf.workflow_name === workflowName)
    return workflow?.failed_jobs || []
  }

  // 构建树形数据
  const buildFailedJobsTree = () => {
    if (!report) return []
    
    return report.workflow_results
      .filter(wf => wf.failed_jobs && wf.failed_jobs.length > 0)
      .map(wf => ({
        title: (
          <Space>
            <FolderOutlined style={{ color: '#fa8c16' }} />
            <Text strong>{wf.workflow_name}</Text>
            <Tag color="error">{wf.failed_jobs.length}个失败Job</Tag>
          </Space>
        ),
        key: wf.workflow_name,
        selectable: false,
        children: wf.failed_jobs.map((job, idx) => ({
          title: (
            <div style={{ padding: '8px 0' }}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Space>
                    <FileTextOutlined />
                    <Text strong>{job.job_name}</Text>
                    <Tag color="error">{job.conclusion}</Tag>
                  </Space>
                  {job.consecutive_failures > 0 && (
                    <Tag color={job.consecutive_failures >= 5 ? 'error' : job.consecutive_failures >= 3 ? 'warning' : 'default'}>
                      {job.consecutive_failures >= 5 ? '🔥' : job.consecutive_failures >= 3 ? '⚠️' : ''} 连续失败 {job.consecutive_failures} 次
                    </Tag>
                  )}
                </div>
                <Space direction="vertical" size={1}>
                  <Space>
                    <UserOutlined />
                    <Text type="secondary">{job.owner || '未配置责任人'}</Text>
                    {job.owner_email && <Text type="secondary">({job.owner_email})</Text>}
                  </Space>
                  {job.github_url && (
                    <Space>
                      <LinkOutlined />
                      <a href={job.github_url} target="_blank" rel="noopener noreferrer">
                        查看 Job
                      </a>
                    </Space>
                  )}
                  {job.duration_seconds && (
                    <Text type="secondary">
                      时长：{Math.floor(job.duration_seconds / 60)}分{job.duration_seconds % 60}秒
                    </Text>
                  )}
                </Space>
              </Space>
            </div>
          ),
          key: `${wf.workflow_name}-${idx}`,
          selectable: false,
          isLeaf: true,
        })),
      }))
  }

  // Workflow 表格列定义
  const workflowColumns = [
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 200,
      render: (text: string, record: any) => (
        <Space>
          <Text strong>{text}</Text>
          {record.hardware && record.hardware !== 'unknown' && (
            <Tag color={record.hardware === 'A2' ? 'green' : 'purple'}>{record.hardware}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '运行次数',
      dataIndex: 'total_runs',
      key: 'total_runs',
      width: 100,
      align: 'center' as const,
    },
    {
      title: '成功/失败',
      key: 'success_failure',
      width: 150,
      render: (_: any, record: any) => (
        <Space>
          <Tag color="success">{record.success_runs}</Tag>
          <span>/</span>
          <Tag color="error">{record.failure_runs}</Tag>
        </Space>
      ),
    },
    {
      title: 'Job 通过率',
      key: 'job_success_rate',
      width: 120,
      align: 'center' as const,
      render: (_: any, record: any) => {
        const rate = record.total_jobs > 0 ? (record.passed_jobs / record.total_jobs * 100) : 0
        const color = rate >= 90 ? '#3f8600' : rate >= 70 ? '#1890ff' : '#cf1322'
        return (
          <Space direction="vertical" size={0} style={{ textAlign: 'center' }}>
            <Text style={{ color, fontWeight: 'bold', fontSize: '16px' }}>{rate.toFixed(1)}%</Text>
            <Text type="secondary" style={{ fontSize: '11px' }}>{record.passed_jobs}/{record.total_jobs}</Text>
          </Space>
        )
      },
    },
    {
      title: '平均时长',
      dataIndex: 'avg_duration',
      key: 'avg_duration',
      width: 120,
      align: 'center' as const,
      render: (avgDuration: number | null) => {
        if (!avgDuration) return '-'
        const minutes = Math.floor(avgDuration / 60)
        const seconds = avgDuration % 60
        return `${minutes}分${seconds}秒`
      },
    },
    {
      title: '最新运行',
      key: 'latest_run',
      width: 180,
      render: (_: any, record: any) => {
        if (!record.latest_run) return '-'
        const startTime = record.latest_run.started_at
        // 后端已返回北京时间，直接显示
        return (
          <Space direction="vertical" size={0}>
            {startTime && (
              <Text strong style={{ fontSize: '13px' }}>
                {dayjs(startTime).format('YYYY-MM-DD HH:mm:ss')}
              </Text>
            )}
            <Tag color={
              record.latest_run.conclusion === 'success' ? 'success' :
              record.latest_run.conclusion === 'failure' ? 'error' :
              record.latest_run.conclusion === 'cancelled' ? 'warning' : 'processing'
            }>
              {record.latest_run.conclusion || record.latest_run.status}
            </Tag>
          </Space>
        )
      },
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      {/* 头部导航和日期选择 */}
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/')}
          >
            返回主页
          </Button>
          <Title level={3} style={{ margin: 0 }}>
            CI 每日报告
          </Title>
        </Space>
        <Space>
          <DatePicker
            value={selectedDate}
            onChange={(value) => value && setSelectedDate(value)}
            format="YYYY-MM-DD"
          />
          <Button
            icon={<ExclamationCircleOutlined />}
            onClick={() => setDrawerVisible(true)}
            disabled={!report || report.workflow_results.every(wf => !wf.failed_jobs || wf.failed_jobs.length === 0)}
          >
            查看失败 Job
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleExportMarkdown}
            disabled={!report}
          >
            导出 Markdown
          </Button>
        </Space>
      </div>

      {isLoading ? (
        <Card><Space size="large"><Statistic loading /></Space></Card>
      ) : report ? (
        <>
          {/* 统计卡片 */}
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}>
              <Card>
                <Statistic
                  title="总运行次数"
                  value={report.summary.total_runs}
                  suffix="次"
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="成功次数"
                  value={report.summary.success_runs}
                  suffix="次"
                  valueStyle={{ color: '#3f8600' }}
                  prefix={<CheckCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="失败次数"
                  value={report.summary.failure_runs}
                  suffix="次"
                  valueStyle={{ color: '#cf1322' }}
                  prefix={<CloseCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={6}>
              <Card>
                <Statistic
                  title="平均时长"
                  value={report.summary.avg_duration_seconds ? Math.floor(report.summary.avg_duration_seconds / 60) : 0}
                  suffix={`分${report.summary.avg_duration_seconds ? report.summary.avg_duration_seconds % 60 : 0}秒`}
                  prefix={<ClockCircleOutlined />}
                />
              </Card>
            </Col>
          </Row>

          {/* Workflow 详情表格 */}
          <Card title="Workflow 详情" style={{ marginBottom: 24 }}>
            <Table
              columns={workflowColumns}
              dataSource={report.workflow_results}
              rowKey="workflow_name"
              pagination={false}
              size="middle"
            />
          </Card>

          {/* Markdown 报告 */}
          <Card title="📝 报告总结">
            <div
              style={{
                padding: '24px',
                background: '#fafafa',
                borderRadius: '4px',
                lineHeight: '1.8',
                fontSize: '14px',
              }}
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({node: _node, ...props}) => <h1 style={{ borderBottom: '2px solid #1890ff', paddingBottom: '8px', marginBottom: '16px' }} {...props} />,
                  h2: ({node: _node, ...props}) => <h2 style={{ color: '#1890ff', marginTop: '24px', marginBottom: '12px' }} {...props} />,
                  h3: ({node: _node, ...props}) => <h3 style={{ color: '#096dd9', marginTop: '16px', marginBottom: '8px' }} {...props} />,
                  strong: ({node: _node, ...props}) => <strong style={{ color: '#262626' }} {...props} />,
                  code: ({node: _node, inline, ...props}: any) => (
                    inline ?
                    <code style={{ background: '#f5f5f5', padding: '2px 6px', borderRadius: '3px', fontSize: '0.9em' }} {...props} /> :
                    <pre style={{ background: '#f5f5f5', padding: '16px', borderRadius: '4px', overflow: 'auto' }}><code {...props} /></pre>
                  ),
                  ul: ({node: _node, ...props}) => <ul style={{ paddingLeft: '24px', marginBottom: '12px' }} {...props} />,
                  li: ({node: _node, ...props}) => <li style={{ marginBottom: '4px' }} {...props} />,
                  p: ({node: _node, ...props}) => <p style={{ marginBottom: '12px' }} {...props} />,
                }}
              >
                {report.markdown_report}
              </ReactMarkdown>
            </div>
          </Card>
        </>
      ) : (
        <Card>
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
            暂无报告数据
          </div>
        </Card>
      )}

      {/* 失败 Job 抽屉 */}
      <Drawer
        title={<Space><ExclamationCircleOutlined style={{ color: '#ff4d4f' }} /><span>失败 Job 列表</span></Space>}
        placement="right"
        width={500}
        onClose={() => setDrawerVisible(false)}
        open={drawerVisible}
      >
        {report && report.workflow_results.some(wf => wf.failed_jobs && wf.failed_jobs.length > 0) ? (
          <DirectoryTree
            treeData={buildFailedJobsTree()}
            defaultExpandAll={true}
            showIcon={false}
          />
        ) : (
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
            暂无失败 Job
          </div>
        )}
      </Drawer>
    </div>
  )
}

export default CIDailyReport
