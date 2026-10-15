import { useState, useMemo, useEffect } from 'react'
import {
  Card,
  Table,
  Tag,
  Select,
  Space,
  Typography,
  Button,
  Modal,
  Input,
  Form,
  message,
  Dropdown,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  ReloadOutlined,
  DownloadOutlined,
  DownOutlined,
} from '@ant-design/icons'
import {
  useNightlyTestCases,
  useCreateNightlyTestCase,
  useUpdateNightlyTestCase,
} from '../hooks/useCI'
import type { NightlyTestCase } from '../services/ci'
import { exportNightlyTestCaseRuns } from '../services/ci'
import dayjs from 'dayjs'
import {
  WorkflowDateFilter,
  getDateRangeForMode,
  getInitialCustomDateRange,
  getInitialDateFilterMode,
  type DateFilterMode,
} from '../components/WorkflowDateFilter'

const { Text, Title } = Typography

const WORKFLOW_OPTIONS = [
  { label: '全部', value: '__all__' },
  { label: 'Nightly-A2', value: 'Nightly-A2' },
  { label: 'Nightly-A3', value: 'Nightly-A3' },
  { label: 'Nightly-310P', value: 'Nightly-310P' },
]

type NightlyConfigPreferences = {
  selectedDate?: string | null
  dateFilterMode?: DateFilterMode
  dateRange?: { start: string | null; end: string | null } | null
  selectedBranch?: string
  workflowFilter?: string | null
}

const NIGHTLY_CONFIG_PREFERENCES_KEY = 'ci-nightly-test-case-config-preferences'

function readNightlyConfigPreferences(): NightlyConfigPreferences {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(NIGHTLY_CONFIG_PREFERENCES_KEY)
    return raw ? JSON.parse(raw) as NightlyConfigPreferences : {}
  } catch {
    return {}
  }
}

