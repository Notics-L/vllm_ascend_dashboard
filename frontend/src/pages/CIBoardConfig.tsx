import { useState, useMemo, useEffect } from 'react'
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Switch,
  message,
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Typography,
  Alert,
  Tabs,
  Progress,
  Timeline,
  Row,
  Col,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  SyncOutlined,
  EyeOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../services/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  useJobOwners,
  useCreateJobOwner,
  useUpdateJobOwner,
  useAvailableJobs,
  useToggleJobHidden,
  useHiddenJobsList,
} from '../hooks/useJobOwners'
import { formatTimezone, fromTimezoneNow } from '../utils/timezone'
import { useSyncProgress } from '../hooks/useCI'

const { Option } = Select
const { TextArea } = Input
const { Text, Title } = Typography

// ============ Workflow 管理相关类型和函数 ============

interface Workflow {
  id: number
  workflow_name: string
  workflow_file: string
  hardware: string
  event?: string | null
  actor?: string | null
  description?: string
  enabled: boolean
  display_order: number
  stats_start_hour?: number | null
  stats_end_hour?: number | null
  materialize_name_regex?: string | null
  auto_failure_analysis_enabled: boolean
  last_sync_at?: string | null
}

const useWorkflows = () => {
  return useQuery({
    queryKey: ['ci-workflows'],
    queryFn: async () => {
      const response = await api.get<Workflow[]>('/workflows')
      return response.data
    },
  })
}

const useCreateWorkflow = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: Partial<Workflow>) => api.post('/workflows', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      message.success('创建成功')
    },
    onError: () => {
      message.error('创建失败')
    },
  })
}

const useUpdateWorkflow = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<Workflow> }) =>
      api.put(`/workflows/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      message.success('更新成功')
    },
    onError: () => {
      message.error('更新失败')
    },
  })
}

const useDeleteWorkflow = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.delete(`/workflows/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      message.success('删除成功')
    },
    onError: () => {
      message.error('删除失败')
    },
  })
}

const useToggleWorkflow = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.patch(`/workflows/${id}/toggle`, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      message.success('状态已更新')
    },
    onError: () => {
      message.error('更新失败')
    },
  })
}

// ============ Job 配置相关类型 ============

interface JobConfigItem {
  workflow_name: string
  job_name: string
  display_name?: string | null
  owner?: string
  email?: string | null
  notes?: string | null
  is_hidden?: boolean
  owner_id?: number
  has_owner?: boolean
}

