import { useState, useEffect, useMemo } from 'react'
import {
  Card,
  Descriptions,
  Tag,
  Button,
  Space,
  Form,
  Input,
  InputNumber,
  Switch,
  Select,
  message,
  Alert,
  Modal,
  Typography,
  Tabs,
  Table,
  Collapse,
  Timeline,
  Empty,
  Divider,
  Row,
  Col,
  Badge,
  Popconfirm,
  Progress,
  Spin,
} from 'antd'
import {
  SettingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  GithubOutlined,
  SyncOutlined,
  EditOutlined,
  InfoCircleOutlined,
  ClockCircleOutlined,
  RobotOutlined,
  KeyOutlined,
  MessageOutlined,
  ReloadOutlined,
  ExperimentOutlined,
  MergeOutlined,
  PlusOutlined,
  RocketOutlined,
  CodeOutlined,
} from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useSystemConfig, useSystemStatus, useUpdateAppConfig, useUpdateGitHubConfig } from '../hooks/useSystemConfig'
import {
  useDailySummaryConfig,
  useUpdateDailySummaryConfig,
  useLLMProviders,
  useUpdateLLMProvider,
  useCreateLLMProvider,
  useDeleteLLMProvider,
  useSystemPromptConfig,
  useUpdateSystemPromptConfig,
} from '../hooks/useDailySummary'
import api from '../services/api'
import { formatTimezone } from '../utils/timezone'
import { useSyncProgress } from '../hooks/useCI'
import { getGitCacheStatus, syncGitCache, type GitCacheStatus } from '../services/systemConfig'
import { listSkills, getSkillDetail, refreshSkills, type SkillInfo, type SkillDetail } from '../services/skills'
import {
  getDashboardConfig,
  updateLocalCache,
  rebuildLocalCache,
  fixLocalCache,
  getForceMergeRecords,
  type ProjectDashboardConfig,
} from '../services/projectDashboard'
import { getAgentAnalysisConfig, updateAgentAnalysisConfig } from '../services/systemConfig'

// Failure analysis agent config tab
function AgentAnalysisConfigTab() {
  const queryClient = useQueryClient()
  const { data: config, isLoading } = useQuery({
    queryKey: ['agent-analysis-config'],
    queryFn: getAgentAnalysisConfig,
  })
  const mutation = useMutation({
    mutationFn: updateAgentAnalysisConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-analysis-config'] })
      message.success('Agent 分析配置已更新')
    },
    onError: () => message.error('更新失败'),
  })
  const [form] = Form.useForm()

  useEffect(() => {
    if (config) form.setFieldsValue(config)
  }, [config, form])

  if (isLoading) return <Spin />

  return (
    <Card title={<Space><CodeOutlined />Agent 分析配置</Space>} size="small">
      <Form form={form} layout="inline" onFinish={(v) => mutation.mutate(v)}>
        <Form.Item name="runtime" label="运行方式" rules={[{ required: true }]}>
          <Select
            style={{ width: 180 }}
            options={[
              { value: 'claude_cli', label: 'Claude Code CLI' },
              { value: 'custom_agent', label: '自定义 Agent' },
            ]}
          />
        </Form.Item>
        <Form.Item name="max_turns" label="最大轮次" rules={[{ required: true }]}>
          <InputNumber min={3} max={1000} style={{ width: 100 }} />
        </Form.Item>
        <Form.Item name="timeout_seconds" label="超时(秒)" rules={[{ required: true }]}>
          <InputNumber min={60} max={7200} step={60} style={{ width: 120 }} />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>保存</Button>
        </Form.Item>
      </Form>
      <div style={{ marginTop: 8, color: '#8c8c8c', fontSize: 12 }}>
        修改后下次分析生效。当前运行方式：{config?.runtime === 'custom_agent' ? '自定义 Agent' : 'Claude Code CLI'}；最大 {config?.max_turns ?? 80} 轮，超时 {(config?.timeout_seconds ?? 1800) / 60} 分钟。升级后默认保持 Claude Code CLI，只有手动选择后才启用自定义 Agent。
      </div>
    </Card>
  )
}
import {
  getSyncConfigs as getModelSyncConfigs,
  deleteSyncConfig as deleteModelSyncConfig,
  createSyncConfig as createModelSyncConfig,
  updateSyncConfig as updateModelSyncConfig,
  triggerSync as triggerModelSync,
} from '../services/models'
import type { ModelSyncConfig, ModelSyncConfigCreate } from '../types/models'
import dayjs from 'dayjs'
import type { ColumnsType } from 'antd/es/table'

const { Text, Title } = Typography

