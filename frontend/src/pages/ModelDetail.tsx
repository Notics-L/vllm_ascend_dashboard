import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Card,
  Descriptions,
  Space,
  Button,
  Table,
  Tag,
  Typography,
  Tabs,
  Skeleton,
  message,
  Row,
  Col,
} from 'antd'
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  SettingOutlined,
  LinkOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import {
  getModel,
  getModelReports,
  getReport,
  getModelTrends,
  getStartupCommands,
  compareReports,
} from '../services/models'
import type { ModelReport } from '../types/models'
import { ReportDetailModal } from '../components/ReportDetailModal'
import { StartupCommandDisplay } from '../components/StartupCommandDisplay'
import { ModelTrendChart } from '../components/charts/ModelTrendChart'
import { ModelCompareModal } from '../components/ModelCompareModal'
import type { ModelComparisonResponse } from '../types/models'

const { Title, Text } = Typography

function ModelDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const modelId = id && !isNaN(parseInt(id, 10)) ? parseInt(id, 10) : 0

  // UI 状态
  const [selectedReport, setSelectedReport] = useState<ModelReport | null>(null)
  const [isReportModalVisible, setIsReportModalVisible] = useState(false)
  const [compareReportIds, setCompareReportIds] = useState<number[]>([])
  const [isCompareModalVisible, setIsCompareModalVisible] = useState(false)
  const [comparisonData, setComparisonData] = useState<ModelComparisonResponse | null>(null)

  // 获取模型详情
  const { data: model, isLoading: modelLoading } = useQuery({
    queryKey: ['model', modelId],
    queryFn: () => getModel(modelId),
    enabled: !!modelId,
  })

  // 获取报告列表
  const { data: reports = [], isLoading: reportsLoading } = useQuery({
    queryKey: ['model-reports', modelId],
    queryFn: () => getModelReports(modelId),
    enabled: !!modelId,
  })

  // 获取趋势数据
  const { data: trendData = [] } = useQuery({
    queryKey: ['model-trends', modelId],
    queryFn: () => getModelTrends(modelId, { days: 30 }),
    enabled: !!modelId,
  })

  // 获取启动命令
  const { data: startupCommands = {} } = useQuery({
    queryKey: ['startup-commands', modelId],
    queryFn: () => getStartupCommands(modelId),
    enabled: !!modelId,
  })

  // 查看报告详情
  const handleViewReport = async (reportId: number) => {
    try {
      const report = await getReport(modelId, reportId)
      setSelectedReport(report)
      setIsReportModalVisible(true)
    } catch (error: any) {
      message.error('获取报告详情失败')
    }
  }

  // 对比报告
  const handleCompare = async () => {
    if (compareReportIds.length !== 2) {
      message.warning('请选择两个报告进行对比')
      return
    }
    try {
      const data = await compareReports(modelId, compareReportIds)
      setComparisonData(data)
      setIsCompareModalVisible(true)
    } catch (error: any) {
      message.error('对比失败：' + (error.response?.data?.detail || '未知错误'))
    }
  }

  // 提取所有可用的 metrics keys（用于图表显示）
  // 注意：指标过滤现在在每个图表中单独控制

  // 表格列定义
  const columns = [
    {
      title: '',
      key: 'compare',
      width: 50,
      render: (_: any, record: ModelReport) => (
        <input
          type="checkbox"
          checked={compareReportIds.includes(record.id)}
          onChange={(e) => {
            e.stopPropagation()
            if (e.target.checked) {
              if (compareReportIds.length >= 2) {
                message.warning('最多选择两个报告进行对比')
                return
              }
              setCompareReportIds([...compareReportIds, record.id])
            } else {
              setCompareReportIds(compareReportIds.filter((id) => id !== record.id))
            }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ),
    },
    {
      title: '报告时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: any, b: any) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      render: (date: string) => dayjs(date).add(8, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_: any, record: ModelReport) => (
        <Space>
          <Tag color={record.pass_fail === 'pass' ? 'green' : 'red'}>
            {record.pass_fail === 'pass' ? '通过' : '未通过'}
          </Tag>
          {record.manual_override && (
            <Tag color="orange">手动</Tag>
          )}
        </Space>
      ),
    },
    {
      title: 'vLLM 版本',
      dataIndex: 'vllm_version',
      key: 'vllm_version',
      width: 120,
      render: (version?: string) => version || '-',
    },
    {
      title: 'vLLM Ascend 版本',
      dataIndex: 'report_json',
      key: 'vllm_ascend_version',
      width: 140,
      render: (reportJson?: Record<string, any>) =>
        reportJson?.['vllm_ascend_version'] || '-',
    },
    {
      title: '硬件',
      dataIndex: 'hardware',
      key: 'hardware',
      width: 100,
      render: (hardware?: string) => hardware || '-',
    },
  ]

  if (modelLoading) {
    return (
      <div style={{ padding: 24 }}>
        <Skeleton active />
      </div>
    )
  }

  if (!model) {
    return (
      <div style={{ padding: 24 }}>
        <Card>
          <Text type="danger">模型不存在</Text>
        </Card>
      </div>
    )
  }

  return (
    <div className="stripe-page-container">
      {/* 返回按钮和标题 */}
      <div className="stripe-page-header">
        <Space>
          <Button type="default" icon={<ArrowLeftOutlined />} onClick={() => navigate('/models')} className="stripe-btn-ghost stripe-btn-sm">
            返回
          </Button>
          <Title level={3} className="stripe-page-title" style={{ margin: 0 }}>
            {model.model_name}
          </Title>
          <Tag color="blue" className="stripe-badge-info">{model.series}</Tag>
        </Space>
      </div>

      <Row gutter={16}>
        {/* 左侧：配置信息 */}
        <Col xs={24} md={8}>
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            {/* 基础信息 */}
            <Card title={<><SettingOutlined /> 配置信息</>}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="模型名称">
                  {model.model_name}
                </Descriptions.Item>
                <Descriptions.Item label="系列">
                  {model.series || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={model.status === 'active' ? 'green' : 'default'}>
                    {model.status === 'active' ? '活跃' : '未激活'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {dayjs(model.updated_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
                {model.official_doc_url && (
                  <Descriptions.Item label="官方文档">
                    <a
                      href={model.official_doc_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {model.official_doc_url}
                      <LinkOutlined style={{ marginLeft: 4 }} />
                    </a>
                  </Descriptions.Item>
                )}
              </Descriptions>
            </Card>

            {/* 启动命令 */}
            <StartupCommandDisplay
              modelId={modelId}
              commands={startupCommands}
              editable={false}
            />
          </Space>
        </Col>

        {/* 右侧：历史报告和趋势 */}
        <Col xs={24} md={16}>
          <Card className="stripe-card">
            <Tabs
              defaultActiveKey="reports"
              className="stripe-page-tabs"
              items={[
              {
                key: 'reports',
                label: '历史报告',
                children: (
                  <Space direction="vertical" size="large" style={{ width: '100%' }}>
                    {/* 操作栏 */}
                    <Card>
                      <Space>
                        <Button
                          icon={<BarChartOutlined />}
                          onClick={handleCompare}
                          disabled={compareReportIds.length !== 2}
                        >
                          对比所选 ({compareReportIds.length}/2)
                        </Button>
                      </Space>
                    </Card>

                    {/* 报告列表 */}
                    <Card>
                      <Table
                        columns={columns}
                        dataSource={reports}
                        loading={reportsLoading}
                        rowKey="id"
                        pagination={{ pageSize: 20 }}
                        scroll={{ x: 1000 }}
                        onRow={(record) => ({
                          onClick: () => handleViewReport(record.id),
                          style: { cursor: 'pointer' },
                        })}
                      />
                    </Card>
                  </Space>
                ),
              },
              {
                key: 'trends',
                label: '趋势分析',
                children: (
                  <Space direction="vertical" size="large" style={{ width: '100%' }}>
                    {/* 趋势图 - 每个图表支持独立的指标过滤 */}
                    <ModelTrendChart
                      data={trendData}
                      height={300}
                    />
                  </Space>
                ),
              },
            ]}
          />
          </Card>
        </Col>
      </Row>

      {/* 报告详情弹窗 */}
      <ReportDetailModal
        visible={isReportModalVisible}
        report={selectedReport}
        onClose={() => setIsReportModalVisible(false)}
      />

      {/* 对比弹窗 */}
      <ModelCompareModal
        visible={isCompareModalVisible}
        comparisonData={comparisonData}
        onClose={() => setIsCompareModalVisible(false)}
      />
    </div>
  )
}

export default ModelDetail
