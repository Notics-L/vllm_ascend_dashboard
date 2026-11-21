import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Card,
  Table,
  Space,
  Input,
  Select,
  Tag,
  Typography,
  Tabs,
} from 'antd'
import {
  SearchOutlined,
  LinkOutlined,
  AppstoreOutlined,
  TableOutlined,
  HeatMapOutlined
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import { getModels } from '../services/models'
import type { ModelConfig } from '../types/models'
import dayjs from 'dayjs'
import SupportMatrixTab from '../components/SupportMatrixTab'
import FeatureCompatibilityTab from '../components/FeatureCompatibilityTab'
import './Models.css'

const { Title, Text } = Typography

/**
 * 模型看板页面 - 增强版（Tab 式布局）
 * Tab 1: 模型总览（原有模型列表）
 * Tab 2: 支持矩阵（模型×特性交叉表）
 * Tab 3: 特性互操作（25×25 热力图）
 */
function Models() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('overview')
  const [searchText, setSearchText] = useState('')
  const [seriesFilter, setSeriesFilter] = useState<string[]>([])
  const [statusFilter, setStatusFilter] = useState<string[]>([])

  // 获取模型列表
  const { data: models = [], isLoading } = useQuery({
    queryKey: ['models', { search: searchText, series: seriesFilter, status: statusFilter }],
    queryFn: () => getModels({
      search: searchText || undefined,
      series: seriesFilter.length > 0 ? seriesFilter[0] : undefined,
      status: statusFilter.length > 0 ? statusFilter[0] : undefined,
    }),
  })

  // 从数据中动态获取模型系列选项
  const seriesOptions = Array.from(
    new Set(models.map(m => m.series).filter(Boolean))
  ).map(series => ({
    label: series as string,
    value: series as string,
  }))

  // 表格列定义
  const columns: ColumnsType<ModelConfig> = [
    {
      title: '模型名称',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 300,
      sorter: (a, b) => a.model_name.localeCompare(b.model_name),
      render: (text: string, record: ModelConfig) => (
        <Space direction="vertical" size={0} className="stripe-model-name-cell">
          <Text strong className="stripe-model-name">{text}</Text>
          {record.series && (
            <Tag color="#533afd" className="stripe-series-tag">{record.series}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '官方文档',
      key: 'official_doc_url',
      width: 150,
      render: (_: any, record: ModelConfig) => {
        if (!record.official_doc_url) return <span className="stripe-empty-text">-</span>;
        return (
          <a
            href={record.official_doc_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="stripe-doc-link"
          >
            查看文档 <LinkOutlined />
          </a>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      filters: [
        { text: '活跃', value: 'active' },
        { text: '未激活', value: 'inactive' },
      ],
      filteredValue: statusFilter,
      onFilter: (value, record) => record.status === value,
      render: (status: string) => (
        <Tag 
          color={status === 'active' ? '#15be53' : '#64748d'}
          className="stripe-status-tag"
        >
          {status === 'active' ? '活跃' : '未激活'}
        </Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      sorter: (a, b) => new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime(),
      render: (date: string) => (
        <span className="stripe-date-text">
          {dayjs(date).add(8, 'hour').format('YYYY-MM-DD HH:mm:ss')}
        </span>
      ),
    },
  ]

  const tabItems = [
    {
      key: 'overview',
      label: (
        <span>
          <AppstoreOutlined /> 模型总览
        </span>
      ),
      children: (
        <>
          <Card className="stripe-card stripe-filter-card">
            <Space size="middle" wrap>
              <Input
                placeholder="搜索模型名称"
                prefix={<SearchOutlined />}
                className="stripe-search-input"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                allowClear
              />
              <Select
                mode="multiple"
                placeholder="模型系列"
                className="stripe-filter-select"
                value={seriesFilter}
                onChange={setSeriesFilter}
                options={seriesOptions}
                allowClear
              />
            </Space>
          </Card>
          <Card className="stripe-card stripe-table-card">
            <Table
              columns={columns}
              dataSource={models}
              loading={isLoading}
              rowKey="id"
              pagination={{
                pageSize: 20,
                showSizeChanger: false,
              }}
              scroll={{ x: 1000 }}
              className="stripe-table"
              onRow={(record) => ({
                onClick: () => navigate(`/models/${record.id}`),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>
        </>
      ),
    },
    {
      key: 'support-matrix',
      label: (
        <span>
          <TableOutlined /> 支持矩阵
        </span>
      ),
      children: <SupportMatrixTab />,
    },
    {
      key: 'feature-compatibility',
      label: (
        <span>
          <HeatMapOutlined /> 特性互操作
        </span>
      ),
      children: <FeatureCompatibilityTab />,
    },
  ]

  return (
    <div className="stripe-models-page">
      {/* 页面标题 */}
      <div className="stripe-page-header">
        <Title level={3} className="stripe-page-title">
          模型看板
        </Title>
        <Text className="stripe-page-description">
          模型验证报告 · 支持矩阵 · 特性互操作
        </Text>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
        size="large"
      />
    </div>
  )
}

export default Models