// 格式化倒计时
const formatCountdown = (ms: number): string => {
  if (ms <= 0) return '即将执行'
  
  const totalSeconds = Math.floor(ms / 1000)
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  
  const parts: string[] = []
  if (days > 0) parts.push(`${days}天`)
  if (hours > 0) parts.push(`${hours}小时`)
  if (minutes > 0) parts.push(`${minutes}分钟`)
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds}秒`)
  
  return parts.join('')
}

// 计算倒计时的 Hook
const useCountdown = (targetTime: string | null | undefined, intervalMinutes: number | undefined) => {
  const [countdown, setCountdown] = useState<string>('')
  
  useEffect(() => {
    if (!targetTime || !intervalMinutes) {
      setCountdown('')
      return
    }
    
    const updateCountdown = () => {
      const target = new Date(targetTime).getTime()
      const now = Date.now()
      const diff = target - now
      
      if (diff <= 0) {
        // 如果已过时间，使用间隔时间计算下一次
        const nextTarget = target + intervalMinutes * 60 * 1000
        const newDiff = nextTarget - now
        setCountdown(formatCountdown(newDiff))
      } else {
        setCountdown(formatCountdown(diff))
      }
    }
    
    updateCountdown()
    const timer = setInterval(updateCountdown, 1000)
    
    return () => clearInterval(timer)
  }, [targetTime, intervalMinutes])
  
  return countdown
}

function SystemConfig() {
  const { data: currentUser } = useCurrentUser()
  const isSuperAdmin = currentUser?.role === 'super_admin'
  const queryClient = useQueryClient()

  const [appConfigForm] = Form.useForm()
  const [gitHubConfigForm] = Form.useForm()
  const [dailySummaryConfigForm] = Form.useForm()
  const [llmProviderForm] = Form.useForm()
  const [systemPromptForm] = Form.useForm()
  const [commitAnalysisPromptForm] = Form.useForm()
  const [ciFailureAnalysisPromptForm] = Form.useForm()

  // 同步配置相关 state
  const [syncActiveTab, setSyncActiveTab] = useState('project_cache')
  const [cacheDirForm] = Form.useForm()
  const [isCacheDirModalOpen, setIsCacheDirModalOpen] = useState(false)
  const [isCacheUpdating, setIsCacheUpdating] = useState(false)
  const [gitCacheStatus, setGitCacheStatus] = useState<GitCacheStatus | null>(null)
  const [gitCacheStatusLoading, setGitCacheStatusLoading] = useState(false)
  const [githubCacheDir, setGithubCacheDir] = useState<string>('')
  const [projectDashboardCacheInterval, setProjectDashboardCacheInterval] = useState<number>(60)
  
  // CI 同步配置
  const [syncConfigForm] = Form.useForm()
  const [isSyncConfigModalOpen, setIsSyncConfigModalOpen] = useState(false)
  const [isSyncModalOpen, setIsSyncModalOpen] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [ciSyncForm] = Form.useForm()
  const [elapsedTime, setElapsedTime] = useState(0)
  const [systemStatus, setSystemStatus] = useState<any>(null)
  const { data: progress, error: progressError } = useSyncProgress(isSyncing)
  
  // 模型同步配置
  const [isModelSyncModalVisible, setIsModelSyncModalVisible] = useState(false)
  const [editingModelSyncConfig, setEditingModelSyncConfig] = useState<ModelSyncConfig | null>(null)
  const [modelSyncForm] = Form.useForm()
  const [isGlobalModelSyncConfigModalOpen, setIsGlobalModelSyncConfigModalOpen] = useState(false)
  const [globalModelSyncConfigForm] = Form.useForm()
  const [globalModelSyncConfig, setGlobalModelSyncConfig] = useState<{
    sync_interval_minutes: number
    days_back: number
  } | null>(null)

  const [forceMergeRecords, setForceMergeRecords] = useState<any[]>([])

  const { data: config, isLoading: configLoading } = useSystemConfig()
  const { data: ciSyncConfig, isLoading: ciSyncConfigLoading } = useQuery({
    queryKey: ['ci-sync-config'],
    queryFn: async () => {
      const response = await api.get('/system/config')
      return response.data
    },
  })
  const { data: modelSyncConfigs = [], isLoading: modelSyncConfigsLoading } = useQuery({
    queryKey: ['model-sync-configs'],
    queryFn: () => getModelSyncConfigs(),
  })
  const { data: status } = useSystemStatus()
  const updateAppMutation = useUpdateAppConfig()
  const updateGitHubMutation = useUpdateGitHubConfig()

  // 每日总结配置 hooks
  const { data: dailySummaryConfig, isLoading: dailySummaryConfigLoading } = useDailySummaryConfig()
  const { data: llmProviders } = useLLMProviders()
  const updateDailySummaryMutation = useUpdateDailySummaryConfig()
  const updateLLMProviderMutation = useUpdateLLMProvider()
  const createLLMProviderMutation = useCreateLLMProvider()
  const deleteLLMProviderMutation = useDeleteLLMProvider()

  // 系统提示词配置 hooks
  const { data: systemPromptConfig, isLoading: systemPromptConfigLoading } = useSystemPromptConfig()
  const { data: commitAnalysisPromptConfig } = useSystemPromptConfig('commit_analysis')
  const { data: ciFailureAnalysisPromptConfig } = useSystemPromptConfig('ci_failure_analysis')
  const updateSystemPromptMutation = useUpdateSystemPromptConfig()

  const [isAppConfigModalOpen, setIsAppConfigModalOpen] = useState(false)
  const [isGitHubConfigModalOpen, setIsGitHubConfigModalOpen] = useState(false)
  const [isDailySummaryConfigModalOpen, setIsDailySummaryConfigModalOpen] = useState(false)
  const [isLLMProviderModalOpen, setIsLLMProviderModalOpen] = useState(false)
  const [selectedLLMProvider, setSelectedLLMProvider] = useState<string | null>(null)
  const [isCreatingLLMProvider, setIsCreatingLLMProvider] = useState(false)
  const [isSystemPromptModalOpen, setIsSystemPromptModalOpen] = useState(false)
  const [isCommitAnalysisPromptModalOpen, setIsCommitAnalysisPromptModalOpen] = useState(false)
  const [isCIFailureAnalysisPromptModalOpen, setIsCIFailureAnalysisPromptModalOpen] = useState(false)

  const [activeTabKey, setActiveTabKey] = useState('system')

  // 倒计时 hooks
  const ciCountdown = useCountdown(
    status?.scheduler.tasks?.ci_sync?.next_sync,
    status?.scheduler.tasks?.ci_sync?.interval_minutes
  )
  const modelCountdown = useCountdown(
    status?.scheduler.tasks?.model_report_sync?.next_sync,
    status?.scheduler.tasks?.model_report_sync?.interval_minutes
  )
  const cacheCountdown = useCountdown(
    status?.scheduler.tasks?.project_dashboard_cache?.next_sync,
    status?.scheduler.tasks?.project_dashboard_cache?.interval_minutes
  )
  // 项目动态同步任务使用固定间隔（24 小时）计算倒计时
  const dailySummaryCountdown = useCountdown(
    status?.scheduler.tasks?.daily_summary?.next_sync,
    1440 // 24 小时 = 1440 分钟
  )

  // ============ 同步配置 Handler 函数（需要在 render 函数之前定义）============
  
  // CI 同步配置 Handler
  const handleUpdateCISyncConfig = (values: Record<string, number | boolean | null | undefined>) => {
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
    updateCISyncMutation.mutate(updateData)
  }

  const handleTriggerCISync = (values: any) => {
    Modal.confirm({
      title: '确认同步',
      content: `确定要执行数据同步吗？\n\n采集范围：最近 ${values.days_back || 7} 天\n每个 Workflow 最多：${values.max_runs_per_workflow || 100} 条记录\n模式：${values.force_full_refresh ? '强制全量覆盖' : '增量刷新'}`,
      okText: '确认',
      cancelText: '取消',
      onOk: () => {
        triggerCISyncMutation.mutate(values)
      },
    })
  }

  // 缓存目录 Handler
  const handleUpdateCacheDir = async (values: Record<string, string | null | undefined>) => {
    try {
      const { updateSyncConfig } = await import('../services/systemConfig')
      await updateSyncConfig({
        github_cache_dir: values.github_cache_dir || '',
      })
      message.success('缓存目录已更新')
      setIsCacheDirModalOpen(false)
      loadGitHubCacheDir()
    } catch (error: any) {
      message.error('更新失败：' + (error.response?.data?.detail || error.message))
    }
  }

  const handleUpdateCacheInterval = async (values: Record<string, number | null | undefined>) => {
    try {
      const { updateSyncConfig } = await import('../services/systemConfig')
      const updateData: Record<string, number> = {}
      if (values.project_dashboard_cache_interval_minutes !== undefined && values.project_dashboard_cache_interval_minutes !== null) {
        updateData.project_dashboard_cache_interval_minutes = values.project_dashboard_cache_interval_minutes
      }
      await updateSyncConfig(updateData)
      message.success('缓存更新间隔已更新')
    } catch (error: any) {
      message.error('更新失败：' + (error.response?.data?.detail || error.message))
    }
  }

  // 模型同步 Handler
  const handleModelSyncFinish = (values: any) => {
    const data: ModelSyncConfigCreate = {
      workflow_name: values.workflow_name,
      workflow_file: values.workflow_file,
      artifacts_pattern: values.artifacts_pattern,
      file_patterns: values.file_patterns?.split('\n').filter((l: string) => l.trim()),
      enabled: values.enabled,
      branch: values.branch,
    }

    if (editingModelSyncConfig) {
      updateModelSyncMutation.mutate({ id: editingModelSyncConfig.id, data })
    } else {
      createModelSyncMutation.mutate(data)
    }
  }

  const handleUpdateGlobalModelSyncConfig = (values: any) => {
    updateGlobalModelSyncMutation.mutate({
      model_sync_interval_minutes: values.model_sync_interval_minutes,
      model_sync_days_back: values.model_sync_days_back,
    })
  }

  const handleOpenCacheDir = () => {
    cacheDirForm.setFieldsValue({
      github_cache_dir: githubCacheDir,
      project_dashboard_cache_interval_minutes: projectDashboardCacheInterval,
    })
    setIsCacheDirModalOpen(true)
  }

  // ============ 同步配置 Mutations（需要在 handler 之前定义）============
  
  const updateCISyncMutation = useMutation({
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

  const triggerCISyncMutation = useMutation({
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
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '同步失败')
      setIsSyncing(false)
    },
  })

  const updateGlobalModelSyncMutation = useMutation({
    mutationFn: async (data: {
      model_sync_interval_minutes?: number
      model_sync_days_back?: number
    }) => {
      const params = new URLSearchParams()
      Object.entries(data).forEach(([key, value]) => {
        if (value !== undefined) {
          params.append(key, value.toString())
        }
      })
      const token = localStorage.getItem('access_token')
      const res = await fetch(`/api/v1/system/config/sync?${params}`, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      })
      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.detail || '更新失败')
      }
      return res.json()
    },
    onSuccess: () => {
      message.success('同步配置已更新')
      setIsGlobalModelSyncConfigModalOpen(false)
      queryClient.invalidateQueries({ queryKey: ['system-config'] })
    },
    onError: (error: any) => {
      message.error(error.message || '更新失败')
    },
  })

  const deleteModelSyncMutation = useMutation({
    mutationFn: deleteModelSyncConfig,
    onSuccess: () => {
      message.success('配置已删除')
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '删除失败')
    },
  })

  const createModelSyncMutation = useMutation({
    mutationFn: createModelSyncConfig,
    onSuccess: () => {
      message.success('配置已创建')
      setIsModelSyncModalVisible(false)
      modelSyncForm.resetFields()
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '创建失败')
    },
  })

  const updateModelSyncMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: ModelSyncConfigCreate }) =>
      updateModelSyncConfig(id, data),
    onSuccess: () => {
      message.success('配置已更新')
      setIsModelSyncModalVisible(false)
      modelSyncForm.resetFields()
      setEditingModelSyncConfig(null)
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '更新失败')
    },
  })

  const triggerModelSyncMutation = useMutation({
    mutationFn: triggerModelSync,
    onSuccess: (data) => {
      message.success(data.message || '同步已触发')
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '同步失败')
    },
  })

  const loadGitHubCacheDir = async () => {
    try {
      const { getSystemConfig } = await import('../services/systemConfig')
      const data = await getSystemConfig()
      setGithubCacheDir(data.sync_config.github_cache_dir || '')
      setProjectDashboardCacheInterval(data.sync_config.project_dashboard_cache_interval_minutes || 1440)
    } catch (error: any) {
      console.error('Failed to load GitHub cache dir:', error)
    }
  }

  const handleUpdateCache = async () => {
    setIsCacheUpdating(true)
    try {
      await updateLocalCache()
      message.success('本地缓存已更新')
      await loadGitCacheStatus()
    } catch (error: any) {
      message.error('更新缓存失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setIsCacheUpdating(false)
    }
  }

  const handleRebuildCache = async () => {
    Modal.confirm({
      title: '确认重建缓存？',
      content: '这将删除现有 git 仓库缓存，并重新克隆完整历史和 tags。首次克隆可能需要 5-10 分钟，请耐心等待。确定要继续吗？',
      okText: '确认',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        setIsCacheUpdating(true)
        try {
          await rebuildLocalCache()
          message.success('本地缓存已重建，正在获取 git 历史和 tags，这可能需要几分钟...')
          await loadGitCacheStatus()
        } catch (error: any) {
          message.error('重建缓存失败：' + (error.response?.data?.detail || error.message))
        } finally {
          setIsCacheUpdating(false)
        }
      },
    })
  }

  const handleFixCache = async () => {
    Modal.confirm({
      title: '确认修复缓存？',
      content: '这将清理 git 锁文件、重置本地修改并获取最新状态。比重建缓存更快，推荐优先尝试此操作。',
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        setIsCacheUpdating(true)
        try {
          await fixLocalCache()
          message.success('本地缓存已修复')
          await loadGitCacheStatus()
        } catch (error: any) {
          message.error('修复缓存失败：' + (error.response?.data?.detail || error.message))
        } finally {
          setIsCacheUpdating(false)
        }
      },
    })
  }

  const handleSyncGitCache = async (repoType: 'ascend' | 'vllm' | 'all') => {
    setIsCacheUpdating(true)
    try {
      const result = await syncGitCache(repoType)
      if (result.success) {
        const messages = result.results.map(r => `${r.repo}: ${r.message}`).join('; ')
        message.success(`同步成功：${messages}`)
      } else {
        const failed = result.results.filter(r => !r.success).map(r => `${r.repo}: ${r.message}`).join('; ')
        message.error(`部分同步失败：${failed}`)
      }
      await loadGitCacheStatus()
    } catch (error: any) {
      message.error('同步失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setIsCacheUpdating(false)
    }
  }

  const loadGitCacheStatus = async () => {
    setGitCacheStatusLoading(true)
    try {
      const data = await getGitCacheStatus()
      setGitCacheStatus(data)
    } catch (error: any) {
      console.error('Failed to load git cache status:', error)
    } finally {
      setGitCacheStatusLoading(false)
    }
  }

  useEffect(() => {
    loadGitHubCacheDir()
    loadGitCacheStatus()
  }, [])

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
    const interval = setInterval(fetchSystemStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (progress?.status === 'completed' && isSyncing) {
      message.success(`同步完成！共采集 ${progress.total_collected} 条记录`)
      setIsSyncing(false)
      setIsSyncModalOpen(false)
      syncConfigForm.resetFields()
      setElapsedTime(0)
      queryClient.invalidateQueries({ queryKey: ['ci-workflows'] })
      queryClient.invalidateQueries({ queryKey: ['ci-runs'] })
      queryClient.invalidateQueries({ queryKey: ['ci-stats'] })
    } else if (progress && ['failed', 'dead'].includes(progress.status) && isSyncing) {
      message.error(`同步失败：${progress.error_message || '任务已终止'}`)
      setIsSyncing(false)
      setElapsedTime(0)
    }
  }, [progress, isSyncing, queryClient, syncConfigForm])

  useEffect(() => {
    if (progressError && isSyncing) {
      message.error('无法获取同步任务状态，请检查后端服务日志')
      setIsSyncing(false)
      setElapsedTime(0)
    }
  }, [progressError, isSyncing])

  useEffect(() => {
    if (config?.sync_config?.model_sync_config) {
      setGlobalModelSyncConfig({
        sync_interval_minutes: config.sync_config.model_sync_config.sync_interval_minutes,
        days_back: config.sync_config.model_sync_config.days_back,
      })
    }
  }, [config])

  // 更新应用配置
  const handleUpdateAppConfig = (values: Record<string, string | boolean | null | undefined>) => {
    const updateData: Record<string, string | boolean> = {}
    if (values.log_level !== undefined && values.log_level !== null) {
      updateData.log_level = values.log_level as string
    }
    if (values.debug !== undefined && values.debug !== null) {
      updateData.debug = values.debug as boolean
    }

    updateAppMutation.mutate(updateData, {
      onSuccess: (data: { message: string }) => {
        message.success(data.message)
        setIsAppConfigModalOpen(false)
      },
      onError: (error: any) => {
        message.error(error.response?.data?.detail || '更新失败')
      },
    })
  }

  // 更新 GitHub 配置
  const handleUpdateGitHubConfig = (values: Record<string, string | null | undefined>) => {
    const updateData: Record<string, string | boolean> = {}
    if (values.github_token !== undefined && values.github_token !== null && values.github_token.trim()) {
      updateData.github_token = values.github_token.trim()
    }
    updateGitHubMutation.mutate(updateData, {
      onSuccess: (data: { message: string }) => {
        message.success(data.message)
        setIsGitHubConfigModalOpen(false)
        queryClient.invalidateQueries({ queryKey: ['system-config'] })
      },
      onError: (error: any) => {
        message.error(error.response?.data?.detail || '更新失败')
      },
    })
  }

  // 清除 GitHub Token（回退到 .env 启动值）
  const handleClearGitHubToken = () => {
    updateGitHubMutation.mutate({ clear_token: true }, {
      onSuccess: (data: { message: string }) => {
        message.success(data.message)
        setIsGitHubConfigModalOpen(false)
        queryClient.invalidateQueries({ queryKey: ['system-config'] })
      },
      onError: (error: any) => {
        message.error(error.response?.data?.detail || '清除失败')
      },
    })
  }

  // 打开应用配置编辑
  const handleOpenAppConfig = () => {
    appConfigForm.setFieldsValue({
      log_level: config?.app_config?.log_level,
      debug: config?.app_config?.debug,
    })
    setIsAppConfigModalOpen(true)
  }

  // 打开 GitHub 配置编辑
  const handleOpenGitHubConfig = () => {
    gitHubConfigForm.setFieldsValue({
      github_token: '',  // Token 不显示，只让用户输入新的
    })
    setIsGitHubConfigModalOpen(true)
  }

  // 渲染环境标签
  const renderEnvironmentTag = (env: string) => {
    const envMap: Record<string, { color: string; text: string }> = {
      development: { color: 'blue', text: '开发环境' },
      production: { color: 'red', text: '生产环境' },
      test: { color: 'orange', text: '测试环境' },
    }
    const config = envMap[env] || { color: 'default', text: env }
    return <Tag color={config.color}>{config.text}</Tag>
  }

  // 渲染数据库类型标签
  const renderDatabaseTypeTag = (type: string) => {
    const typeMap: Record<string, { color: string; icon: JSX.Element }> = {
      sqlite: { color: 'blue', icon: <DatabaseOutlined /> },
      mysql: { color: 'green', icon: <DatabaseOutlined /> },
    }
    const config = typeMap[type] || { color: 'default', icon: <DatabaseOutlined /> }
    return <Tag color={config.color} icon={config.icon}>{type.toUpperCase()}</Tag>
  }

  // 系统信息 Tab 内容
  const systemInfoTabContent = (
    <Card
      title={
        <span>
          <InfoCircleOutlined style={{ marginRight: 8 }} />
          系统信息
        </span>
      }
      loading={configLoading}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* 应用环境信息 */}
        <Descriptions
          title="应用环境"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="环境">
            {config?.app_config && renderEnvironmentTag(config.app_config.environment)}
          </Descriptions.Item>
          <Descriptions.Item label="调试模式">
            {config?.app_config ? (
              config.app_config.debug ? (
                <Tag color="green" icon={<CheckCircleOutlined />}>开启</Tag>
              ) : (
                <Tag color="default" icon={<CloseCircleOutlined />}>关闭</Tag>
              )
            ) : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="日志级别">
            <Tag>{config?.app_config?.log_level || '-'}</Tag>
          </Descriptions.Item>
        </Descriptions>

        {/* 数据库状态 */}
        <Descriptions
          title="数据库状态"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="数据库类型">
            {config?.database_config && renderDatabaseTypeTag(config.database_config.type)}
          </Descriptions.Item>
          <Descriptions.Item label="配置状态">
            {config?.database_config?.configured ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>已配置</Tag>
            ) : (
              <Tag color="red" icon={<CloseCircleOutlined />}>未配置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="连接状态">
            {status?.database.connected ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
            ) : (
              <Tag color="red" icon={<CloseCircleOutlined />}>异常</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>

        {/* Git 仓库缓存更新任务 */}
        <Descriptions
          title="Git 仓库缓存更新任务"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="下次同步时间">
            {status?.scheduler.tasks?.project_dashboard_cache?.next_sync ? (
              <Tag color="cyan"><ClockCircleOutlined style={{ marginRight: 4 }} />{new Date(status.scheduler.tasks.project_dashboard_cache.next_sync).toLocaleString()}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="倒计时">
            {cacheCountdown ? (
              <Tag color="orange"><ClockCircleOutlined style={{ marginRight: 4 }} />{cacheCountdown}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="同步间隔">
            <Tag color="purple">{status?.scheduler.tasks?.project_dashboard_cache?.interval_minutes} 分钟</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="任务状态">
            {status?.scheduler.running && status?.scheduler.tasks?.project_dashboard_cache?.next_sync ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>

        {/* CI 数据同步任务 */}
        <Descriptions
          title="CI 数据同步任务"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="下次同步时间">
            {status?.scheduler.tasks?.ci_sync?.next_sync ? (
              <Tag color="cyan"><ClockCircleOutlined style={{ marginRight: 4 }} />{new Date(status.scheduler.tasks.ci_sync.next_sync).toLocaleString()}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="倒计时">
            {ciCountdown ? (
              <Tag color="orange"><ClockCircleOutlined style={{ marginRight: 4 }} />{ciCountdown}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="同步间隔">
            <Tag color="blue">{status?.scheduler.tasks?.ci_sync?.interval_minutes || status?.scheduler.sync_interval_minutes} 分钟</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="任务状态">
            {status?.scheduler.running && status?.scheduler.tasks?.ci_sync?.next_sync ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>

        {/* 模型报告同步任务 */}
        <Descriptions
          title="模型报告同步任务"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="下次同步时间">
            {status?.scheduler.tasks?.model_report_sync?.next_sync ? (
              <Tag color="cyan"><ClockCircleOutlined style={{ marginRight: 4 }} />{new Date(status.scheduler.tasks.model_report_sync.next_sync).toLocaleString()}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="倒计时">
            {modelCountdown ? (
              <Tag color="orange"><ClockCircleOutlined style={{ marginRight: 4 }} />{modelCountdown}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="同步间隔">
            <Tag color="blue">{status?.scheduler.tasks?.model_report_sync?.interval_minutes || status?.scheduler.sync_interval_minutes} 分钟</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="任务状态">
            {status?.scheduler.running && status?.scheduler.tasks?.model_report_sync?.next_sync ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>

        {/* 项目动态同步任务 */}
        <Descriptions
          title="项目动态同步任务"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="下次同步时间">
            {status?.scheduler.tasks?.daily_summary?.next_sync ? (
              <Tag color="cyan"><ClockCircleOutlined style={{ marginRight: 4 }} />{new Date(status.scheduler.tasks.daily_summary.next_sync).toLocaleString()}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="倒计时">
            {dailySummaryCountdown ? (
              <Tag color="orange"><ClockCircleOutlined style={{ marginRight: 4 }} />{dailySummaryCountdown}</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="执行时间">
            <Tag color="green">
              每天 {status?.scheduler.tasks?.daily_summary?.cron_hour || 8}:{(status?.scheduler.tasks?.daily_summary?.cron_minute || 0).toString().padStart(2, '0')}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="启用状态">
            {status?.scheduler.tasks?.daily_summary?.enabled ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>已启用</Tag>
            ) : (
              <Tag color="default" icon={<CloseCircleOutlined />}>已禁用</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="任务状态" span={2}>
            {status?.scheduler.running && status?.scheduler.tasks?.daily_summary?.next_sync ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>正常</Tag>
            ) : (
              <Tag color="default">-</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>

        {/* GitHub 仓库信息 */}
        <Descriptions
          title="GitHub 仓库"
          column={{ xxl: 3, xl: 3, lg: 3, md: 3, sm: 2, xs: 1 }}
          bordered
          size="small"
          styles={{ label: { width: 120, fontWeight: 'normal' } }}
          style={{ tableLayout: 'fixed' }}
        >
          <Descriptions.Item label="仓库">
            <Tag color="blue">{config?.github_config?.owner || '-'}/{config?.github_config?.repo || '-'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Token 状态" span={2}>
            {config?.github_config?.token_configured ? (
              <Tag color="green" icon={<CheckCircleOutlined />}>
                已配置
              </Tag>
            ) : (
              <Tag color="red" icon={<CloseCircleOutlined />}>未配置</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Space>
    </Card>
  )

  // 应用配置 Tab 内容（已合并 GitHub 配置）
  const appConfigTabContent = (
    <Card
      title="应用配置"
      loading={configLoading}
      extra={
        isSuperAdmin && (
          <Button size="small" icon={<EditOutlined />} onClick={handleOpenAppConfig}>
            编辑
          </Button>
        )
      }
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* 应用配置 */}
        <div>
          <Title level={5} style={{ marginBottom: 12 }}>
            <SettingOutlined style={{ marginRight: 8 }} />
            应用配置
          </Title>
          <Descriptions
            column={{ xxl: 2, xl: 2, lg: 2, md: 2, sm: 2, xs: 1 }}
            bordered
            size="small"
            styles={{ label: { width: 120, fontWeight: 'normal' } }}
            style={{ tableLayout: 'fixed' }}
          >
            <Descriptions.Item label="调试模式">
              {config?.app_config?.debug ? (
                <Tag color="green" icon={<CheckCircleOutlined />}>开启</Tag>
              ) : (
                <Tag color="default" icon={<CloseCircleOutlined />}>关闭</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="日志级别">
              <Tag>{config?.app_config?.log_level || '-'}</Tag>
            </Descriptions.Item>
          </Descriptions>
        </div>

        {/* GitHub 配置 */}
        <Divider />
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <Title level={5} style={{ margin: 0 }}>
              <GithubOutlined style={{ marginRight: 8 }} />
              GitHub 配置
            </Title>
            {isSuperAdmin && (
              <Button size="small" icon={<GithubOutlined />} onClick={handleOpenGitHubConfig}>
                编辑
              </Button>
            )}
          </div>
          <Descriptions
            column={{ xxl: 2, xl: 2, lg: 2, md: 2, sm: 2, xs: 1 }}
            bordered
            size="small"
            styles={{ label: { width: 120, fontWeight: 'normal' } }}
            style={{ tableLayout: 'fixed' }}
          >
            <Descriptions.Item label="项目">
              <Tag color="blue">vllm-project/vllm-ascend</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Token 状态">
              {config?.github_config?.token_configured ? (
                <Tag color="green" icon={<CheckCircleOutlined />}>
                  已配置
                </Tag>
              ) : (
                <Tag color="red" icon={<CloseCircleOutlined />}>未配置</Tag>
              )}
            </Descriptions.Item>
          </Descriptions>
          {!config?.github_config?.token_configured && (
            <Alert
              message="GitHub Token 未配置"
              description="请点击右上角“编辑 GitHub”按钮配置 GitHub Token，否则无法同步 CI 数据"
              type="warning"
              showIcon
              style={{ marginTop: 16 }}
            />
          )}
        </div>
      </Space>
    </Card>
  )

  // 项目动态配置 Tab 渲染函数（用于同步配置 tab）
  const renderDailySummaryTab = () => (
    <Card
      title="项目动态配置"
      loading={dailySummaryConfigLoading}
      extra={
        isSuperAdmin && (
          <Button size="small" icon={<EditOutlined />} onClick={() => setIsDailySummaryConfigModalOpen(true)}>
            编辑
          </Button>
        )
      }
    >
      <Descriptions
        column={{ xxl: 2, xl: 2, lg: 2, md: 2, sm: 2, xs: 1 }}
        bordered
        size="small"
        styles={{ label: { width: 160, fontWeight: 'normal' } }}
        style={{ tableLayout: 'fixed' }}
      >
        <Descriptions.Item label="定时任务状态">
          {dailySummaryConfig?.enabled ? (
            <Tag color="green" icon={<CheckCircleOutlined />}>已启用</Tag>
          ) : (
            <Tag color="default" icon={<CloseCircleOutlined />}>已禁用</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="执行时间">
          <Tag color="blue">
            每天 {dailySummaryConfig?.cron_hour || 8}:{(dailySummaryConfig?.cron_minute || 0).toString().padStart(2, '0')}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="时区">
          <Tag>{dailySummaryConfig?.timezone || 'Asia/Shanghai'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="已配置 LLM 数量">
          <Tag color="cyan">{llmProviders?.filter((p: any) => p.api_key_configured).length || 0} / {llmProviders?.length || 0}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="已配置项目" span={2}>
          <Space wrap>
            {dailySummaryConfig?.projects?.map((p: any) => (
              <Tag key={p.id} color={p.enabled ? 'green' : 'default'}>
                {p.name} {p.enabled ? '已启用' : '已禁用'}
              </Tag>
            ))}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="说明" span={2}>
          <Text type="secondary">
            定时任务每天自动执行，生成项目动态总结；支持手动触发数据采集和总结生成
          </Text>
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )

  // 提供商配置 Tab 内容（已合并系统提示词）
  const llmProviderConfigTabContent = (
    <Card
      title={
        <span>
          <KeyOutlined style={{ marginRight: 8 }} />
          提供商
        </span>
      }
      loading={!llmProviders}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* 提供商配置 */}
        <div>
          <Alert
            message="提示"
            description="LLM API Key 可在此页面配置。设置“激活状态”为 true 的提供商将用于 AI 分析。支持新增自定义提供商和删除不需要的提供商。CI 失败分析 Agent 使用 Docker LiteLLM / formatproxy，不走本地 Claude CLI。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => { setIsCreatingLLMProvider(true); llmProviderForm.resetFields(); setIsLLMProviderModalOpen(true) }}
            style={{ marginBottom: 16 }}
          >
            新增提供商
          </Button>

          <Table
            dataSource={llmProviders || []}
            rowKey="provider"
            size="small"
            pagination={false}
            columns={[
              {
                title: '提供商',
                dataIndex: 'display_name',
                key: 'display_name',
                render: (text: string) => <Tag color="blue">{text}</Tag>,
              },
              {
                title: 'API Key',
                dataIndex: 'api_key_configured',
                key: 'api_key_configured',
                render: (configured: boolean) =>
                  configured ? (
                    <Tag color="green" icon={<CheckCircleOutlined />}>已配置</Tag>
                  ) : (
                    <Tag color="red" icon={<CloseCircleOutlined />}>未配置</Tag>
                  ),
              },
              {
                title: '激活状态',
                dataIndex: 'is_active',
                key: 'is_active',
                render: (is_active: boolean) =>
                  is_active ? (
                    <Tag color="gold" icon={<CheckCircleOutlined />}>AI 总结使用</Tag>
                  ) : (
                    <Tag color="default">未激活</Tag>
                  ),
              },
              {
                title: '默认模型',
                dataIndex: 'default_model',
                key: 'default_model',
                render: (text: string) => <Tag>{text}</Tag>,
              },
              {
                title: '操作',
                key: 'action',
                render: (_, record: any) => (
                  isSuperAdmin && (
                    <Space size="small">
                      <Button
                        size="small"
                        type="link"
                        onClick={() => {
                          setIsCreatingLLMProvider(false)
                          setSelectedLLMProvider(record.provider)
                          llmProviderForm.setFieldsValue({
                            is_active: record.is_active,
                            default_model: record.default_model,
                            display_name: record.display_name,
                            api_base_url: record.api_base_url || '',
                          })
                          setIsLLMProviderModalOpen(true)
                        }}
                      >
                        编辑
                      </Button>
                      <Popconfirm
                        title={`确定删除提供商 "${record.provider}"？`}
                        description="此操作不可撤销"
                        onConfirm={() => deleteLLMProviderMutation.mutate(record.provider)}
                        okText="删除"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                      >
                        <Button size="small" type="link" danger
                          disabled={record.is_active}
                          loading={deleteLLMProviderMutation.isPending}>
                          删除
                        </Button>
                      </Popconfirm>
                    </Space>
                  )
                ),
              },
            ]}
          />
        </div>

        {/* 系统提示词配置 */}
        <Divider />
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <Title level={5} style={{ margin: 0 }}>
              <MessageOutlined style={{ marginRight: 8 }} />
              系统提示词
            </Title>
            {isSuperAdmin && (
              <Button size="small" icon={<MessageOutlined />} onClick={() => setIsSystemPromptModalOpen(true)}>
                编辑
              </Button>
            )}
          </div>
          <Alert
            message="说明"
            description="系统提示词用于指导 AI 生成项目动态总结的风格和内容重点。不同项目可使用不同的提示词模板。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Collapse
            items={[
              {
                key: 'ascend',
                label: <Tag color="purple">vLLM Ascend 项目动态提示词</Tag>,
                children: (
                  <div style={{ maxHeight: 200, overflow: 'auto' }}>
                    <Text>{systemPromptConfig?.prompts?.ascend || '未配置'}</Text>
                  </div>
                ),
              },
              {
                key: 'vllm',
                label: <Tag color="cyan">vLLM 项目动态提示词</Tag>,
                children: (
                  <div style={{ maxHeight: 200, overflow: 'auto' }}>
                    <Text>{systemPromptConfig?.prompts?.vllm || '未配置'}</Text>
                  </div>
                ),
              },
            ]}
          />
        </div>

        <Divider />
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <Title level={5} style={{ margin: 0 }}>
              <MessageOutlined style={{ marginRight: 8 }} />
              Commit 分析系统提示词
            </Title>
            {isSuperAdmin && (
              <Button size="small" icon={<MessageOutlined />} onClick={() => setIsCommitAnalysisPromptModalOpen(true)}>
                编辑
              </Button>
            )}
          </div>
          <Alert
            message="说明"
            description="系统提示词用于指导 AI 生成单个 Commit 分析总结的风格和内容重点。不同项目可使用不同的提示词模板。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Collapse
            items={[
              {
                key: 'commit-ascend',
                label: <Tag color="purple">vLLM Ascend Commit 分析提示词</Tag>,
                children: (
                  <div style={{ maxHeight: 200, overflow: 'auto' }}>
                    <Text>{commitAnalysisPromptConfig?.prompts?.ascend || '未配置'}</Text>
                  </div>
                ),
              },
              {
                key: 'commit-vllm',
                label: <Tag color="cyan">vLLM Commit 分析提示词</Tag>,
                children: (
                  <div style={{ maxHeight: 200, overflow: 'auto' }}>
                    <Text>{commitAnalysisPromptConfig?.prompts?.vllm || '未配置'}</Text>
                  </div>
                ),
              },
            ]}
          />
        </div>

        <Divider />
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <Title level={5} style={{ margin: 0 }}>
              <MessageOutlined style={{ marginRight: 8 }} />
              CI 失败分析系统提示词
            </Title>
            {isSuperAdmin && (
              <Button size="small" icon={<MessageOutlined />} onClick={() => setIsCIFailureAnalysisPromptModalOpen(true)}>
                编辑
              </Button>
            )}
          </div>
          <Alert
            message="说明"
            description="系统提示词用于指导 AI 分析 CI 失败 Job 的根因分类和改进建议。基于 auto-bug-fixer 技能模板，并追加 CI 专属分类附录。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
            <Collapse
            items={[
              {
                key: 'ci-failure-ascend',
                label: <Tag color="orange">CI 失败分析提示词（当前配置）</Tag>,
                children: (
                  <div style={{ maxHeight: 200, overflow: 'auto' }}>
                    <Text>{ciFailureAnalysisPromptConfig?.prompts?.default || ciFailureAnalysisPromptConfig?.prompts?.ascend || '未配置（使用默认提示词）'}</Text>
                  </div>
                ),
              },
            ]}
          />
        </div>
      </Space>
    </Card>
  )

  const [skillsList, setSkillsList] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null)
  const [skillDetail, setSkillDetail] = useState<SkillDetail | null>(null)
  const [skillDetailLoading, setSkillDetailLoading] = useState(false)
  const [skillRefreshing, setSkillRefreshing] = useState(false)

  const loadSkills = async () => {
    setSkillsLoading(true)
    try {
      const data = await listSkills()
      setSkillsList(data)
    } catch (error) {
      message.error('加载 Skills 失败')
    } finally {
      setSkillsLoading(false)
    }
  }

  const loadSkillDetail = async (name: string) => {
    setSelectedSkillName(name)
    setSkillDetailLoading(true)
    try {
      const data = await getSkillDetail(name)
      setSkillDetail(data)
    } catch (error) {
      message.error('加载 Skill 详情失败')
    } finally {
      setSkillDetailLoading(false)
    }
  }

  const handleRefreshSkills = async () => {
    setSkillRefreshing(true)
    try {
      const result = await refreshSkills()
      message.success(result.message)
      await loadSkills()
    } catch (error) {
      message.error('刷新 Skills 失败')
    } finally {
      setSkillRefreshing(false)
    }
  }

  useEffect(() => {
    if (activeTabKey === 'skills') {
      loadSkills()
    }
  }, [activeTabKey])

  const renderSkillsTab = () => (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card
        title="已安装的 Skills"
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={skillRefreshing}
            onClick={handleRefreshSkills}
          >
            刷新
          </Button>
        }
      >
        <Table
          dataSource={skillsList}
          loading={skillsLoading}
          rowKey="name"
          pagination={false}
          columns={[
            {
              title: 'Skill 名称',
              dataIndex: 'name',
              key: 'name',
              render: (name: string) => (
                <Button type="link" onClick={() => loadSkillDetail(name)} style={{ padding: 0 }}>
                  <CodeOutlined style={{ marginRight: 4 }} />
                  {name}
                </Button>
              ),
            },
            {
              title: '描述',
              dataIndex: 'description',
              key: 'description',
              ellipsis: true,
            },
            {
              title: '关联 Scope',
              dataIndex: 'scope',
              key: 'scope',
              render: (scope: string | null) => scope ? <Tag color="blue">{scope}</Tag> : '-',
            },
            {
              title: '内容长度',
              dataIndex: 'content_length',
              key: 'content_length',
              render: (len: number) => len > 0 ? `${len} 字符` : '-',
            },
            {
              title: '加载时间',
              dataIndex: 'loaded_at',
              key: 'loaded_at',
              render: (at: string) => at ? dayjs(at).format('YYYY-MM-DD HH:mm:ss') : '-',
            },
          ]}
        />
        {skillsList.length === 0 && !skillsLoading && (
          <Alert
            message="未安装任何 Skill"
            description="请将 Skill 文件（SKILL.md）放到 data/skills/<skill_name>/ 目录下，然后点击刷新按钮加载"
            type="info"
            showIcon
            style={{ marginTop: 16 }}
          />
        )}
      </Card>

      {selectedSkillName && (
        <Card
          title={<Space><CodeOutlined /> Skill 详情: {selectedSkillName}</Space>}
          loading={skillDetailLoading}
        >
          {skillDetail && (
            <Descriptions column={1} bordered size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="名称">{skillDetail.name}</Descriptions.Item>
              <Descriptions.Item label="描述">{skillDetail.description}</Descriptions.Item>
              <Descriptions.Item label="关联 Scope">
                {skillDetail.scope ? <Tag color="blue">{skillDetail.scope}</Tag> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="文件路径">
                <Text copyable style={{ fontFamily: 'monospace', fontSize: 12 }}>{skillDetail.file_path}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="加载时间">{skillDetail.loaded_at}</Descriptions.Item>
            </Descriptions>
          )}
          {skillDetail?.content && (
            <div style={{ marginTop: 16 }}>
              <Text strong style={{ marginBottom: 8, display: 'block' }}>Skill 内容（SKILL.md）</Text>
              <div style={{
                padding: 16,
                background: '#fafafa',
                borderRadius: 8,
                maxHeight: '500px',
                overflowY: 'auto',
                fontSize: 13,
                fontFamily: 'monospace',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.6,
              }}>
                {skillDetail.content}
              </div>
            </div>
          )}
        </Card>
      )}
    </Space>
  )

  const tabItems = [
    {
      key: 'system',
      label: (
        <Space>
          <InfoCircleOutlined />
          系统信息
        </Space>
      ),
      children: systemInfoTabContent,
    },
    {
      key: 'app',
      label: (
        <Space>
          <SettingOutlined />
          应用配置
        </Space>
      ),
      children: appConfigTabContent,
    },
    {
      key: 'llm_providers',
      label: (
        <Space>
          <KeyOutlined />
          LLM 配置
        </Space>
      ),
      children: llmProviderConfigTabContent,
    },
    {
      key: 'cli_config',
      label: (
        <Space>
          <CodeOutlined />
          Agent 分析配置
        </Space>
      ),
      children: <AgentAnalysisConfigTab />,
    },
    {
      key: 'sync_config',
      label: (
        <Space>
          <SyncOutlined />
          同步配置
        </Space>
      ),
      children: null, // Will be added separately
    },
    {
      key: 'skills',
      label: (
        <Space>
          <RocketOutlined />
          Skills 管理
        </Space>
      ),
      children: renderSkillsTab(),
    },
  ]

  // ============ 同步配置 Tab 渲染函数 ============

  // 1. 项目看板 Git 缓存管理
  const renderProjectCacheTab = () => {
    return (
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* Git 缓存状态 */}
        <Card
          title="Git 缓存状态"
          loading={gitCacheStatusLoading}
          extra={
            <Space>
              <Button size="small" onClick={handleOpenCacheDir}>
                编辑
              </Button>
            </Space>
          }
        >
          <Row gutter={[16, 16]}>
            <Col span={12}>
              <Card
                type="inner"
                title={
                  <Space>
                    <Tag color="blue">vLLM Ascend</Tag>
                    {gitCacheStatus?.repositories?.ascend?.is_cloned ? (
                      <Tag color="green">已克隆</Tag>
                    ) : (
                      <Tag color="default">未克隆</Tag>
                    )}
                  </Space>
                }
                size="small"
              >
                {gitCacheStatus?.repositories?.ascend?.latest_commit ? (
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    <div>
                      <Text strong>最新 Commit: </Text>
                      <Tag color="blue" style={{ fontFamily: 'monospace', fontSize: '12px' }}>
                        {gitCacheStatus.repositories.ascend.latest_commit.sha.substring(0, 7)}
                      </Tag>
                      <Text ellipsis style={{ maxWidth: 200 }}>{gitCacheStatus.repositories.ascend.latest_commit.subject}</Text>
                    </div>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        <ClockCircleOutlined /> {dayjs(gitCacheStatus.repositories.ascend.latest_commit.date).format('YYYY-MM-DD HH:mm')}
                      </Text>
                    </div>
                  </Space>
                ) : (
                  <Text type="warning">缓存未就绪</Text>
                )}
              </Card>
            </Col>
            <Col span={12}>
              <Card
                type="inner"
                title={
                  <Space>
                    <Tag color="purple">vLLM</Tag>
                    {gitCacheStatus?.repositories?.vllm?.is_cloned ? (
                      <Tag color="green">已克隆</Tag>
                    ) : (
                      <Tag color="default">未克隆</Tag>
                    )}
                  </Space>
                }
                size="small"
              >
                {gitCacheStatus?.repositories?.vllm?.latest_commit ? (
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    <div>
                      <Text strong>最新 Commit: </Text>
                      <Tag color="purple" style={{ fontFamily: 'monospace', fontSize: '12px' }}>
                        {gitCacheStatus.repositories.vllm.latest_commit.sha.substring(0, 7)}
                      </Tag>
                      <Text ellipsis style={{ maxWidth: 200 }}>{gitCacheStatus.repositories.vllm.latest_commit.subject}</Text>
                    </div>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        <ClockCircleOutlined /> {dayjs(gitCacheStatus.repositories.vllm.latest_commit.date).format('YYYY-MM-DD HH:mm')}
                      </Text>
                    </div>
                  </Space>
                ) : (
                  <Text type="warning">缓存未就绪</Text>
                )}
              </Card>
            </Col>
          </Row>
        </Card>

        {/* 操作按钮 */}
        <Card title="缓存操作">
          <Space wrap>
            <Button
              icon={<SyncOutlined spin={isCacheUpdating} />}
              onClick={() => handleSyncGitCache('all')}
              loading={isCacheUpdating}
            >
              同步所有仓库
            </Button>
            <Button
              icon={<SyncOutlined spin={isCacheUpdating} />}
              onClick={() => handleSyncGitCache('ascend')}
              loading={isCacheUpdating}
            >
              同步 ascend 仓库
            </Button>
            <Button
              icon={<SyncOutlined spin={isCacheUpdating} />}
              onClick={() => handleSyncGitCache('vllm')}
              loading={isCacheUpdating}
            >
              同步 vllm 仓库
            </Button>
            <Button danger icon={<SyncOutlined />} onClick={handleRebuildCache} loading={isCacheUpdating}>
              重建缓存
            </Button>
            <Button icon={<ExperimentOutlined />} onClick={handleFixCache} loading={isCacheUpdating}>
              修复缓存
            </Button>
          </Space>
        </Card>
      </Space>
    )
  }

  // 2. CI 看板同步配置
  const renderCISyncTab = () => {
    const handleOpenCISyncConfig = () => {
      syncConfigForm.setFieldsValue({
        ci_sync_interval_minutes: ciSyncConfig?.sync_config.ci_sync_config.sync_interval_minutes,
        ci_sync_days_back: ciSyncConfig?.sync_config.ci_sync_config.days_back,
        ci_sync_max_runs_per_workflow: ciSyncConfig?.sync_config.ci_sync_config.max_runs_per_workflow,
        ci_sync_force_full_refresh: ciSyncConfig?.sync_config.ci_sync_config.force_full_refresh,
        ci_auto_failure_analysis_enabled: ciSyncConfig?.sync_config.ci_sync_config.auto_failure_analysis_enabled,
        data_retention_days: ciSyncConfig?.sync_config.data_retention_days,
      })
      setIsSyncConfigModalOpen(true)
    }

    const handleOpenCISyncModal = () => {
      syncConfigForm.setFieldsValue({
        days_back: 7,
        max_runs_per_workflow: 100,
        force_full_refresh: false,
      })
      setIsSyncModalOpen(true)
    }

    return (
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* CI 同步配置卡片 */}
        <Card
          title="CI 同步配置"
          loading={ciSyncConfigLoading}
          extra={
            <Space>
              {isSuperAdmin && (
                <Button size="small" icon={<ReloadOutlined />} onClick={handleOpenCISyncModal}>
                  手动同步
                </Button>
              )}
              {isSuperAdmin && (
                <Button size="small" onClick={handleOpenCISyncConfig}>
                  编辑
                </Button>
              )}
            </Space>
          }
        >
          <Descriptions column={2} bordered>
            <Descriptions.Item label="同步间隔">
              {ciSyncConfig?.sync_config.ci_sync_config.sync_interval_minutes} 分钟
            </Descriptions.Item>
            <Descriptions.Item label="同步天数范围">
              {ciSyncConfig?.sync_config.ci_sync_config.days_back} 天
            </Descriptions.Item>
            <Descriptions.Item label="每个 Workflow 最多采集">
              {ciSyncConfig?.sync_config.ci_sync_config.max_runs_per_workflow} 条
            </Descriptions.Item>
            <Descriptions.Item label="刷新模式">
              {ciSyncConfig?.sync_config.ci_sync_config.force_full_refresh ? '全量覆盖' : '增量刷新'}
            </Descriptions.Item>
            <Descriptions.Item label="同步后自动失败分析">
              {ciSyncConfig?.sync_config.ci_sync_config.auto_failure_analysis_enabled ? '开启' : '关闭'}
            </Descriptions.Item>
            <Descriptions.Item label="数据保留策略">
              {ciSyncConfig?.sync_config.data_retention_days} 天
            </Descriptions.Item>
            <Descriptions.Item label="下次同步时间">
              {systemStatus?.scheduler?.tasks?.ci_sync?.next_sync
                ? formatTimezone(systemStatus.scheduler.tasks.ci_sync.next_sync, 'YYYY-MM-DD HH:mm:ss')
                : '未安排'}
            </Descriptions.Item>
            <Descriptions.Item label="调度器状态">
              {systemStatus?.scheduler?.running ? (
                <Tag color="green">运行中</Tag>
              ) : (
                <Tag color="red">已停止</Tag>
              )}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      </Space>
    )
  }

  // 3. 模型看板模型同步配置
  const renderModelSyncTab = () => {
    const openModelSyncModal = (config?: ModelSyncConfig) => {
      if (config) {
        setEditingModelSyncConfig(config)
        modelSyncForm.setFieldsValue({
          workflow_name: config.workflow_name,
          workflow_file: config.workflow_file,
          artifacts_pattern: config.artifacts_pattern,
          file_patterns: config.file_patterns?.join('\n'),
          enabled: config.enabled,
          branch: config.branch || 'main',
        })
      } else {
        setEditingModelSyncConfig(null)
        modelSyncForm.resetFields()
      }
      setIsModelSyncModalVisible(true)
    }

    const handleOpenGlobalModelSyncConfig = () => {
      globalModelSyncConfigForm.setFieldsValue({
        model_sync_interval_minutes: globalModelSyncConfig?.sync_interval_minutes || 60,
        model_sync_days_back: globalModelSyncConfig?.days_back || 3,
      })
      setIsGlobalModelSyncConfigModalOpen(true)
    }

    const modelSyncColumns: ColumnsType<ModelSyncConfig> = [
      {
        title: 'Workflow 名称',
        dataIndex: 'workflow_name',
        key: 'workflow_name',
        width: 200,
        render: (text: string) => <Text strong>{text}</Text>,
      },
      {
        title: 'Workflow 文件',
        dataIndex: 'workflow_file',
        key: 'workflow_file',
        width: 250,
        render: (text: string) => <Text code>{text}</Text>,
      },
      {
        title: 'Artifacts 规则',
        dataIndex: 'artifacts_pattern',
        key: 'artifacts_pattern',
        width: 150,
        render: (pattern?: string) => pattern || '-',
      },
      {
        title: '上次同步',
        dataIndex: 'last_sync_at',
        key: 'last_sync_at',
        width: 180,
        render: (date?: string) =>
          date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '未同步',
      },
      {
        title: '状态',
        key: 'enabled',
        width: 80,
        render: (_: any, record: ModelSyncConfig) => (
          <Tag color={record.enabled ? 'green' : 'default'}>
            {record.enabled ? '启用' : '禁用'}
          </Tag>
        ),
      },
      {
        title: '操作',
        key: 'action',
        width: 200,
        fixed: 'right',
        render: (_: any, record: ModelSyncConfig) => (
          <Space>
            <Button
              type="link"
              size="small"
              icon={<SyncOutlined />}
              onClick={() => triggerModelSyncMutation.mutate(record.id)}
              loading={triggerModelSyncMutation.isPending && triggerModelSyncMutation.variables === record.id}
            >
              同步
            </Button>
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => openModelSyncModal(record)}
            >
              编辑
            </Button>
            <Button
              type="link"
              size="small"
              danger
              icon={<CloseCircleOutlined />}
              onClick={() => deleteModelSyncMutation.mutate(record.id)}
            >
              删除
            </Button>
          </Space>
        ),
      },
    ]

    return (
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {/* 全局同步配置 */}
        <Card
          title="全局同步配置"
          extra={
            isSuperAdmin && (
              <Button size="small" onClick={handleOpenGlobalModelSyncConfig}>
                编辑
              </Button>
            )
          }
        >
          <Descriptions column={2} bordered>
            <Descriptions.Item label="同步间隔">
              {globalModelSyncConfig?.sync_interval_minutes || 60} 分钟
            </Descriptions.Item>
            <Descriptions.Item label="同步天数范围">
              {globalModelSyncConfig?.days_back || 3} 天
            </Descriptions.Item>
          </Descriptions>
        </Card>

        {/* 模型同步配置列表 */}
        <Card
          title="模型同步配置列表"
          extra={
            isSuperAdmin && (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openModelSyncModal()}>
                创建同步配置
              </Button>
            )
          }
        >
          <Table
            columns={modelSyncColumns}
            dataSource={modelSyncConfigs}
            loading={modelSyncConfigsLoading}
            rowKey="id"
            pagination={{ pageSize: 10, showSizeChanger: false }}
            scroll={{ x: 1000 }}
          />
        </Card>
      </Space>
    )
  }

  return (
    <div style={{ padding: 24 }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0, display: 'inline-block', marginRight: 16 }}>
          <SettingOutlined style={{ marginRight: 8 }} />
          系统配置
        </Title>
        <Text type="secondary">管理系统核心配置和同步设置</Text>
      </div>

      {/* 系统状态警告 */}
      {status && !status.scheduler.running && (
        <Alert
          message="警告：数据同步调度器未运行"
          description="请检查后端服务状态，数据同步将不会自动执行"
          type="warning"
          showIcon
          style={{ marginBottom: 24 }}
        />
      )}

      {/* Tab 页 */}
      <Tabs
        activeKey={activeTabKey}
        onChange={setActiveTabKey}
        items={tabItems.map(tab => {
          if (tab.key === 'sync_config') {
            // 同步配置 Tab 使用嵌套 Tabs
            return {
              ...tab,
              children: (
                <Tabs
                  activeKey={syncActiveTab}
                  onChange={setSyncActiveTab}
                  items={[
                    {
                      key: 'project_cache',
                      label: (
                        <Space>
                          <SyncOutlined />
                          Git 缓存
                        </Space>
                      ),
                      children: renderProjectCacheTab(),
                    },
                    {
                      key: 'ci_sync',
                      label: (
                        <Space>
                          <ExperimentOutlined />
                          CI 结果
                        </Space>
                      ),
                      children: renderCISyncTab(),
                    },
                    {
                      key: 'model_sync',
                      label: (
                        <Space>
                          <DatabaseOutlined />
                          模型测试
                        </Space>
                      ),
                      children: renderModelSyncTab(),
                    },
                    {
                      key: 'daily_summary',
                      label: (
                        <Space>
                          <RobotOutlined />
                          项目动态
                        </Space>
                      ),
                      children: renderDailySummaryTab(),
                    },
                  ]}
                />
              ),
            }
          }
          return tab
        })}
        className="stripe-page-tabs"
      />

      {/* 应用配置编辑弹窗 */}
      <Modal
        title="编辑应用配置"
        open={isAppConfigModalOpen}
        onCancel={() => {
          setIsAppConfigModalOpen(false)
          appConfigForm.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={appConfigForm}
          layout="vertical"
          onFinish={handleUpdateAppConfig}
        >
          <Form.Item
            name="log_level"
            label="日志级别"
            rules={[{ required: true, message: '请选择日志级别' }]}
          >
            <Select>
              <Select.Option value="DEBUG">DEBUG</Select.Option>
              <Select.Option value="INFO">INFO</Select.Option>
              <Select.Option value="WARNING">WARNING</Select.Option>
              <Select.Option value="ERROR">ERROR</Select.Option>
              <Select.Option value="CRITICAL">CRITICAL</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item
            name="debug"
            label="调试模式"
            valuePropName="checked"
          >
            <Switch checkedChildren="开启" unCheckedChildren="关闭" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsAppConfigModalOpen(false)
                appConfigForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateAppMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* GitHub 配置编辑弹窗 */}
      <Modal
        title="编辑 GitHub 配置"
        open={isGitHubConfigModalOpen}
        onCancel={() => {
          setIsGitHubConfigModalOpen(false)
          gitHubConfigForm.resetFields()
        }}
        footer={null}
        width={500}
      >
          <Form
            form={gitHubConfigForm}
            layout="vertical"
            onFinish={handleUpdateGitHubConfig}
          >
            <Alert
              type={config?.github_config?.token_configured ? 'success' : 'warning'}
              showIcon
              message={config?.github_config?.token_configured ? '当前已配置 GitHub Token' : '当前未配置 GitHub Token（使用 .env 启动值）'}
              style={{ marginBottom: 16 }}
            />
            <Form.Item
              name="github_token"
              label="GitHub Token"
              rules={[
                { required: false },
                {
                  validator: (_, value) => {
                    if (value && value.trim() && value.trim().length < 10) {
                      return Promise.reject(new Error('GitHub Token 长度至少 10 个字符'))
                    }
                    return Promise.resolve()
                  },
                },
              ]}
              extra="输入新 Token 进行替换，留空则不修改。格式：ghp_xxxxxxxxxxxx"
            >
              <Input.Password placeholder={config?.github_config?.token_configured ? '请输入新的 GitHub Token（留空保持不变）' : '请输入 GitHub Token'} />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space>
                  {config?.github_config?.token_configured && (
                    <Popconfirm
                      title="清除 Token"
                      description="清除后回退到 .env 启动值，确认清除？"
                      onConfirm={handleClearGitHubToken}
                      okText="确认清除"
                      cancelText="取消"
                    >
                      <Button danger size="small">
                        清除 Token
                      </Button>
                    </Popconfirm>
                  )}
                </Space>
                <Space>
                  <Button onClick={() => {
                    setIsGitHubConfigModalOpen(false)
                    gitHubConfigForm.resetFields()
                  }}>
                    取消
                  </Button>
                  <Button type="primary" htmlType="submit" loading={updateGitHubMutation.isPending}>
                    保存
                  </Button>
                </Space>
              </Space>
            </Form.Item>
          </Form>
      </Modal>

      {/* 项目动态配置编辑弹窗 */}
      <Modal
        title="编辑项目动态配置"
        open={isDailySummaryConfigModalOpen}
        onCancel={() => {
          setIsDailySummaryConfigModalOpen(false)
          dailySummaryConfigForm.resetFields()
        }}
        onOk={() => {
          dailySummaryConfigForm.submit()
        }}
        width={700}
      >
        <Form
          form={dailySummaryConfigForm}
          layout="vertical"
          onFinish={(values) => {
            updateDailySummaryMutation.mutate({
              enabled: values.enabled,
              cron_hour: values.cron_hour,
              cron_minute: values.cron_minute,
              timezone: values.timezone,
              projects: values.projects,
            }, {
              onSuccess: () => {
                message.success('配置已更新')
                setIsDailySummaryConfigModalOpen(false)
              },
              onError: (error: any) => {
                message.error(error.response?.data?.detail || '更新失败')
              },
            })
          }}
          initialValues={{
            enabled: dailySummaryConfig?.enabled ?? true,
            cron_hour: dailySummaryConfig?.cron_hour ?? 8,
            cron_minute: dailySummaryConfig?.cron_minute ?? 0,
            timezone: dailySummaryConfig?.timezone ?? 'Asia/Shanghai',
            projects: dailySummaryConfig?.projects ?? [],
          }}
        >
          <Form.Item
            name="enabled"
            label="启用定时任务"
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>

          <Form.Item
            name="cron_hour"
            label="执行时间（时）"
            rules={[{ required: true, message: '请输入执行时间' }]}
          >
            <InputNumber min={0} max={23} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="cron_minute"
            label="执行时间（分）"
            rules={[{ required: true, message: '请输入执行时间' }]}
          >
            <InputNumber min={0} max={59} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="timezone"
            label="时区"
            rules={[{ required: true, message: '请选择时区' }]}
          >
            <Select>
              <Select.Option value="Asia/Shanghai">Asia/Shanghai (UTC+8)</Select.Option>
              <Select.Option value="UTC">UTC</Select.Option>
              <Select.Option value="America/New_York">America/New_York</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      {/* LLM 提供商编辑弹窗 */}
      <Modal
        title={isCreatingLLMProvider ? '新增 LLM 提供商' : `编辑 LLM 提供商 - ${selectedLLMProvider}`}
        open={isLLMProviderModalOpen}
        onCancel={() => {
          setIsLLMProviderModalOpen(false)
          setSelectedLLMProvider(null)
          setIsCreatingLLMProvider(false)
          llmProviderForm.resetFields()
        }}
        footer={null}
        width={600}
      >
        <Alert
          message="提示"
          description='设置“激活状态”为 true 的提供商将用于 AI 每日总结生成。同一时间只能有一个提供商被激活。CI 失败分析 Agent 使用 Docker LiteLLM / formatproxy 配置。'
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        {(selectedLLMProvider || isCreatingLLMProvider) && (
          <Form
            form={llmProviderForm}
            layout="vertical"
            onFinish={(values) => {
              if (isCreatingLLMProvider) {
                createLLMProviderMutation.mutate(values, {
                  onSuccess: (data: any) => {
                    message.success(data?.message || '创建成功')
                    setIsLLMProviderModalOpen(false)
                    setIsCreatingLLMProvider(false)
                    setSelectedLLMProvider(null)
                    llmProviderForm.resetFields()
                  },
                  onError: (error: any) => {
                    message.error(error.response?.data?.detail || '创建失败')
                  },
                })
                return
              }

              const config: any = {
                is_active: values.is_active,
                default_model: values.default_model,
                display_name: values.display_name,
                api_base_url: values.api_base_url,
              }
              if (values.api_key && values.api_key.trim() !== '') {
                config.api_key = values.api_key
              }
              updateLLMProviderMutation.mutate({
                provider: selectedLLMProvider!,
                config,
              }, {
                onSuccess: () => {
                  message.success('配置已更新')
                  setIsLLMProviderModalOpen(false)
                  setSelectedLLMProvider(null)
                },
                onError: (error: any) => {
                  message.error(error.response?.data?.detail || '更新失败')
                },
              })
            }}
          >
            <Form.Item
              name="is_active"
              label="激活状态（用于 AI 总结）"
              valuePropName="checked"
              extra="设置为激活后，该提供商将用于 AI 每日总结生成。同一时间只能有一个提供商被激活。"
            >
              <Switch checkedChildren="激活" unCheckedChildren="未激活" />
            </Form.Item>

            {isCreatingLLMProvider && (
              <Form.Item
                name="provider"
                label="提供商标识"
                rules={[{ required: true, message: '请输入提供商标识' }, { pattern: /^[a-zA-Z0-9_-]+$/, message: '只能包含字母、数字、下划线和连字符' }]}
                extra="唯一标识，创建后不可修改。如 openai、deepseek、zhipu、litellm"
              >
                <Input placeholder="例如：deepseek、zhipu、litellm" />
              </Form.Item>
            )}
            {!isCreatingLLMProvider && (
              <Form.Item label="提供商标识">
                <Tag>{selectedLLMProvider}</Tag>
              </Form.Item>
            )}

            <Form.Item
              name="display_name"
              label="显示名称"
              rules={[{ required: true, message: '请输入显示名称' }]}
            >
              <Input placeholder="例如：Docker LiteLLM、OpenAI GPT-4、通义千问" />
            </Form.Item>

            <Form.Item
              name="api_key"
              label="API Key"
              extra={
                <Space direction="vertical" size={0}>
                  <span>留空表示不修改，输入新的 API Key 将覆盖原有配置。</span>
                  {llmProviders?.find((p: any) => p.provider === selectedLLMProvider)?.api_key_configured && (
                    <span style={{ color: '#52c41a', fontWeight: 500 }}>
                      已配置
                    </span>
                  )}
                </Space>
              }
            >
              <Input.Password 
                placeholder="输入新的 API Key 以覆盖原有配置"
                autoComplete="new-password"
              />
            </Form.Item>

            <Form.Item
              name="default_model"
              label="默认模型"
              rules={[{ required: true, message: '请输入默认模型名称' }]}
            >
              <Input placeholder="例如：gpt-4o、qwen-plus、glm-4.5、litellm/qwen" />
            </Form.Item>

            <Form.Item
              name="api_base_url"
              label="API Base URL"
            >
              <Input placeholder="例如：http://localhost:4000/v1 或 https://api.openai.com/v1" />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
              <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                <Button onClick={() => {
                  setIsLLMProviderModalOpen(false)
                  setSelectedLLMProvider(null)
                  llmProviderForm.resetFields()
                }}>
                  取消
                </Button>
                <Button type="primary" htmlType="submit" loading={updateLLMProviderMutation.isPending}>
                  保存
                </Button>
              </Space>
            </Form.Item>
          </Form>
        )}
      </Modal>

      {/* 系统提示词编辑弹窗 */}
      <Modal
        title="编辑系统提示词"
        open={isSystemPromptModalOpen}
        onCancel={() => {
          setIsSystemPromptModalOpen(false)
          systemPromptForm.resetFields()
        }}
        footer={null}
        width={800}
      >
        <Alert
          message="说明"
          description="系统提示词用于指导 AI 生成项目动态总结的风格和内容重点。修改后会对新生成的总结生效。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Form
          form={systemPromptForm}
          layout="vertical"
          onFinish={(values) => {
            updateSystemPromptMutation.mutate({
              prompts: {
                ascend: values.ascend,
                vllm: values.vllm,
              },
              scope: 'daily_summary',
            }, {
              onSuccess: () => {
                message.success('系统提示词已更新')
                setIsSystemPromptModalOpen(false)
              },
              onError: (error: any) => {
                message.error(error.response?.data?.detail || '更新失败')
              },
            })
          }}
          initialValues={{
            ascend: systemPromptConfig?.prompts?.ascend || '',
            vllm: systemPromptConfig?.prompts?.vllm || '',
          }}
        >
          <Form.Item
            name="ascend"
            label={<Tag color="purple">vLLM Ascend 项目提示词</Tag>}
          >
            <Input.TextArea
              rows={6}
              placeholder="用于 vLLM Ascend 项目动态总结的系统提示词..."
            />
          </Form.Item>

          <Form.Item
            name="vllm"
            label={<Tag color="cyan">vLLM 项目提示词</Tag>}
          >
            <Input.TextArea
              rows={6}
              placeholder="用于 vLLM 项目动态总结的系统提示词..."
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsSystemPromptModalOpen(false)
                systemPromptForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateSystemPromptMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* Commit 分析系统提示词编辑弹窗 */}
      <Modal
        title="编辑 Commit 分析系统提示词"
        open={isCommitAnalysisPromptModalOpen}
        onCancel={() => {
          setIsCommitAnalysisPromptModalOpen(false)
          commitAnalysisPromptForm.resetFields()
        }}
        footer={null}
        width={800}
      >
        <Alert
          message="说明"
          description="系统提示词用于指导 AI 生成单个 Commit 分析总结。修改后会对新生成的 Commit AI 总结生效。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Form
          form={commitAnalysisPromptForm}
          layout="vertical"
          onFinish={(values) => {
            updateSystemPromptMutation.mutate({
              prompts: {
                ascend: values.ascend,
                vllm: values.vllm,
              },
              scope: 'commit_analysis',
            }, {
              onSuccess: () => {
                message.success('Commit 分析系统提示词已更新')
                setIsCommitAnalysisPromptModalOpen(false)
              },
              onError: (error: any) => {
                message.error(error.response?.data?.detail || '更新失败')
              },
            })
          }}
          initialValues={{
            ascend: commitAnalysisPromptConfig?.prompts?.ascend || '',
            vllm: commitAnalysisPromptConfig?.prompts?.vllm || '',
          }}
        >
          <Form.Item
            name="ascend"
            label={<Tag color="purple">vLLM Ascend Commit 分析提示词</Tag>}
          >
            <Input.TextArea
              rows={6}
              placeholder="用于 vLLM Ascend Commit 分析总结的系统提示词..."
            />
          </Form.Item>

          <Form.Item
            name="vllm"
            label={<Tag color="cyan">vLLM Commit 分析提示词</Tag>}
          >
            <Input.TextArea
              rows={6}
              placeholder="用于 vLLM Commit 分析总结的系统提示词..."
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsCommitAnalysisPromptModalOpen(false)
                commitAnalysisPromptForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateSystemPromptMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* CI 失败分析系统提示词编辑弹窗 */}
      <Modal
        title="编辑 CI 失败分析系统提示词"
        open={isCIFailureAnalysisPromptModalOpen}
        onCancel={() => {
          setIsCIFailureAnalysisPromptModalOpen(false)
          ciFailureAnalysisPromptForm.resetFields()
        }}
        footer={null}
        width={800}
      >
        <Alert
          message="说明"
          description="系统提示词用于指导 AI 分析 CI 失败 Job 的根因分类和改进建议。留空将使用基于 auto-bug-fixer 技能的默认提示词。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Form
          form={ciFailureAnalysisPromptForm}
          layout="vertical"
          onFinish={(values) => {
            updateSystemPromptMutation.mutate({
              prompts: {
                default: values.default,
              },
              scope: 'ci_failure_analysis',
            }, {
              onSuccess: () => {
                message.success('CI 失败分析系统提示词已更新')
                setIsCIFailureAnalysisPromptModalOpen(false)
              },
              onError: (error: any) => {
                message.error(error.response?.data?.detail || '更新失败')
              },
            })
          }}
          initialValues={{
            default: ciFailureAnalysisPromptConfig?.prompts?.default || '',
          }}
        >
          <Form.Item
            name="default"
            label={<Tag color="orange">CI 失败分析提示词</Tag>}
          >
            <Input.TextArea
              rows={10}
              placeholder="用于 CI 失败 Job 诊断的系统提示词...（留空使用默认 auto-bug-fixer 模板 + CI 分类附录）"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsCIFailureAnalysisPromptModalOpen(false)
                ciFailureAnalysisPromptForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateSystemPromptMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* ========== 同步配置相关弹窗 ========== */}

      {/* 缓存目录配置弹窗 */}
      <Modal
        title="编辑缓存目录配置"
        open={isCacheDirModalOpen}
        onCancel={() => {
          setIsCacheDirModalOpen(false)
          cacheDirForm.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={cacheDirForm}
          layout="vertical"
          onFinish={(values) => {
            if (values.github_cache_dir !== undefined) {
              handleUpdateCacheDir(values)
            }
            if (values.project_dashboard_cache_interval_minutes !== undefined) {
              handleUpdateCacheInterval(values)
            }
          }}
        >
          <Form.Item
            name="github_cache_dir"
            label="GitHub 缓存目录"
            extra="GitHub 本地缓存目录路径，留空使用默认值（data/repos/），例如：/mnt/data/github-cache"
          >
            <Input placeholder="留空使用默认值，例如：/mnt/data/github-cache" />
          </Form.Item>

          <Form.Item
            name="project_dashboard_cache_interval_minutes"
            label="缓存更新间隔（分钟）"
            rules={[{ required: true, message: '请输入缓存更新间隔' }]}
            extra="Git 仓库缓存固定每天更新一次；失败分析直接使用本地快照"
          >
            <InputNumber min={1440} max={1440} disabled style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsCacheDirModalOpen(false)
                cacheDirForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit">
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* CI 同步配置编辑弹窗 */}
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
          onFinish={handleUpdateCISyncConfig}
        >
          <Form.Item
            name="ci_sync_interval_minutes"
            label="同步间隔（分钟）"
            rules={[{ required: true, message: '请输入同步间隔' }]}
          >
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="ci_sync_days_back"
            label="同步天数范围"
            rules={[{ required: true, message: '请输入同步天数' }]}
          >
            <InputNumber min={1} max={30} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="ci_sync_max_runs_per_workflow"
            label="每个 Workflow 最多采集记录数"
            rules={[{ required: true, message: '请输入最大记录数' }]}
          >
            <InputNumber min={10} max={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="ci_sync_force_full_refresh"
            label="刷新模式"
            valuePropName="checked"
          >
            <Switch checkedChildren="全量覆盖" unCheckedChildren="增量刷新" />
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
            rules={[{ required: true, message: '请输入保留天数' }]}
          >
            <InputNumber min={7} max={365} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsSyncConfigModalOpen(false)
                syncConfigForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateCISyncMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* CI 手动同步弹窗 */}
      <Modal
        title="手动同步 CI 数据"
        open={isSyncModalOpen}
        onCancel={() => {
          setIsSyncModalOpen(false)
          syncConfigForm.resetFields()
          setElapsedTime(0)
        }}
        footer={null}
        width={500}
      >
        <Form
          form={syncConfigForm}
          layout="vertical"
          onFinish={handleTriggerCISync}
        >
          <Form.Item
            name="days_back"
            label="采集天数范围"
            rules={[{ required: true, message: '请输入天数' }]}
          >
            <InputNumber min={1} max={30} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="max_runs_per_workflow"
            label="每个 Workflow 最多采集记录数"
            rules={[{ required: true, message: '请输入最大记录数' }]}
          >
            <InputNumber min={10} max={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="force_full_refresh"
            label="同步模式"
            valuePropName="checked"
          >
            <Switch checkedChildren="强制全量覆盖" unCheckedChildren="增量刷新" />
          </Form.Item>

          {isSyncing && (
            <Alert
              message="同步进行中..."
              description={
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <Progress percent={progress?.progress_percentage || 0} />
                  <Text>已用时间：{elapsedTime} 秒</Text>
                  <Text>已采集：{progress?.total_collected || 0} 条记录</Text>
                </Space>
              }
              type="info"
              showIcon
              style={{ marginTop: 16 }}
            />
          )}

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsSyncModalOpen(false)
                syncConfigForm.resetFields()
                setElapsedTime(0)
              }} disabled={isSyncing}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={isSyncing}>
                开始同步
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 模型同步配置编辑弹窗 */}
      <Modal
        title={editingModelSyncConfig ? '编辑模型同步配置' : '创建模型同步配置'}
        open={isModelSyncModalVisible}
        onCancel={() => {
          setIsModelSyncModalVisible(false)
          modelSyncForm.resetFields()
          setEditingModelSyncConfig(null)
        }}
        footer={null}
        width={700}
      >
        <Form
          form={modelSyncForm}
          layout="vertical"
          onFinish={handleModelSyncFinish}
        >
          <Form.Item
            name="workflow_name"
            label="Workflow 名称"
            rules={[{ required: true, message: '请输入 Workflow 名称' }]}
          >
            <Input placeholder="例如：Model-Report-Sync" />
          </Form.Item>

          <Form.Item
            name="workflow_file"
            label="Workflow 文件"
            rules={[{ required: true, message: '请输入 Workflow 文件' }]}
            tooltip="GitHub workflow 文件路径"
          >
            <Input placeholder="例如：sync_model_report.yaml" />
          </Form.Item>

          <Form.Item
            name="artifacts_pattern"
            label="Artifacts 匹配模式"
            rules={[{ required: true, message: '请输入 Artifacts 模式' }]}
          >
            <Input placeholder="例如：model-report-*" />
          </Form.Item>

          <Form.Item
            name="file_patterns"
            label="文件匹配模式（每行一个）"
            tooltip="匹配报告文件的 glob 模式"
          >
            <Input.TextArea
              rows={4}
              placeholder="*.json&#10;reports/*.json"
            />
          </Form.Item>

          <Form.Item
            name="branch"
            label="分支"
            rules={[{ required: true, message: '请输入分支名称' }]}
          >
            <Input placeholder="main" />
          </Form.Item>

          <Form.Item
            name="enabled"
            label="启用状态"
            initialValue={true}
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsModelSyncModalVisible(false)
                modelSyncForm.resetFields()
                setEditingModelSyncConfig(null)
              }}>
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={createModelSyncMutation.isPending || updateModelSyncMutation.isPending}
              >
                {editingModelSyncConfig ? '更新' : '创建'}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 模型全局同步配置弹窗 */}
      <Modal
        title="编辑模型同步全局配置"
        open={isGlobalModelSyncConfigModalOpen}
        onCancel={() => {
          setIsGlobalModelSyncConfigModalOpen(false)
          globalModelSyncConfigForm.resetFields()
        }}
        footer={null}
        width={500}
      >
        <Form
          form={globalModelSyncConfigForm}
          layout="vertical"
          onFinish={handleUpdateGlobalModelSyncConfig}
        >
          <Form.Item
            name="model_sync_interval_minutes"
            label="同步间隔（分钟）"
            rules={[{ required: true, message: '请输入同步间隔' }]}
          >
            <InputNumber min={1} max={1440} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="model_sync_days_back"
            label="同步天数范围"
            rules={[{ required: true, message: '请输入同步天数' }]}
          >
            <InputNumber min={1} max={30} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsGlobalModelSyncConfigModalOpen(false)
                globalModelSyncConfigForm.resetFields()
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={updateGlobalModelSyncMutation.isPending}>
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default SystemConfig
