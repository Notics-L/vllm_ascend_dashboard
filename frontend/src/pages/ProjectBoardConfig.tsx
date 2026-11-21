import { useState, useEffect } from 'react'
import {
  Card,
  Tabs,
  Space,
  Button,
  Descriptions,
  Tag,
  Form,
  Input,
  InputNumber,
  Modal,
  message,
  Alert,
  Typography,
  Timeline,
  Empty,
  Table,
  Select,
  Row,
  Col,
  Popconfirm
} from 'antd'
import {
  SettingOutlined,
  SaveOutlined,
  SyncOutlined,
  MergeOutlined,
  UserOutlined,
  ClockCircleOutlined,
  PlusOutlined
} from '@ant-design/icons'
import {
  getDashboardConfig,
  updateModelSupportMatrix,
  updateLocalCache,
  rebuildLocalCache,
  fixLocalCache,
  getForceMergeRecords,
  type ModelSupportEntry,
  type ProjectDashboardConfig,
} from '../services/projectDashboard'
import { getGitCacheStatus, syncGitCache, type GitCacheStatus } from '../services/systemConfig'
import dayjs from 'dayjs'
import type { ColumnsType } from 'antd/es/table'

const { Text, Title } = Typography
const { TextArea } = Input

interface ModelSupportEntryWithKey extends ModelSupportEntry {
  key: string
}

// 特性列类型定义
interface FeatureColumn {
  key: string
  title: string
  width: number
  type: 'toggle' | 'multiSelect' | 'input'
  options?: string[]
  placeholder?: string
}

// 默认特性列定义（支持自定义扩展）
const DEFAULT_FEATURE_COLUMNS: FeatureColumn[] = [
  { key: 'chunked_prefill', title: 'Chunked Prefill', width: 100, type: 'toggle' },
  { key: 'automatic_prefix_cache', title: 'APC', width: 80, type: 'toggle' },
  { key: 'lora', title: 'LoRA', width: 80, type: 'toggle' },
  { key: 'speculative_decoding', title: 'Spec Dec', width: 90, type: 'toggle' },
  { key: 'async_scheduling', title: 'Async Sched', width: 90, type: 'toggle' },
  { key: 'tensor_parallel', title: 'TP', width: 70, type: 'toggle' },
  { key: 'pipeline_parallel', title: 'PP', width: 70, type: 'toggle' },
  { key: 'expert_parallel', title: 'EP', width: 70, type: 'toggle' },
  { key: 'data_parallel', title: 'DP', width: 70, type: 'toggle' },
  { key: 'prefilled_decode_disaggregation', title: 'PD 分离', width: 80, type: 'toggle' },
  { key: 'piecewise_aclgraph', title: 'Piecewise Graph', width: 100, type: 'toggle' },
  { key: 'fullgraph_aclgraph', title: 'Full Graph', width: 90, type: 'toggle' },
  { key: 'mlp_weight_prefetch', title: 'MLP Prefetch', width: 100, type: 'toggle' },
]

// 预定义的特性选项
const PREDEFINED_FEATURES: Record<string, { title: string; type: 'toggle' | 'multiSelect' | 'input'; options?: string[] }> = {
  chunked_prefill: { title: 'Chunked Prefill', type: 'toggle' },
  automatic_prefix_cache: { title: 'APC', type: 'toggle' },
  lora: { title: 'LoRA', type: 'toggle' },
  speculative_decoding: { title: 'Spec Dec', type: 'toggle' },
  async_scheduling: { title: 'Async Sched', type: 'toggle' },
  tensor_parallel: { title: 'TP', type: 'toggle' },
  pipeline_parallel: { title: 'PP', type: 'toggle' },
  expert_parallel: { title: 'EP', type: 'toggle' },
  data_parallel: { title: 'DP', type: 'toggle' },
  prefilled_decode_disaggregation: { title: 'PD 分离', type: 'toggle' },
  piecewise_aclgraph: { title: 'Piecewise Graph', type: 'toggle' },
  fullgraph_aclgraph: { title: 'Full Graph', type: 'toggle' },
  mlp_weight_prefetch: { title: 'MLP Prefetch', type: 'toggle' },
}