function NightlyTestCaseConfig() {
  const [savedPreferences] = useState(readNightlyConfigPreferences)
  const datePreferences = useMemo(() => ({
    dateFilterMode: savedPreferences.dateFilterMode ?? (savedPreferences.selectedDate ? 'custom' as const : undefined),
    dateRange: savedPreferences.dateRange ?? (
      savedPreferences.selectedDate
        ? { start: savedPreferences.selectedDate, end: savedPreferences.selectedDate }
        : undefined
    ),
  }), [savedPreferences])
  const initialDateFilterMode = getInitialDateFilterMode(datePreferences)
  const [dateFilterMode, setDateFilterMode] = useState<DateFilterMode>(initialDateFilterMode)
  const [customDateRange, setCustomDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(() => (
    getInitialCustomDateRange(datePreferences, initialDateFilterMode)
  ))
  const [currentDay, setCurrentDay] = useState(() => dayjs().format('YYYY-MM-DD'))
  const [selectedBranch, setSelectedBranch] = useState(() => savedPreferences.selectedBranch ?? 'main')

  const [workflowFilter, setWorkflowFilter] = useState(() => savedPreferences.workflowFilter ?? 'Nightly-A3')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingRecord, setEditingRecord] = useState<NightlyTestCase | null>(null)
  const [exporting, setExporting] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    const timer = window.setInterval(() => {
      const nextDay = dayjs().format('YYYY-MM-DD')
      setCurrentDay((previousDay) => previousDay === nextDay ? previousDay : nextDay)
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const dateRange = useMemo(
    () => dateFilterMode === 'custom' ? customDateRange : getDateRangeForMode(dateFilterMode, dayjs(currentDay)),
    [currentDay, customDateRange, dateFilterMode],
  )

  const { data: testCases, isLoading, refetch } = useNightlyTestCases({
    start_date: dateRange?.[0]?.format('YYYY-MM-DD'),
    end_date: dateRange?.[1]?.format('YYYY-MM-DD'),
    source_branch: selectedBranch,
  })

  const filteredTestCases = useMemo(
    () => workflowFilter === '__all__'
      ? testCases
      : testCases?.filter((item) => item.workflow_name === workflowFilter),
    [testCases, workflowFilter],
  )

  useEffect(() => {
    window.localStorage.setItem(NIGHTLY_CONFIG_PREFERENCES_KEY, JSON.stringify({
      dateFilterMode,
      dateRange: dateFilterMode === 'custom' && customDateRange
        ? {
            start: customDateRange[0]?.format('YYYY-MM-DD') ?? null,
            end: customDateRange[1]?.format('YYYY-MM-DD') ?? null,
          }
        : null,
      selectedBranch,
      workflowFilter,
    }))
  }, [customDateRange, dateFilterMode, selectedBranch, workflowFilter])

  const createMutation = useCreateNightlyTestCase()
  const updateMutation = useUpdateNightlyTestCase()

  const handleAdd = () => {
    setEditingRecord(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (record: NightlyTestCase) => {
    setEditingRecord(record)
    form.setFieldsValue(record)
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      if (editingRecord) {
        await updateMutation.mutateAsync({ id: editingRecord.id, data: values })
        message.success('已更新')
      } else {
        await createMutation.mutateAsync(values)
        message.success('已创建')
      }
      setModalOpen(false)
    } catch (error: any) {
      if (error?.response?.data?.detail) {
        message.error(error.response.data.detail)
      }
    }
  }

  const handleExport = async (format: 'json' | 'xlsx') => {
    setExporting(true)
    try {
      const params = {
        format,
        scope: 'filtered' as const,
        start_date: dateRange?.[0]?.format('YYYY-MM-DD'),
        end_date: dateRange?.[1]?.format('YYYY-MM-DD'),
        source_branch: selectedBranch,
        workflow_names: workflowFilter === '__all__' ? undefined : [workflowFilter],
      }
      const blob = await exportNightlyTestCaseRuns(params)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      const branch = selectedBranch.replace(/[^\w.-]+/g, '_')
      link.download = `nightly_run_details_${branch}_${dayjs().format('YYYYMMDD_HHmmss')}.${format}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      message.success('用例配置 JSON 已导出')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '导出用例配置失败')
    } finally {
      setExporting(false)
    }
  }

  const columns = [
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 120,
      render: (text: string) => <Tag color="blue">{text}</Tag>,
    },
    {
      title: '用例名',
      dataIndex: 'job_name',
      key: 'job_name',
      width: 240,
      ellipsis: true,
      render: (text: string) => <Text>{text}</Text>,
    },
    {
      title: '测试模型',
      dataIndex: 'test_model',
      key: 'test_model',
      width: 160,
      ellipsis: true,
      render: (text: string | null) => text || '-',
    },
    {
      title: '模型 FO',
      dataIndex: 'model_fo',
      key: 'model_fo',
      width: 90,
      render: (text: string | null) => text || '-',
    },
{
      title: '部署方式',
      dataIndex: 'deployment_type',
      key: 'deployment_type',
      width: 120,
      ellipsis: true,
      render: (text: string | null) => text || '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_: any, record: NightlyTestCase) => (
        <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
          编辑
        </Button>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Nightly 用例配置</Title>
          <Text type="secondary">管理 Nightly 流水线中的静态用例，过时用例标记而非删除</Text>
        </div>
        <Space>
          <WorkflowDateFilter
            mode={dateFilterMode}
            customDateRange={customDateRange}
            onModeChange={(value) => {
              setDateFilterMode(value)
              if (value !== 'custom') setCustomDateRange(null)
            }}
            onRangeChange={(range) => {
              if (!range) {
                setCustomDateRange(null)
                setDateFilterMode('all')
              } else {
                setCustomDateRange(range)
              }
            }}
          />
          <Select
            value={selectedBranch}
            onChange={setSelectedBranch}
            style={{ width: 140 }}
            options={[
              { label: 'main', value: 'main' },
              { label: 'releases/v0.23.0', value: 'releases/v0.23.0' },
              { label: 'releases/v0.25.1', value: 'releases/v0.25.1' },
            ]}
          />
          <Select
            value={workflowFilter}
            onChange={setWorkflowFilter}
            placeholder="筛选 Workflow"
            options={WORKFLOW_OPTIONS}
            maxTagCount="responsive"
            style={{ width: 240 }}
          />
          {false && <Button>
            全部 Workflow
          </Button>}
          <Dropdown
            menu={{
              items: false ? [
                { key: 'filtered-json', label: '当前筛选 · 导出 JSON' },
                { key: 'filtered-xlsx', label: '当前筛选 · 导出 Excel' },
                { type: 'divider' },
                { key: 'all-json', label: '全部记录 · 导出 JSON' },
                { key: 'all-xlsx', label: '全部记录 · 导出 Excel' },
              ] : [
                { key: 'json', label: '导出 JSON' },
                { key: 'xlsx', label: '导出 Excel' },
              ],
              onClick: ({ key }) => {
                void handleExport(key as 'json' | 'xlsx')
              },
            }}
          >
            <Button icon={<DownloadOutlined />} loading={exporting}>
              导出运行明细 <DownOutlined />
            </Button>
          </Dropdown>
          <Button style={{ display: 'none' }}>
            导出 JSON
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>新增用例</Button>
        </Space>
      </div>

      <Card>
        <Table
          columns={columns}
          dataSource={filteredTestCases}
          loading={isLoading}
          rowKey="id"
          pagination={{ pageSize: 20, showSizeChanger: false }}
          scroll={{ x: 1200 }}
          size="middle"
        />
      </Card>

      <Modal
        title={editingRecord ? '编辑用例' : '新增用例'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="workflow_name" label="Workflow" rules={[{ required: true, message: '请选择' }]}>
            <Select options={WORKFLOW_OPTIONS} placeholder="选择 Workflow" />
          </Form.Item>
          <Form.Item name="job_name" label="用例名" rules={[{ required: true, message: '请输入用例名' }]}>
            <Input placeholder="如 deepseek-r1-0528-w8a8" />
          </Form.Item>
          <Space size={16} style={{ width: '100%' }}>
            <Form.Item name="test_model" label="测试模型" style={{ width: 240 }}>
              <Input placeholder="如 MiniMax-M3-BF16" />
            </Form.Item>
            <Form.Item name="model_fo" label="模型 FO" style={{ width: 240 }}>
              <Input placeholder="模型负责人" />
            </Form.Item>
          </Space>
          <Space size={16} style={{ width: '100%' }}>
            <Form.Item name="owner" label="测试负责人" style={{ width: 240 }}>
              <Input placeholder="测试负责人" />
            </Form.Item>
            <Form.Item name="deployment_type" label="部署方式" style={{ width: 240 }}>
              <Input placeholder="如 single-node / pd-disagg" />
            </Form.Item>
          </Space>
          <Form.Item name="notes" label="备注">
            <Input.TextArea rows={2} placeholder="备注信息" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default NightlyTestCaseConfig