function CIBoardConfig() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState('workflows')
  const { data: currentUser } = useCurrentUser()

  // 判断是否是超级管理员
  const isSuperAdmin = currentUser?.role === 'super_admin'

  // Workflow 管理状态
  const [isWorkflowModalVisible, setIsWorkflowModalVisible] = useState(false)
  const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(null)
  const [workflowForm] = Form.useForm()

  // Job 配置状态
  const [jobForm] = Form.useForm()
  const [workflowFilter, setWorkflowFilter] = useState<string[]>([])
  const [showHidden, setShowHidden] = useState(false)
  const [isJobEditMode, setIsJobEditMode] = useState(false)
  const [isJobModalVisible, setIsJobModalVisible] = useState(false)
  const [selectedJob, setSelectedJob] = useState<JobConfigItem | null>(null)

  // 同步配置状态
  const [syncConfigForm] = Form.useForm()
  const [isSyncConfigModalOpen, setIsSyncConfigModalOpen] = useState(false)
  const [isSyncModalOpen, setIsSyncModalOpen] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [syncForm] = Form.useForm()
  const [elapsedTime, setElapsedTime] = useState(0)
  const [systemStatus, setSystemStatus] = useState<any>(null)

  // 获取同步进度（每 2 秒轮询）
  const { data: progress, error: progressError } = useSyncProgress(isSyncing)

  // 本地计时器，每秒更新已用时间
  useEffect(() => {
    if (isSyncing) {
      setElapsedTime(0)
      const timer = setInterval(() => {
        setElapsedTime(prev => prev + 1)
      }, 1000)
      return () => clearInterval(timer)
    }
  }, [isSyncing])

  // 监听同步状态变化
  useEffect(() => {
    if (progress?.status === 'completed' && isSyncing) {
      message.success(`同步完成！共采集 ${progress.total_collected} 条记录`)
      setIsSyncing(false)
      setIsSyncModalOpen(false)
      syncForm.resetFields()
      setElapsedTime(0)
      // 刷新相关数据
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      queryClient.invalidateQueries({ queryKey: ['ci-runs'] })
      queryClient.invalidateQueries({ queryKey: ['ci-stats'] })
      queryClient.invalidateQueries({ queryKey: ['ci-trends'] })
      queryClient.invalidateQueries({ queryKey: ['workflow-latest-results'] })
    } else if (progress && ['failed', 'dead'].includes(progress.status) && isSyncing) {
      message.error(`同步失败：${progress.error_message || '任务已终止'}`)
      setIsSyncing(false)
      setElapsedTime(0)
    }
  }, [progress, isSyncing, queryClient])

  useEffect(() => {
    if (progressError && isSyncing) {
      message.error('无法获取同步任务状态，请检查后端服务日志')
      setIsSyncing(false)
      setElapsedTime(0)
    }
  }, [progressError, isSyncing])

  // 获取系统状态
  useEffect(() => {
    const fetchSystemStatus = async () => {
      try {
        const response = await api.get('/system/config/status')
        setSystemStatus(response.data)
      } catch (error) {
        console.error('Failed to fetch system status:', error)
      }
    }
    fetchSystemStatus()
    // 每 30 秒刷新一次系统状态
    const interval = setInterval(fetchSystemStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  // ============ Workflow 管理 ============

  const { data: rawWorkflows = [], isLoading: workflowsLoading } = useWorkflows()
  const workflows = useMemo(() => {
    return [...rawWorkflows].sort((a, b) => a.workflow_name.localeCompare(b.workflow_name))
  }, [rawWorkflows])
  const createWorkflowMutation = useCreateWorkflow()
  const updateWorkflowMutation = useUpdateWorkflow()
  const deleteWorkflowMutation = useDeleteWorkflow()
  const toggleWorkflowMutation = useToggleWorkflow()

  // ============ 同步配置 ============

  const { data: syncConfig, isLoading: syncConfigLoading } = useQuery({
    queryKey: ['ci-sync-config'],
    queryFn: async () => {
      const response = await api.get('/system/config')
      return response.data
    },
  })

  const updateSyncMutation = useMutation({
    mutationFn: (data: Record<string, number | boolean>) =>
      api.put('/system/config/sync', null, { params: data }),
    onSuccess: () => {
      message.success('更新成功')
      setIsSyncConfigModalOpen(false)
      syncConfigForm.resetFields()
      queryClient.invalidateQueries({ queryKey: ['ci-sync-config'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '更新失败')
    },
  })

  const triggerSyncMutation = useMutation({
    mutationFn: (values: any) => {
      setIsSyncing(true)
      return api.post('/ci/sync', null, {
        params: {
          days_back: values.days_back || 7,
          max_runs_per_workflow: values.max_runs_per_workflow || 100,
          force_full_refresh: values.force_full_refresh || false,
        },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ci-runs'] })
      queryClient.invalidateQueries({ queryKey: ['ci-stats'] })
      queryClient.invalidateQueries({ queryKey: ['ci-trends'] })
      queryClient.invalidateQueries({ queryKey: ['ci-jobs'] })
      queryClient.invalidateQueries({ queryKey: ['ci-job-detail'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '同步失败')
      setIsSyncing(false)
    },
  })

  // 打开同步配置编辑
  const handleOpenSyncConfig = () => {
    syncConfigForm.setFieldsValue({
      ci_sync_interval_minutes: syncConfig?.sync_config.ci_sync_config.sync_interval_minutes,
      ci_sync_days_back: syncConfig?.sync_config.ci_sync_config.days_back,
      ci_sync_max_runs_per_workflow: syncConfig?.sync_config.ci_sync_config.max_runs_per_workflow,
      ci_sync_force_full_refresh: syncConfig?.sync_config.ci_sync_config.force_full_refresh,
      ci_auto_failure_analysis_enabled: syncConfig?.sync_config.ci_sync_config.auto_failure_analysis_enabled,
      data_retention_days: syncConfig?.sync_config.data_retention_days,
    })
    setIsSyncConfigModalOpen(true)
  }

  // 打开手动同步弹窗
  const handleOpenSyncModal = () => {
    syncForm.setFieldsValue({
      days_back: 7,
      max_runs_per_workflow: 100,
      force_full_refresh: false,
    })
    setIsSyncModalOpen(true)
  }

  // 更新同步配置
  const handleUpdateSyncConfig = (values: Record<string, number | boolean | null | undefined>) => {
    const updateData: Record<string, number | boolean> = {}
    if (values.ci_sync_interval_minutes !== undefined && values.ci_sync_interval_minutes !== null) {
      updateData.ci_sync_interval_minutes = values.ci_sync_interval_minutes
    }
    if (values.ci_sync_days_back !== undefined && values.ci_sync_days_back !== null) {
      updateData.ci_sync_days_back = values.ci_sync_days_back
    }
    if (values.ci_sync_max_runs_per_workflow !== undefined && values.ci_sync_max_runs_per_workflow !== null) {
      updateData.ci_sync_max_runs_per_workflow = values.ci_sync_max_runs_per_workflow
    }
    if (values.ci_sync_force_full_refresh !== undefined && values.ci_sync_force_full_refresh !== null) {
      updateData.ci_sync_force_full_refresh = values.ci_sync_force_full_refresh
    }
    if (values.ci_auto_failure_analysis_enabled !== undefined && values.ci_auto_failure_analysis_enabled !== null) {
      updateData.ci_auto_failure_analysis_enabled = values.ci_auto_failure_analysis_enabled
    }
    if (values.data_retention_days !== undefined && values.data_retention_days !== null) {
      updateData.data_retention_days = values.data_retention_days
    }

    updateSyncMutation.mutate(updateData)
  }

  // 触发同步
  const handleTriggerSync = (values: any) => {
    Modal.confirm({
      title: '确认同步',
      content: `确定要执行数据同步吗？\n\n采集范围：最近 ${values.days_back || 7} 天\n每个 Workflow 最多：${values.max_runs_per_workflow || 100} 条记录\n模式：${values.force_full_refresh ? '强制全量覆盖' : '增量刷新'}`,
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        triggerSyncMutation.mutate(values)
      },
    })
  }

  // Workflow 表格列
  const workflowColumns = [
    {
      title: 'Workflow 名称',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 180,
    },
    {
      title: 'Workflow 文件',
      dataIndex: 'workflow_file',
      key: 'workflow_file',
      width: 220,
      render: (text: string) => <Text code>{text}</Text>,
    },
    {
      title: '硬件',
      dataIndex: 'hardware',
      key: 'hardware',
      width: 80,
      render: (hardware: string) => (
        <Tag color={hardware === 'A2' ? 'green' : 'purple'}>{hardware}</Tag>
      ),
    },
    {
      title: '采集名称正则',
      dataIndex: 'materialize_name_regex',
      key: 'materialize_name_regex',
      width: 200,
      render: (value: string | null) => value || <Text type="secondary">不限制</Text>,
    },
    {
      title: '自动失败分析',
      dataIndex: 'auto_failure_analysis_enabled',
      key: 'auto_failure_analysis_enabled',
      width: 110,
      render: (enabled: boolean, record: Workflow) => (
        <Switch
          checked={enabled}
          checkedChildren="开启"
          unCheckedChildren="关闭"
          onChange={(checked) => updateWorkflowMutation.mutate({
            id: record.id,
            data: { auto_failure_analysis_enabled: checked },
          })}
        />
      ),
    },
    {
      title: '事件',
      dataIndex: 'event',
      key: 'event',
      width: 120,
      render: (event: string | null) => (
        event ? <Tag>{event}</Tag> : <Tag color="default">全部</Tag>
      ),
    },
    {
      title: '最后同步时间',
      dataIndex: 'last_sync_at',
      key: 'last_sync_at',
      width: 180,
      render: (lastSyncAt: string | null) => {
        if (!lastSyncAt) return <Text type="secondary">从未同步</Text>
        return (
          <Space direction="vertical" size={0}>
            <Text strong>{formatTimezone(lastSyncAt, 'YYYY-MM-DD HH:mm:ss')}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {fromTimezoneNow(lastSyncAt)}
            </Text>
          </Space>
        )
      },
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      width: 180,
      ellipsis: true,
    },
    {
      title: '启用',
      key: 'enabled',
      width: 80,
      render: (_: any, record: Workflow) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) =>
            toggleWorkflowMutation.mutate({ id: record.id, enabled: checked })
          }
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: Workflow) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditingWorkflow(record)
              workflowForm.setFieldsValue(record)
              setIsWorkflowModalVisible(true)
            }}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => {
              Modal.confirm({
                title: '确定要删除此 Workflow 吗？',
                onOk: () => deleteWorkflowMutation.mutate(record.id),
              })
            }}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ]

  // 打开 Workflow 弹窗
  const openWorkflowModal = (workflow?: Workflow) => {
    if (workflow) {
      setEditingWorkflow(workflow)
      workflowForm.setFieldsValue(workflow)
    } else {
      setEditingWorkflow(null)
      workflowForm.resetFields()
    }
    setIsWorkflowModalVisible(true)
  }

  // 处理 Workflow 创建/更新
  const handleWorkflowFinish = (values: any) => {
    if (editingWorkflow) {
      updateWorkflowMutation.mutate({ id: editingWorkflow.id, data: values })
    } else {
      createWorkflowMutation.mutate(values)
    }
    setIsWorkflowModalVisible(false)
  }

  // ============ Job 配置 ============

  const { data: jobOwners, isLoading: jobOwnersLoading } = useJobOwners()
  const { data: availableJobs } = useAvailableJobs()
  const { data: hiddenJobsList } = useHiddenJobsList()
  const createJobOwnerMutation = useCreateJobOwner()
  const updateJobOwnerMutation = useUpdateJobOwner()
  const toggleHiddenMutation = useToggleJobHidden()

  // 构建所有 Job 列表（包含已配置和未配置的）
  const allJobs = useMemo(() => {
    if (!availableJobs) return []

    const jobOwnerMap = new Map(
      jobOwners?.map((jo) => [
        `${jo.workflow_name}-${jo.job_name}`,
        jo,
      ]) || []
    )

    const hiddenMap = new Map(
      hiddenJobsList?.map((v) => [
        `${v.workflow_name}-${v.job_name}`,
        v.is_hidden,
      ]) || []
    )

    return availableJobs.map((job) => {
      const key = `${job.workflow_name}-${job.job_name}`
      const owner = jobOwnerMap.get(key)
      return {
        workflow_name: job.workflow_name,
        job_name: job.job_name,
        owner_id: owner?.id,
        owner: owner?.owner,
        email: owner?.email,
        display_name: owner?.display_name,
        notes: owner?.notes,
        is_hidden: hiddenMap.get(key) || false,
        has_owner: !!owner,
      } as JobConfigItem
    })
  }, [availableJobs, jobOwners, hiddenJobsList])

  // 构建去重后的 workflow 列表（用于筛选）
  const workflowOptions = useMemo(() => {
    return Array.from(new Set(allJobs.map((j) => j.workflow_name))).map((name) => ({
      text: name,
      value: name,
    }))
  }, [allJobs])

  // 构建去重后的责任人选项
  const ownerOptions = useMemo(() => {
    return Array.from(
      new Map(
        jobOwners?.map((o) => [
          o.owner,
          {
            value: o.owner,
            label: o.owner,
            title: o.email ? `${o.owner} (${o.email})` : o.owner,
          },
        ]) || []
      ).values()
    )
  }, [jobOwners])

  // 过滤后的 Job 列表（根据显示隐藏开关）
  const filteredJobs = useMemo(() => {
    if (showHidden) {
      return allJobs.filter((job) => job.is_hidden)
    }
    return allJobs.filter((job) => !job.is_hidden)
  }, [allJobs, showHidden])

  // Job 配置表格列
  const jobColumns = [
    {
      title: 'Workflow',
      dataIndex: 'workflow_name',
      key: 'workflow_name',
      width: 200,
      filters: workflowOptions,
      filteredValue: workflowFilter,
      onFilter: (value: any, record: any) => record.workflow_name === value,
    },
    {
      title: 'Job 名称',
      dataIndex: 'job_name',
      key: 'job_name',
      width: 300,
      render: (text: string, record: JobConfigItem) => (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <Text>{text}</Text>
          {record.display_name && (
            <Text style={{ fontSize: '12px', color: '#1890ff', fontWeight: 500 }}>{record.display_name}</Text>
          )}
        </div>
      ),
    },
    {
      title: '责任人',
      dataIndex: 'owner',
      key: 'owner',
      width: 150,
      render: (owner?: string, record?: JobConfigItem) => {
        if (!owner) return <Text type="secondary">未配置</Text>
        return (
          <Space direction="vertical" size={0}>
            <Tag color="green">{owner}</Tag>
            {record?.email && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {record.email}
              </Text>
            )}
          </Space>
        )
      },
    },
    {
      title: '显示/隐藏',
      key: 'visibility',
      width: 100,
      render: (_: any, record: JobConfigItem) => (
        <Switch
          checkedChildren="显示"
          unCheckedChildren="隐藏"
          checked={!record.is_hidden}
          onChange={() => handleJobToggleHidden(record)}
          size="small"
        />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, record: JobConfigItem) => (
        <Button
          type="link"
          size="small"
          onClick={() => openJobModal(record)}
        >
          配置
        </Button>
      ),
    },
  ]

  // 统计信息
  const jobStats = {
    total: allJobs.length,
    visible: allJobs.filter(j => !j.is_hidden).length,
    hidden: allJobs.filter(j => j.is_hidden).length,
    configured: allJobs.filter(j => j.has_owner).length,
  }

  // 切换 Job 显示/隐藏状态
  const handleJobToggleHidden = (record: JobConfigItem) => {
    const newIsHidden = !record.is_hidden
    toggleHiddenMutation.mutate({
      workflow_name: record.workflow_name,
      job_name: record.job_name,
      is_hidden: newIsHidden,
    })
  }

  // 处理 Job 配置创建/更新
  const handleJobFinish = (values: any) => {
    // owner 字段是数组（tags 模式），取第一个元素
    const ownerValue = Array.isArray(values.owner) && values.owner.length > 0 
      ? values.owner[0] 
      : values.owner
    
    const data = {
      workflow_name: values.workflow_name,
      job_name: values.job_name,
      owner: ownerValue,
      email: values.email,
      display_name: values.display_name,
      notes: values.notes,
    }

    if (isJobEditMode && selectedJob?.owner_id) {
      updateJobOwnerMutation.mutate({ ownerId: selectedJob.owner_id, data })
    } else {
      createJobOwnerMutation.mutate(data)
    }
    setIsJobModalVisible(false)
    jobForm.resetFields()
    setSelectedJob(null)
  }

  // 打开 Job 配置弹窗
  const openJobModal = (record: JobConfigItem) => {
    setSelectedJob(record)
    if (record.owner_id) {
      // owner 字段需要转换为数组格式（因为 Select 使用了 mode="tags"）
      jobForm.setFieldsValue({
        ...record,
        owner: record.owner ? [record.owner] : [],
      })
      setIsJobEditMode(true)
    } else {
      jobForm.setFieldsValue({
        workflow_name: record.workflow_name,
        job_name: record.job_name,
      })
      setIsJobEditMode(false)
    }
    setIsJobModalVisible(true)
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0 }}>
          CI 看板配置
        </Title>
        <Text type="secondary">
          配置 CI 看板相关规则
        </Text>
      </div>

      {/* 配置选项卡 */}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        className="stripe-page-tabs"
        items={[
          {
            key: 'workflows',
            label: (
              <Space>
                <SyncOutlined />
                <span>Workflow 管理</span>
              </Space>
            ),
            children: (
              <div>
                {/* 操作栏 */}
                <div style={{ marginBottom: 16 }}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    onClick={() => openWorkflowModal()}
                  >
                    创建 Workflow
                  </Button>
                </div>

                {/* Workflow 列表 */}
                <Card>
                  <Table
                    columns={workflowColumns}
                    dataSource={workflows}
                    loading={workflowsLoading}
                    rowKey="id"
                    pagination={{
                      pageSize: 20,
                      showSizeChanger: false,
                    }}
                    scroll={{ x: 1000 }}
                  />
                </Card>
              </div>
            ),
          },
          {
            key: 'jobs',
            label: (
              <Space>
                <EyeOutlined />
                <span>Job 配置</span>
              </Space>
            ),
            children: (
              <div>
                {/* 统计卡片 */}
                <Space size="large" style={{ marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 24, fontWeight: 'bold' }}>{jobStats.total}</div>
                    <div style={{ color: '#999' }}>总 Job 数</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 24, fontWeight: 'bold', color: '#52c41a' }}>{jobStats.visible}</div>
                    <div style={{ color: '#999' }}>已显示</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 24, fontWeight: 'bold', color: '#8c8c8c' }}>{jobStats.hidden}</div>
                    <div style={{ color: '#999' }}>已隐藏</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 24, fontWeight: 'bold', color: '#1890ff' }}>{jobStats.configured}</div>
                    <div style={{ color: '#999' }}>已配置责任人</div>
                  </div>
                </Space>

                {/* 操作栏 */}
                <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Space>
                    <Text strong>筛选：</Text>
                    <Select
                      mode="multiple"
                      placeholder="Workflow"
                      style={{ width: 200 }}
                      value={workflowFilter}
                      onChange={setWorkflowFilter}
                      options={workflowOptions}
                      allowClear
                    />
                  </Space>
                  <Space>
                    <Text strong>显示隐藏：</Text>
                    <Switch
                      checked={showHidden}
                      onChange={setShowHidden}
                    />
                  </Space>
                </div>

                {/* Job 配置列表 */}
                <Card>
                  <Table
                    columns={jobColumns}
                    dataSource={filteredJobs}
                    loading={jobOwnersLoading}
                    rowKey={(record) => `${record.workflow_name}-${record.job_name}`}
                    pagination={{
                      pageSize: 20,
                      showSizeChanger: false,
                    }}
                    scroll={{ x: 1000 }}
                  />
                </Card>
              </div>
            ),
          },
        ]}
      />

      {/* Workflow 管理弹窗 */}
      <Modal
        title={editingWorkflow ? '编辑 Workflow' : '创建 Workflow'}
        open={isWorkflowModalVisible}
        onCancel={() => {
          setIsWorkflowModalVisible(false)
          workflowForm.resetFields()
          setEditingWorkflow(null)
        }}
        footer={null}
        width={700}
      >
        <Form
          form={workflowForm}
          layout="vertical"
          onFinish={handleWorkflowFinish}
        >
          <Form.Item
            name="workflow_name"
            label="Workflow 名称"
            rules={[{ required: true, message: '请输入 Workflow 名称' }]}
          >
            <Input placeholder="例如：Nightly-A2" />
          </Form.Item>

          <Form.Item
            name="workflow_file"
            label="Workflow 文件名"
            rules={[{ required: true, message: '请输入 Workflow 文件名' }]}
            tooltip="GitHub workflow 文件路径"
          >
            <Input placeholder="schedule_nightly_test_a2.yaml" />
          </Form.Item>

          <Form.Item
            name="hardware"
            label="硬件类型"
            rules={[{ required: true, message: '请选择硬件类型' }]}
          >
            <Select>
              <Option value="A2">A2</Option>
              <Option value="A3">A3</Option>
              <Option value="310P">310P</Option>
            </Select>
          </Form.Item>

          <Form.Item name="event" label="事件类型" initialValue="schedule">
            <Select>
              <Option value="">全部（不过滤）</Option>
              <Option value="schedule">schedule（定时）</Option>
              <Option value="push">push</Option>
              <Option value="pull_request">pull_request</Option>
              <Option value="workflow_dispatch">workflow_dispatch（手动）</Option>
            </Select>
          </Form.Item>

          <Form.Item name="actor" label="触发人">
            <Input placeholder="留空=不过滤" />
          </Form.Item>

          <Form.Item
            name="auto_failure_analysis_enabled"
            label="自动失败分析"
            initialValue={true}
            valuePropName="checked"
            tooltip="仅控制此 Workflow 的失败 Job 是否由同步任务自动排队分析；不影响 CI 同步、失败追踪和手动分析。"
          >
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>

          <Form.Item
            name="materialize_name_regex"
            label="采集名称正则表达式"
            tooltip="CI 同步入库前，匹配 GitHub 返回的实际 Workflow 名称；留空则不按名称过滤。页面仍只展示逻辑名称。"
          >
            <Input allowClear placeholder="例如：^Nightly-A3\s+\(scheduled\)$，留空=不过滤" />
          </Form.Item>

          <Form.Item name="description" label="描述">
            <TextArea rows={3} placeholder="Workflow 描述信息" />
          </Form.Item>

          <Form.Item label="统计 / 物化时间窗口">
            <Space>
              <Form.Item
                name="stats_start_hour"
                noStyle
                initialValue={21}
              >
                <Select
                  style={{ width: 90 }}
                  placeholder="开始"
                  options={Array.from({ length: 24 }, (_, i) => ({ label: `${String(i).padStart(2, '0')}:00`, value: i }))}
                  allowClear
                />
              </Form.Item>
              <span>至</span>
              <Form.Item
                name="stats_end_hour"
                noStyle
                initialValue={3}
              >
                <Select
                  style={{ width: 90 }}
                  placeholder="结束"
                  options={Array.from({ length: 24 }, (_, i) => ({ label: `${String(i).padStart(2, '0')}:00`, value: i }))}
                  allowClear
                />
              </Form.Item>
            </Space>
            <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
              统计及每日失败追踪物化仅纳入此时间段内启动的流水线；任一端留空则不按时段过滤
            </div>
          </Form.Item>

          <Form.Item
            name="enabled"
            label="启用状态"
            initialValue={true}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Button
              style={{ marginRight: 8 }}
              onClick={() => {
                setIsWorkflowModalVisible(false)
                workflowForm.resetFields()
                setEditingWorkflow(null)
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={
                createWorkflowMutation.isPending ||
                updateWorkflowMutation.isPending
              }
            >
              {editingWorkflow ? '更新' : '创建'}
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* Job 配置弹窗 */}
      <Modal
        title={isJobEditMode ? '编辑 Job 责任人' : '配置 Job 责任人'}
        open={isJobModalVisible}
        onCancel={() => {
          setIsJobModalVisible(false)
          jobForm.resetFields()
          setSelectedJob(null)
        }}
        footer={null}
        width={600}
      >
        <Form
          form={jobForm}
          layout="vertical"
          onFinish={handleJobFinish}
        >
          <Form.Item
            name="workflow_name"
            label="Workflow"
            rules={[
              { required: true, message: '请输入 Workflow' },
            ]}
          >
            <Input disabled />
          </Form.Item>

          <Form.Item
            name="job_name"
            label="Job 名称"
            rules={[
              { required: true, message: '请输入 Job 名称' },
            ]}
          >
            <Input disabled />
          </Form.Item>

          <Form.Item
            name="owner"
            label="责任人"
            rules={[
              { required: true, message: '请输入责任人' },
            ]}
            extra="可选择已有责任人，或直接输入新的责任人"
          >
            <Select
              placeholder="选择或输入责任人"
              options={ownerOptions}
              showSearch
              allowClear
              mode="tags"
              maxCount={1}
              style={{ width: '100%' }}
            />
          </Form.Item>

          <Form.Item name="email" label="邮箱">
            <Input placeholder="责任人邮箱" />
          </Form.Item>

          <Form.Item name="display_name" label="显示名称">
            <Input placeholder="Job 显示名称" />
          </Form.Item>

          <Form.Item name="notes" label="备注">
            <TextArea rows={2} placeholder="备注信息" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Button
              style={{ marginRight: 8 }}
              onClick={() => {
                setIsJobModalVisible(false)
                jobForm.resetFields()
                setSelectedJob(null)
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={
                createJobOwnerMutation.isPending ||
                updateJobOwnerMutation.isPending
              }
            >
              {isJobEditMode ? '更新' : '创建'}
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* 同步配置编辑弹窗 */}
      <Modal
        title="编辑同步配置"
        open={isSyncConfigModalOpen}
        onCancel={() => {
          setIsSyncConfigModalOpen(false)
          syncConfigForm.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={syncConfigForm}
          layout="vertical"
          onFinish={handleUpdateSyncConfig}
        >
          <Form.Item
            name="ci_sync_interval_minutes"
            label="CI 同步间隔（分钟）"
            rules={[
              { required: true, message: '请输入同步间隔' },
              { type: 'number', min: 1, max: 10080, message: '同步间隔必须在 1-10080 分钟之间' },
            ]}
            extra="自动同步的时间间隔，1-10080 分钟（7 天）"
          >
            <InputNumber min={1} max={10080} style={{ width: '100%' }} addonAfter="分钟" />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="ci_sync_days_back"
                label="同步天数范围"
                rules={[
                  { required: true, message: '请输入同步天数' },
                  { type: 'number', min: 1, max: 90, message: '同步天数必须在 1-90 天之间' },
                ]}
              >
                <InputNumber min={1} max={90} style={{ width: '100%' }} addonAfter="天" />
              </Form.Item>
            </Col>

            <Col span={12}>
              <Form.Item
                name="ci_sync_max_runs_per_workflow"
                label="每个 Workflow 最多采集"
                rules={[
                  { required: true, message: '请输入采集数量' },
                  { type: 'number', min: 1, max: 1000, message: '采集数量必须在 1-1000 之间' },
                ]}
              >
                <InputNumber min={1} max={1000} style={{ width: '100%' }} addonAfter="条" />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item
            name="ci_sync_force_full_refresh"
            label="刷新模式"
            valuePropName="checked"
            extra="增量刷新只更新已有记录，强制全量覆盖会重新采集所有数据"
          >
            <Switch
              checkedChildren="强制全量覆盖"
              unCheckedChildren="增量刷新"
            />
          </Form.Item>

          <Form.Item
            name="ci_auto_failure_analysis_enabled"
            label="同步后自动失败分析"
            valuePropName="checked"
            extra="关闭后，同步仍会采集和展示失败任务，但不会自动排队 AI 分析；手动分析不受影响。"
          >
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>

          <Form.Item
            name="data_retention_days"
            label="数据保留天数"
            rules={[
              { required: true, message: '请输入保留天数' },
              { type: 'number', min: 7, max: 365, message: '保留天数必须在 7-365 天之间' },
            ]}
            extra="超过此天数的 CI 记录将被自动清理"
          >
            <InputNumber min={7} max={365} style={{ width: '100%' }} addonAfter="天" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsSyncConfigModalOpen(false)
                syncConfigForm.resetFields()
              }}>
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={updateSyncMutation.isPending}
              >
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 手动同步弹窗 */}
      <Modal
        title={
          <Space>
            <ReloadOutlined spin={isSyncing} />
            手动同步 CI 数据
          </Space>
        }
        open={isSyncModalOpen}
        onCancel={() => {
          if (!isSyncing) {
            setIsSyncModalOpen(false)
            syncForm.resetFields()
          }
        }}
        footer={null}
        width={600}
      >
        {!isSyncing ? (
          <>
            <Alert
              message="手动同步说明"
              description="从 GitHub API 采集最新的 CI 运行数据。可根据需要设置采集范围和时间范围。"
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <Form
              form={syncForm}
              layout="vertical"
              onFinish={handleTriggerSync}
            >
              <Form.Item
                name="days_back"
                label="采集时间范围"
                rules={[
                  { required: true, message: '请输入采集天数' },
                  { type: 'number', min: 1, max: 90, message: '采集天数必须在 1-90 天之间' },
                ]}
                extra="从多少天前开始采集数据"
              >
                <InputNumber min={1} max={90} style={{ width: '100%' }} addonAfter="天" />
              </Form.Item>

              <Form.Item
                name="max_runs_per_workflow"
                label="每个 Workflow 最多采集数量"
                rules={[
                  { required: true, message: '请输入采集数量' },
                  { type: 'number', min: 1, max: 1000, message: '采集数量必须在 1-1000 之间' },
                ]}
                extra="每个 workflow 文件最多采集多少条运行记录"
              >
                <InputNumber min={1} max={1000} style={{ width: '100%' }} addonAfter="条" />
              </Form.Item>

              <Form.Item
                name="force_full_refresh"
                label="刷新模式"
                valuePropName="checked"
                extra="增量刷新只更新已有记录，强制全量覆盖会重新采集所有数据"
              >
                <Switch
                  checkedChildren="强制全量覆盖"
                  unCheckedChildren="增量刷新"
                />
              </Form.Item>

              <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
                <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                  <Button onClick={() => {
                    setIsSyncModalOpen(false)
                    syncForm.resetFields()
                  }}>
                    取消
                  </Button>
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={<SyncOutlined />}
                  >
                    开始同步
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </>
        ) : (
          <>
            <Alert
              message={`正在同步 CI 数据...`}
              description={`当前处理：${progress?.current_workflow || '准备中'}`}
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <div style={{ marginBottom: 24 }}>
              <Progress
                percent={progress?.progress_percentage || 0}
                format={() => `${progress?.completed_workflows || 0}/${progress?.total_workflows || 0} workflows`}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <Space size="large" style={{ width: '100%', justifyContent: 'space-around' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold' }}>{progress?.total_collected || 0}</div>
                  <div style={{ color: '#999', fontSize: 12 }}>已采集记录</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold' }}>{progress?.completed_workflows || 0}</div>
                  <div style={{ color: '#999', fontSize: 12 }}>已完成 workflow</div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold' }}>{elapsedTime}s</div>
                  <div style={{ color: '#999', fontSize: 12 }}>已用时间</div>
                </div>
              </Space>
            </div>

            <Card title="Workflow 详情" size="small" style={{ maxHeight: 300, overflow: 'auto' }}>
              {progress?.workflow_details && Object.keys(progress.workflow_details).length > 0 ? (
                <Timeline
                  items={Object.entries(progress.workflow_details).map(([name, detail]) => ({
                    color: detail.status === 'completed' ? 'green' : detail.status === 'failed' ? 'red' : 'blue',
                    children: (
                      <Space direction="vertical" size={0} style={{ width: '100%' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <Text strong>{name}</Text>
                          <Tag color={detail.status === 'completed' ? 'success' : detail.status === 'failed' ? 'error' : 'processing'}>
                            {detail.status === 'completed' ? '完成' : detail.status === 'failed' ? '失败' : '进行中'}
                          </Tag>
                        </div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          采集 {detail.collected} 条记录 | {new Date(detail.updated_at).toLocaleTimeString()}
                        </Text>
                      </Space>
                    ),
                  }))}
                />
              ) : (
                <div style={{ padding: 20, textAlign: 'center', color: '#999' }}>
                  暂无 Workflow 详情
                </div>
              )}
            </Card>

            <div style={{ marginTop: 24, textAlign: 'center' }}>
              <Text type="secondary">正在同步数据，请稍候...</Text>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}

export default CIBoardConfig