// 支持状态选项
const SUPPORT_OPTIONS = [
  { value: 'supported', label: '✅ 支持', color: '#52c41a' },
  { value: 'experimental', label: '🔵 实验', color: '#faad14' },
  { value: 'not_supported', label: '❌ 不支持', color: '#ff4d4f' },
  { value: 'untested', label: '🟡 未测试', color: '#d9d9d9' },
]

interface ForceMergeRecord {
  pr_number: number
  pr_title: string
  merged_by_user_id: number
  merged_by_username: string
  merged_at: string
  merge_sha: string
}

function ProjectBoardConfig() {
  const [modelForm] = Form.useForm()

  const [configs, setConfigs] = useState<ProjectDashboardConfig[]>([])
  const [configsLoading, setConfigsLoading] = useState(false)

  const [isCacheUpdating, setIsCacheUpdating] = useState(false)

  const [modelEntries, setModelEntries] = useState<ModelSupportEntryWithKey[]>([])
  const [featureColumns, setFeatureColumns] = useState<FeatureColumn[]>(DEFAULT_FEATURE_COLUMNS)
  const [isFeatureModalOpen, setIsFeatureModalOpen] = useState(false)
  const [isFeatureEditModalOpen, setIsFeatureEditModalOpen] = useState(false)
  const [editingFeature, setEditingFeature] = useState<FeatureColumn | null>(null)
  const [featureFormType, setFeatureFormType] = useState<string>('toggle')
  const [featureForm] = Form.useForm()
  const [hasChanges, setHasChanges] = useState(false)
  const [originalModelEntries, setOriginalModelEntries] = useState<ModelSupportEntryWithKey[]>([])
  const [originalFeatureColumns, setOriginalFeatureColumns] = useState<FeatureColumn[]>(DEFAULT_FEATURE_COLUMNS)

  const [forceMergeRecords, setForceMergeRecords] = useState<ForceMergeRecord[]>([])
  const [forceMergeRecordsLoading, setForceMergeRecordsLoading] = useState(false)

  const [activeTabKey, setActiveTabKey] = useState('model')
  const [cacheDirForm] = Form.useForm()
  const [isCacheDirModalOpen, setIsCacheDirModalOpen] = useState(false)
  const [githubCacheDir, setGithubCacheDir] = useState<string>('')
  const [projectDashboardCacheInterval, setProjectDashboardCacheInterval] = useState<number>(60)
  const [modelMatrixLoaded, setModelMatrixLoaded] = useState(false)
  const [gitCacheStatus, setGitCacheStatus] = useState<GitCacheStatus | null>(null)
  const [gitCacheStatusLoading, setGitCacheStatusLoading] = useState(false)

  // 加载配置
  useEffect(() => {
    loadConfigs()
    loadForceMergeRecords()
    loadGitHubCacheDir()
    loadGitCacheStatus()
  }, [])

  const loadConfigs = async () => {
    setConfigsLoading(true)
    try {
      const data = await getDashboardConfig()
      setConfigs(data.configs)
    } catch (error: any) {
      message.error('加载配置失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setConfigsLoading(false)
    }
  }

  const loadForceMergeRecords = async () => {
    setForceMergeRecordsLoading(true)
    try {
      const data = await getForceMergeRecords()
      setForceMergeRecords(data.records)
    } catch (error: any) {
      message.error('加载合入记录失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setForceMergeRecordsLoading(false)
    }
  }

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

  // 加载 Git 缓存状态
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

  // 加载模型矩阵
  const loadModelMatrix = async () => {
    const matrixConfig = configs.find(c => c.config_key === 'model_support_matrix')
    if (matrixConfig && matrixConfig.config_value) {
      // 从数据库配置加载特性列
      if (matrixConfig.config_value.featureColumns) {
        setFeatureColumns(matrixConfig.config_value.featureColumns)
        setOriginalFeatureColumns(matrixConfig.config_value.featureColumns)
      }
      // 从数据库配置加载模型数据
      if (matrixConfig.config_value.entries) {
        const entries = matrixConfig.config_value.entries.map((e: any, idx: number) => ({
          ...e,
          key: `${idx}-${e.model_name}`,
        }))
        setModelEntries(entries)
        setOriginalModelEntries(entries)
      }
      setModelMatrixLoaded(true)
    } else {
      // 数据库没有配置时，使用空列表
      setModelEntries([])
      setOriginalModelEntries([])
      setModelMatrixLoaded(true)
    }
  }

  // 当切换到模型 tab 或配置更新时加载模型矩阵
  useEffect(() => {
    if (activeTabKey === 'model' && configs.length > 0) {
      loadModelMatrix()
    }
  }, [activeTabKey, configs])

  // 检测数据变化
  useEffect(() => {
    // 比较模型数据
    const modelChanged = JSON.stringify(modelEntries) !== JSON.stringify(originalModelEntries)
    // 比较特性列
    const featuresChanged = JSON.stringify(featureColumns) !== JSON.stringify(originalFeatureColumns)
    setHasChanges(modelChanged || featuresChanged)
  }, [modelEntries, featureColumns, originalModelEntries, originalFeatureColumns])

  // 更新缓存
  const handleUpdateCache = async () => {
    setIsCacheUpdating(true)
    try {
      await updateLocalCache()
      message.success('本地缓存已更新')
      // 刷新 Git 缓存状态
      await loadGitCacheStatus()
    } catch (error: any) {
      message.error('更新缓存失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setIsCacheUpdating(false)
    }
  }

  // 重建缓存
  const handleRebuildCache = async () => {
    Modal.confirm({
      title: '确认重建缓存？',
      content: '这将删除现有的 git 仓库缓存并重新克隆完整历史和 tags。首次克隆可能需要 5-10 分钟，请耐心等待。确定要继续吗？',
      okText: '确认',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        setIsCacheUpdating(true)
        try {
          await rebuildLocalCache()
          message.success('本地缓存已重建，正在获取 git 历史和 tags，这可能需要几分钟...')
          // 刷新 Git 缓存状态
          await loadGitCacheStatus()
        } catch (error: any) {
          message.error('重建缓存失败：' + (error.response?.data?.detail || error.message))
        } finally {
          setIsCacheUpdating(false)
        }
      },
    })
  }

  // 修复缓存
  const handleFixCache = async () => {
    Modal.confirm({
      title: '确认修复缓存？',
      content: '这将清理 git 锁文件、重置本地修改并获取最新状态。比重建缓存更快，推荐首先尝试此操作。',
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        setIsCacheUpdating(true)
        try {
          await fixLocalCache()
          message.success('本地缓存已修复')
          // 刷新 Git 缓存状态
          await loadGitCacheStatus()
        } catch (error: any) {
          message.error('修复缓存失败：' + (error.response?.data?.detail || error.message))
        } finally {
          setIsCacheUpdating(false)
        }
      },
    })
  }

  // 打开缓存配置编辑
  const handleOpenCacheDir = () => {
    cacheDirForm.setFieldsValue({
      github_cache_dir: githubCacheDir,
      project_dashboard_cache_interval_minutes: projectDashboardCacheInterval,
    })
    setIsCacheDirModalOpen(true)
  }

  // 同步 Git 缓存（支持多仓库）
  const handleSyncGitCache = async (repoType: 'ascend' | 'vllm' | 'all') => {
    setIsCacheUpdating(true)
    try {
      const result = await syncGitCache(repoType)
      if (result.success) {
        const messages = result.results.map(r => `${r.repo}: ${r.message}`).join('; ')
        message.success(`同步成功: ${messages}`)
      } else {
        const failed = result.results.filter(r => !r.success).map(r => `${r.repo}: ${r.message}`).join('; ')
        message.error(`部分同步失败: ${failed}`)
      }
      // 刷新 Git 缓存状态
      await loadGitCacheStatus()
    } catch (error: any) {
      message.error('同步失败：' + (error.response?.data?.detail || error.message))
    } finally {
      setIsCacheUpdating(false)
    }
  }

  // 更新缓存目录
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

  // 更新缓存更新间隔
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

  // 保存模型矩阵
  const handleSaveModelMatrix = async () => {
    try {
      // 移除 key 字段，只保存数据字段
      const entries = modelEntries.map(({ key: _key, ...rest }) => rest)
      // 保存模型数据和特性列配置
      await updateModelSupportMatrix({ entries, featureColumns })
      message.success('模型支持矩阵已保存')
      // 更新原始数据状态
      setOriginalModelEntries(modelEntries)
      setOriginalFeatureColumns(featureColumns)
      setHasChanges(false)
      // 重置加载标志
      setModelMatrixLoaded(false)
      // 重新加载配置
      await loadConfigs()
    } catch (error: any) {
      message.error('保存失败：' + (error.response?.data?.detail || error.message))
    }
  }

  // 添加模型条目
  const handleAddModelEntry = () => {
    const newEntry: ModelSupportEntryWithKey = {
      key: `new-${Date.now()}`,
      model_name: '',
      series: 'Other',
      support: 'untested',
      note: null,
      doc_link: null,
      weight_format: null,
      kv_cache_type: null,
      supported_hardware: null,
      chunked_prefill: null,
      automatic_prefix_cache: null,
      lora: null,
      speculative_decoding: null,
      async_scheduling: null,
      tensor_parallel: null,
      pipeline_parallel: null,
      expert_parallel: null,
      data_parallel: null,
      prefilled_decode_disaggregation: null,
      piecewise_aclgraph: null,
      fullgraph_aclgraph: null,
      max_model_len: null,
      mlp_weight_prefetch: null,
    }
    setModelEntries([...modelEntries, newEntry])
  }

  // 编辑模型条目（用于打开详情弹窗，如果需要）
  const handleEditModelEntry = (entry: ModelSupportEntryWithKey) => {
    // 内联编辑模式下，这个函数可以保留用于未来扩展
    message.info(`编辑模型：${entry.model_name}`)
  }

  // 删除模型条目
  const handleDeleteModelEntry = (key: string) => {
    setModelEntries(modelEntries.filter(e => e.key !== key))
  }

  // ============ 特性列管理 ============

  // 打开特性管理弹窗
  const handleOpenFeatureManage = () => {
    setIsFeatureModalOpen(true)
  }

  // 打开添加特性弹窗
  const handleAddFeature = () => {
    setEditingFeature(null)
    featureForm.resetFields()
    featureForm.setFieldsValue({
      type: 'toggle',
      width: 80,
    })
    setFeatureFormType('toggle')
    setIsFeatureEditModalOpen(true)
  }

  // 编辑特性列
  const handleEditFeature = (feature: FeatureColumn) => {
    setEditingFeature(feature)
    featureForm.setFieldsValue(feature)
    setFeatureFormType(feature.type)
    setIsFeatureEditModalOpen(true)
  }

  // 删除特性列
  const handleDeleteFeature = (key: string) => {
    const feature = featureColumns.find(f => f.key === key)
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除特性列"${feature?.title}"吗？该操作会从所有模型数据中删除此字段。`,
      okText: '确定',
      cancelText: '取消',
      okType: 'danger',
      onOk: () => {
        setFeatureColumns(featureColumns.filter(f => f.key !== key))
        // 同时从所有条目中删除该字段
        setModelEntries(modelEntries.map(entry => {
          const newEntry: any = { ...entry }
          delete newEntry[key]
          return newEntry
        }))
        message.success('特性列已删除')
      },
    })
  }

  // 保存特性列
  const handleSaveFeature = (values: any) => {
    // 处理选项字段：将逗号分隔的字符串转换为数组
    let options: string[] | undefined
    if (values.options && typeof values.options === 'string') {
      options = values.options.split(',').map((s: string) => s.trim()).filter(Boolean)
    }

    const featureData: FeatureColumn = {
      key: values.key,
      title: values.title,
      width: values.width,
      type: values.type,
      options: options,
    }

    if (editingFeature) {
      // 编辑模式：更新现有列
      if (editingFeature.key !== values.key) {
        // key 改变了，需要迁移数据
        setModelEntries(modelEntries.map(entry => {
          const newEntry: any = { ...entry }
          newEntry[values.key] = newEntry[editingFeature.key]
          delete newEntry[editingFeature.key]
          return newEntry
        }))
      }
      setFeatureColumns(featureColumns.map(f => 
        f.key === editingFeature.key ? featureData : f
      ))
      message.success('特性列已更新')
    } else {
      // 新增模式
      if (featureColumns.some(f => f.key === values.key)) {
        message.error('特性列已存在')
        return
      }
      setFeatureColumns([...featureColumns, featureData])
      message.success('特性列已添加')
    }
    setIsFeatureEditModalOpen(false)
    featureForm.resetFields()
  }

  // 重置为默认特性列
  const handleResetFeatures = () => {
    Modal.confirm({
      title: '确认重置',
      content: '确定要重置为默认特性列吗？自定义的特性列将丢失。',
      okText: '确定',
      cancelText: '取消',
      onOk: () => {
        setFeatureColumns(DEFAULT_FEATURE_COLUMNS)
        message.success('已重置为默认特性列')
      },
    })
  }

  // 模型表格列定义（支持内联编辑）
  const modelColumns: ColumnsType<ModelSupportEntryWithKey> = [
    {
      title: '模型名称',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 180,
      fixed: 'left',
      sorter: (a, b) => a.model_name.localeCompare(b.model_name),
      render: (model_name: string, record: ModelSupportEntryWithKey) => (
        <Input
          value={model_name}
          onChange={(e) => handleUpdateEntryField(record.key, 'model_name', e.target.value)}
          placeholder="模型名称"
          size="small"
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '系列',
      dataIndex: 'series',
      key: 'series',
      width: 130,
      render: (series: string, record: ModelSupportEntryWithKey) => {
        // 动态获取表格中已有的系列选项
        const existingSeries = Array.from(new Set(modelEntries.map(e => e.series))).filter(Boolean)
        return (
          <Select
            value={series}
            onChange={(value) => handleUpdateEntryField(record.key, 'series', value)}
            options={existingSeries.map(s => ({ label: s, value: s }))}
            showSearch
            allowClear
            placeholder="输入或选择系列"
            style={{ width: '100%' }}
            size="small"
            filterOption={(input, option) =>
              (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
            }
            onSearch={(value) => {
              if (value) {
                handleUpdateEntryField(record.key, 'series', value)
              }
            }}
            onBlur={(e) => {
              const inputValue = (e.target as HTMLInputElement).value
              if (inputValue) {
                handleUpdateEntryField(record.key, 'series', inputValue)
              }
            }}
          />
        )
      },
    },
    {
      title: '支持状态',
      dataIndex: 'support',
      key: 'support',
      width: 110,
      render: (support: string, record: ModelSupportEntryWithKey) => {
        const option = SUPPORT_OPTIONS.find(o => o.value === support) || SUPPORT_OPTIONS[0]
        return (
          <Select
            value={support}
            onChange={(value) => handleUpdateEntryField(record.key, 'support', value)}
            options={SUPPORT_OPTIONS}
            style={{ width: '100%' }}
            size="small"
            styles={{ popup: { root: { minWidth: '120px' } } }}
          />
        )
      },
    },
    {
      title: '支持硬件',
      dataIndex: 'supported_hardware',
      key: 'supported_hardware',
      width: 150,
      render: (hardware: string | null, record: ModelSupportEntryWithKey) => {
        // 将存储的字符串转换为数组
        const hardwareArray = hardware ? hardware.split('/').filter(Boolean) : []
        return (
          <Select
            mode="multiple"
            value={hardwareArray}
            onChange={(values) => handleUpdateEntryField(record.key, 'supported_hardware', values.join('/'))}
            options={['A2', 'A3', '310P'].map(h => ({ label: h, value: h }))}
            placeholder="选择硬件"
            style={{ width: '100%' }}
            size="small"
            maxTagCount="responsive"
          />
        )
      },
    },
    {
      title: 'Max Len',
      dataIndex: 'max_model_len',
      key: 'max_model_len',
      width: 100,
      render: (value: string | number | null, record: ModelSupportEntryWithKey) => (
        <Input
          value={value || ''}
          onChange={(e) => handleUpdateEntryField(record.key, 'max_model_len', e.target.value)}
          placeholder="如：128k"
          size="small"
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '权重格式',
      dataIndex: 'weight_format',
      key: 'weight_format',
      width: 180,
      render: (value: string | null, record: ModelSupportEntryWithKey) => {
        // 将存储的字符串转换为数组
        const valueArray = value ? value.split('/').filter(Boolean) : []
        return (
          <Select
            mode="multiple"
            value={valueArray}
            onChange={(values) => handleUpdateEntryField(record.key, 'weight_format', values.join('/'))}
            options={[
              { label: 'BFloat16', value: 'BFloat16' },
              { label: 'Float16', value: 'Float16' },
              { label: 'W8A8', value: 'W8A8' },
              { label: 'W4A8', value: 'W4A8' },
              { label: 'W4A16', value: 'W4A16' },
              { label: 'W8A16', value: 'W8A16' },
              { label: 'HiFloat8', value: 'HiFloat8' },
              { label: 'FP8', value: 'FP8' },
              { label: 'MXFP8', value: 'MXFP8' },
            ]}
            placeholder="选择权重格式"
            style={{ width: '100%' }}
            size="small"
            maxTagCount="responsive"
          />
        )
      },
    },
    {
      title: 'KV Cache',
      dataIndex: 'kv_cache_type',
      key: 'kv_cache_type',
      width: 150,
      render: (value: string | null, record: ModelSupportEntryWithKey) => {
        // 将存储的字符串转换为数组
        const valueArray = value ? value.split('/').filter(Boolean) : []
        return (
          <Select
            mode="multiple"
            value={valueArray}
            onChange={(values) => handleUpdateEntryField(record.key, 'kv_cache_type', values.join('/'))}
            options={[
              { label: 'BFloat16', value: 'BFloat16' },
              { label: 'Float16', value: 'Float16' },
              { label: 'FP8', value: 'FP8' },
              { label: 'Int8', value: 'Int8' },
            ]}
            placeholder="选择 KV Cache"
            style={{ width: '100%' }}
            size="small"
            maxTagCount="responsive"
          />
        )
      },
    },
    // 动态生成特性列
    ...featureColumns.map(col => ({
      title: col.title,
      dataIndex: col.key,
      key: col.key,
      width: col.width,
      render: (_: any, record: ModelSupportEntryWithKey) => {
        const value = (record as any)[col.key]
        
        if (col.type === 'multiSelect') {
          // 将存储的字符串转换为数组
          const valueArray = typeof value === 'string' ? value.split('/').filter(Boolean) : []
          return (
            <Select
              mode="multiple"
              value={valueArray}
              onChange={(values) => handleUpdateEntryField(record.key, col.key, values.join('/'))}
              options={col.options?.map(opt => ({ label: opt, value: opt }))}
              placeholder="选择"
              style={{ width: '100%' }}
              size="small"
              maxTagCount="responsive"
            />
          )
        } else if (col.type === 'toggle') {
          return (
            <FeatureToggle
              value={value as boolean | null | undefined}
              onChange={(val) => handleUpdateEntryField(record.key, col.key, val)}
            />
          )
        } else if (col.type === 'input') {
          return (
            <Input
              value={value || ''}
              onChange={(e) => handleUpdateEntryField(record.key, col.key, e.target.value)}
              placeholder={col.placeholder || ''}
              size="small"
              style={{ width: '100%' }}
            />
          )
        }
        return null
      },
    })),
    {
      title: '文档',
      key: 'doc_link',
      dataIndex: 'doc_link',
      width: 150,
      render: (link: string | null, record: ModelSupportEntryWithKey) => (
        <Input
          value={link || ''}
          onChange={(e) => handleUpdateEntryField(record.key, 'doc_link', e.target.value)}
          placeholder="如：https://docs.example.com"
          size="small"
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '备注',
      dataIndex: 'note',
      key: 'note',
      width: 150,
      render: (note: string | null, record: ModelSupportEntryWithKey) => (
        <Input
          value={note || ''}
          onChange={(e) => handleUpdateEntryField(record.key, 'note', e.target.value)}
          placeholder="备注说明"
          size="small"
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      fixed: 'right',
      render: (_: any, record: ModelSupportEntryWithKey) => (
        <Popconfirm
          title="确定要删除此模型吗？"
          onConfirm={() => handleDeleteModelEntry(record.key)}
          okText="确定"
          cancelText="取消"
        >
          <Button size="small" danger type="text">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  // 更新条目字段
  const handleUpdateEntryField = (key: string, field: string, value: any) => {
    setModelEntries(modelEntries.map(entry => {
      if (entry.key === key) {
        return { ...entry, [field]: value }
      }
      return entry
    }))
  }

  // 渲染特性值（支持/不支持/空）
  const renderFeatureValue = (value: boolean | null | undefined) => {
    if (value === true) return <span style={{ color: '#52c41a' }}>✅</span>
    if (value === false) return <span style={{ color: '#ff4d4f' }}>❌</span>
    return <span style={{ color: '#d9d9d9' }}>-</span>
  }

  // Tab 内容

  const modelTabContent = (
    <div>
      <Card
        title="模型支持矩阵"
        extra={
          <Space>
            <Button
              icon={<SettingOutlined />}
              onClick={handleOpenFeatureManage}
            >
              管理特性列
            </Button>
            <Button
              icon={<PlusOutlined />}
              type="primary"
              onClick={handleAddModelEntry}
            >
              添加模型
            </Button>
            <Button
              icon={<SaveOutlined />}
              onClick={handleSaveModelMatrix}
              type={hasChanges ? 'primary' : 'default'}
            >
              保存所有更改 {hasChanges && <span style={{ marginLeft: 4 }}>●</span>}
            </Button>
          </Space>
        }
      >
        <Text type="secondary" style={{ marginBottom: 12, display: 'block' }}>
          提示：所有字段都支持直接编辑，点击特性列可快速切换状态。可点击"管理特性列"自定义特性
        </Text>
        {modelEntries.length === 0 ? (
          <Empty description="暂无模型配置，请点击右上角'添加模型'按钮开始配置" />
        ) : (
          <Table
            columns={modelColumns}
            dataSource={modelEntries}
            rowKey="key"
            pagination={{ pageSize: 15 }}
            scroll={{ x: 1600 }}
            size="small"
          />
        )}
      </Card>
    </div>
  )

  const forceMergeTabContent = (
    <Card title="强行合入 PR 记录">
      {forceMergeRecordsLoading ? (
        <Text>加载中...</Text>
      ) : forceMergeRecords.length === 0 ? (
        <Empty description="暂无强行合入记录" />
      ) : (
        <Timeline
          items={forceMergeRecords.map(record => ({
            key: `${record.pr_number}-${record.merged_at}`,
            color: 'red',
            children: (
              <Card size="small" style={{ marginTop: 8 }}>
                <Descriptions column={2} bordered size="small">
                  <Descriptions.Item label="PR" span={2}>
                    <a
                      href={`https://github.com/vllm-project/vllm-ascend/pull/${record.pr_number}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      #{record.pr_number} - {record.pr_title}
                    </a>
                  </Descriptions.Item>
                  <Descriptions.Item label="合入时间">
                    <ClockCircleOutlined style={{ marginRight: 4 }} />
                    {dayjs(record.merged_at).format('YYYY-MM-DD HH:mm:ss')}
                  </Descriptions.Item>
                  <Descriptions.Item label="操作人">
                    <UserOutlined style={{ marginRight: 4 }} />
                    {record.merged_by_username} (ID: {record.merged_by_user_id})
                  </Descriptions.Item>
                  <Descriptions.Item label="Merge SHA" span={2}>
                    <Tag color="purple">{record.merge_sha?.slice(0, 7) || 'N/A'}</Tag>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          }))}
        />
      )}
    </Card>
  )

  const tabItems = [
    {
      key: 'model',
      label: (
        <Space>
          <SettingOutlined />
          模型支持矩阵
        </Space>
      ),
      children: modelTabContent,
    },
    {
      key: 'forcemerge',
      label: (
        <Space>
          <MergeOutlined />
          强行合入记录
        </Space>
      ),
      children: forceMergeTabContent,
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0, display: 'inline-block', marginRight: 16 }}>
          <SettingOutlined style={{ marginRight: 8 }} />
          项目看板配置
        </Title>
        <Text type="secondary">配置项目看板设置和数据</Text>
      </div>

      {/* Tab 页 */}
      <Tabs
        activeKey={activeTabKey}
        onChange={setActiveTabKey}
        className="stripe-page-tabs"
        items={tabItems}
      />

      {/* Git 缓存配置编辑弹窗 */}
      <Modal
        title="编辑 Git 缓存配置"
        open={isCacheDirModalOpen}
        onCancel={() => {
          setIsCacheDirModalOpen(false)
          cacheDirForm.resetFields()
        }}
        footer={null}
        width={600}
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
            extra="GitHub 本地缓存目录路径，留空使用默认值 (data/repos/)，例如：/mnt/data/github-cache"
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

      {/* 特性列管理弹窗 */}
      <Modal
        title="管理特性列"
        open={isFeatureModalOpen}
        onCancel={() => setIsFeatureModalOpen(false)}
        width={800}
        footer={
          <Space style={{ justifyContent: 'flex-end' }}>
            <Button onClick={() => setIsFeatureModalOpen(false)}>关闭</Button>
            <Button danger icon={<SyncOutlined />} onClick={handleResetFeatures}>
              重置为默认
            </Button>
          </Space>
        }
      >
        <Alert
          message="特性列管理"
          description="您可以添加、编辑或删除特性列。自定义的特性列会立即应用到表格中。点击'添加特性列'按钮开始添加。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        
        <div style={{ marginBottom: 16 }}>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAddFeature}>
            添加特性列
          </Button>
        </div>

        <Table
          columns={[
            { title: '列名', dataIndex: 'title', key: 'title' },
            { title: '字段 Key', dataIndex: 'key', key: 'key' },
            { 
              title: '类型', 
              dataIndex: 'type', 
              key: 'type',
              render: (type: string) => {
                const typeMap: Record<string, string> = {
                  toggle: '切换',
                  multiSelect: '多选',
                  input: '输入',
                }
                return typeMap[type] || type
              }
            },
            { title: '宽度', dataIndex: 'width', key: 'width', render: (w: number) => `${w}px` },
            {
              title: '操作',
              key: 'action',
              render: (_: any, record: FeatureColumn) => (
                <Space>
                  <Button size="small" onClick={() => handleEditFeature(record)}>编辑</Button>
                  <Button size="small" danger onClick={() => handleDeleteFeature(record.key)}>删除</Button>
                </Space>
              ),
            },
          ]}
          dataSource={featureColumns}
          rowKey="key"
          pagination={false}
          size="small"
        />
      </Modal>

      {/* 添加/编辑特性列弹窗 */}
      <Modal
        title={editingFeature ? '编辑特性列' : '添加特性列'}
        open={isFeatureEditModalOpen}
        onCancel={() => {
          setIsFeatureEditModalOpen(false)
          featureForm.resetFields()
          setEditingFeature(null)
        }}
        width={600}
        footer={null}
      >
        <Form
          form={featureForm}
          layout="vertical"
          onFinish={handleSaveFeature}
          initialValues={{
            type: 'toggle',
            width: 80,
          }}
        >
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="title"
                label="列标题"
                rules={[{ required: true, message: '请输入列标题' }]}
              >
                <Input placeholder="自定义特性" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="key"
                label="字段 Key"
                rules={[{ required: true, message: '请输入字段 Key' }]}
                extra="英文字母和下划线"
              >
                <Input placeholder="custom_feature" disabled={!!editingFeature} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="width"
                label="列宽度"
                rules={[{ required: true, message: '请输入列宽度' }]}
              >
                <InputNumber min={60} max={300} style={{ width: '100%' }} addonAfter="px" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="type"
                label="字段类型"
                rules={[{ required: true, message: '请选择字段类型' }]}
              >
                <Select
                  options={[
                    { label: '切换按钮 (✅/❌/⚪)', value: 'toggle' },
                    { label: '多选下拉', value: 'multiSelect' },
                    { label: '文本输入', value: 'input' },
                  ]}
                  onChange={(value) => setFeatureFormType(value)}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            name="options"
            label="选项列表（仅多选类型需要）"
            extra="多个选项用逗号分隔，如：Option1,Option2,Option3"
          >
            <Input 
              placeholder="Option1,Option2,Option3" 
              disabled={featureFormType !== 'multiSelect'}
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => {
                setIsFeatureEditModalOpen(false)
                featureForm.resetFields()
                setEditingFeature(null)
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
    </div>
  )
}

export default ProjectBoardConfig

// 特性切换按钮组件
function FeatureToggle({ value, onChange }: { value: boolean | null | undefined; onChange: (val: boolean | null) => void }) {
  const handleClick = () => {
    if (value === true) onChange(false)
    else if (value === false) onChange(null)
    else onChange(true)
  }

  const display = value === true ? '✅' : value === false ? '❌' : '⚪'

  return (
    <Button
      block
      size="small"
      onClick={handleClick}
      style={{
        backgroundColor: 'transparent',
        border: 'none',
        boxShadow: 'none',
        color: value === null ? '#d9d9d9' : undefined,
        cursor: 'pointer',
      }}
    >
      {display}
    </Button>
  )
}
