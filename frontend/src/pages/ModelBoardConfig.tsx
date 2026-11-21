import { useState, useEffect } from 'react'
import {
  Card,
  Table,
  Space,
  Button,
  Switch,
  Tag,
  Typography,
  Modal,
  Form,
  Input,
  InputNumber,
  message,
  Popconfirm,
  Select,
  Alert,
  Tabs,
  Upload,
  Divider,
  Collapse,
  Descriptions,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  SyncOutlined,
  DatabaseOutlined,
  UploadOutlined,
  CopyOutlined,
  ImportOutlined,
  LinkOutlined,
} from '@ant-design/icons'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { ColumnsType } from 'antd/es/table'
import { useSystemConfig } from '../hooks/useSystemConfig'
import {
  getSyncConfigs,
  deleteSyncConfig,
  createSyncConfig,
  updateSyncConfig,
  triggerSync,
  getModels,
  deleteModel,
  createModel,
  updateModel,
  getModelReports,
  uploadReport,
  syncReports,
  deleteReport
} from '../services/models'
import type {
  ModelSyncConfig,
  ModelSyncConfigCreate,
  ModelConfig,
  ModelReport,
} from '../types/models'
import dayjs from 'dayjs'
import { ReportEditModal } from '../components/ReportEditModal'
import { ReportDetailModal } from '../components/ReportDetailModal'
import { StartupCommandDisplay } from '../components/StartupCommandDisplay'

const { Title, Text } = Typography
const { TextArea } = Input

interface StartupCommandsEditorProps {
  form: any
}

// 启动命令配置结构：version -> scenario -> hardware -> command
type StartupCommandConfig = Record<string, Record<string, Record<string, string>>>

// 预定义选项
const VLLM_VERSIONS = ['default', 'v0.16.0', 'v0.17.0', 'v0.18.0', 'v0.19.0']
const PD_NODE_TYPES = [
  { value: 'p', label: 'P 节点' },
  { value: 'd', label: 'D 节点' },
]
const HARDWARE_OPTIONS = [
  { value: 'A2', label: 'Atlas A2' },
  { value: 'A3', label: 'Atlas A3' },
  { value: 'A5', label: 'Atlas A5' },
  { value: '310P', label: 'Ascend 310P' },
]

// 部署场景选项（支持下拉和自定义）
const DEPLOYMENT_SCENARIO_OPTIONS = [
  { value: 'standard', label: '标准部署' },
  { value: 'pd-disaggregation', label: 'PD 分离' },
]

// 部署场景显示名称
const SCENARIO_LABELS: Record<string, string> = {
  'standard': '标准部署',
  'pd-disaggregation': 'PD 分离',
}

const HARDWARE_LABELS: Record<string, string> = {
  'A2': 'Atlas A2',
  'A3': 'Atlas A3',
  'A5': 'Atlas A5',
  '310P': 'Ascend 310P',
}

/**
 * 启动命令编辑器组件
 * 支持多维度配置（版本 × 场景 × 硬件）
 */
function StartupCommandsEditor({ form }: StartupCommandsEditorProps) {
  const [commands, setCommands] = useState<StartupCommandConfig>({})
  const [editingKey, setEditingKey] = useState<string | null>(null)  // version:scenario:hardware
  const [editingValue, setEditingValue] = useState('')
  const [isYamlModalVisible, setIsYamlModalVisible] = useState(false)
  const [yamlContent, setYamlContent] = useState('')
  const [isAddModalVisible, setIsAddModalVisible] = useState(false)
  const [newVersion, setNewVersion] = useState('default')
  const [newScenario, setNewScenario] = useState('standard')
  const [newNodeType, setNewNodeType] = useState('p')
  const [newHardware, setNewHardware] = useState('A2')
  const [newCommand, setNewCommand] = useState('')

  // 从表单获取初始值
  useEffect(() => {
    const currentValues = form.getFieldValue('startup_commands')
    if (currentValues) {
      setCommands(currentValues)
    }
  }, [])

  // 打开添加配置弹窗
  const handleAdd = () => {
    setNewVersion('default')
    setNewScenario('standard')
    setNewHardware('A2')
    setNewCommand('')
    setIsAddModalVisible(true)
  }

  // 保存新配置
  const handleAddSave = () => {
    if (!newVersion.trim()) {
      message.error('请输入版本名称')
      return
    }
    // PD 分离场景需要拼接节点类型
    const scenarioKey = newScenario.includes('pd-disaggregation')
      ? `pd-disaggregation-${newNodeType}`
      : newScenario

    const newCommands = {
      ...commands,
      [newVersion]: {
        ...(commands[newVersion] || {}),
        [scenarioKey]: {
          ...(commands[newVersion]?.[scenarioKey] || {}),
          [newHardware]: newCommand,
        },
      },
    }
    form.setFieldValue('startup_commands', newCommands)
    setCommands(newCommands)
    setIsAddModalVisible(false)
    message.success('配置已添加')
  }

  // 删除配置
  const handleDelete = (version: string, scenario: string, nodeType: string | undefined, hardware: string) => {
    // PD 分离场景需要重建 scenario key
    const scenarioKey = nodeType ? `pd-disaggregation-${nodeType}` : scenario
    
    const newCommands = { ...commands }
    if (newCommands[version]?.[scenarioKey]?.[hardware]) {
      delete newCommands[version][scenarioKey][hardware]
      if (Object.keys(newCommands[version][scenarioKey]).length === 0) {
        delete newCommands[version][scenarioKey]
      }
      if (Object.keys(newCommands[version]).length === 0) {
        delete newCommands[version]
      }
    }
    form.setFieldValue('startup_commands', newCommands)
    setCommands(newCommands)
  }

  // 保存编辑
  const handleSave = () => {
    if (!editingKey) return
    const [version, scenario, nodeType, hardware] = editingKey.split(':')
    
    // PD 分离场景需要重建 scenario key
    const scenarioKey = nodeType !== 'undefined' && nodeType 
      ? `pd-disaggregation-${nodeType}`
      : scenario
    
    const newCommands = {
      ...commands,
      [version]: {
        ...(commands[version] || {}),
        [scenarioKey]: {
          ...(commands[version]?.[scenarioKey] || {}),
          [hardware]: editingValue,
        },
      },
    }
    form.setFieldValue('startup_commands', newCommands)
    setCommands(newCommands)
    setEditingKey(null)
    message.success('命令已保存')
  }

  // 取消编辑
  const handleCancel = () => {
    setEditingKey(null)
    setEditingValue('')
  }

  // 复制命令
  const handleCopy = (command: string) => {
    navigator.clipboard.writeText(command)
    message.success('命令已复制')
  }

  // 打开 YAML 导入弹窗
  const handleImportYaml = () => {
    setYamlContent('')
    setIsYamlModalVisible(true)
  }

  // 解析并导入 YAML/JSON
  const handleParseAndImport = () => {
    try {
      const parsed = parseYamlOrJsonContent(yamlContent)
      if (parsed) {
        form.setFieldValue('startup_commands', parsed)
        setCommands(parsed)
        setIsYamlModalVisible(false)
        message.success('配置导入成功')
      }
    } catch (error: any) {
      message.error('解析失败：' + error.message)
    }
  }

  // 上传 YAML/JSON 文件
  const handleFileUpload = (file: File) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const content = e.target?.result as string
      setYamlContent(content)
    }
    reader.readAsText(file)
    return false
  }

  // 渲染配置项
  const renderConfigItems = () => {
    const items: any[] = []
    const processedPdScenarios = new Set<string>()
    
    Object.entries(commands).forEach(([version, scenarios]) => {
      // 先收集 PD 分离的 P 和 D 节点（兼容旧格式 pd-disaggregation-p/d）
      const pdNodes: Record<string, Record<string, Record<string, string>>> = {}
      
      Object.entries(scenarios).forEach(([scenario, hardwareConfigs]) => {
        // 兼容新旧格式
        const isPdScenarioOld = scenario === 'pd-disaggregation-p' || scenario === 'pd-disaggregation-d'
        const isPdScenarioNew = scenario.startsWith('pd-disaggregation-') && !isPdScenarioOld
        const isPdScenario = isPdScenarioOld || isPdScenarioNew
        
        if (isPdScenario) {
          let nodeType: string
          if (isPdScenarioOld) {
            // 旧格式：pd-disaggregation-p -> p
            nodeType = scenario.replace('pd-disaggregation-', '')
          } else {
            // 新格式：pd-disaggregation-p -> p
            nodeType = scenario.replace('pd-disaggregation-', '')
          }
          
          const pdKey = `${version}:pd-disaggregation`
          if (!pdNodes[pdKey]) pdNodes[pdKey] = {}
          pdNodes[pdKey][nodeType] = hardwareConfigs as Record<string, string>
        }
      })
      
      // 渲染配置项
      Object.entries(scenarios).forEach(([scenario, hardwareConfigs]) => {
        // 兼容新旧格式
        const isPdScenarioOld = scenario === 'pd-disaggregation-p' || scenario === 'pd-disaggregation-d'
        const isPdScenarioNew = scenario.startsWith('pd-disaggregation-') && !isPdScenarioOld
        const isPdScenario = isPdScenarioOld || isPdScenarioNew
        
        // 跳过已处理的 PD 分离节点
        if (isPdScenario) {
          const pdKey = `${version}:pd-disaggregation`
          if (processedPdScenarios.has(pdKey)) return
          processedPdScenarios.add(pdKey)
          
          // 为 PD 分离创建嵌套条目
          const pdKeyItem = `${version}:pd-disaggregation`
          const pdNodesData = pdNodes[pdKey]
          
          if (pdNodesData) {
            const pdChildren: any[] = []
            Object.entries(pdNodesData).forEach(([nt, hConfigs]) => {
              Object.entries(hConfigs).forEach(([hardware, command]) => {
                const key = `${version}:pd-disaggregation:${nt}:${hardware}`
                pdChildren.push({
                  key,
                  label: (
                    <Space>
                      <Tag color="cyan">{PD_NODE_TYPES.find(n => n.value === nt)?.label || nt}</Tag>
                      <Tag color="orange">{HARDWARE_LABELS[hardware] || hardware}</Tag>
                    </Space>
                  ),
                  children: (
                    <Space direction="vertical" style={{ width: '100%' }} size="small">
                      {command ? (
                        <>
                          <div
                            style={{
                              backgroundColor: '#f5f5f5',
                              padding: 12,
                              borderRadius: 4,
                              fontFamily: 'monospace',
                              fontSize: 12,
                              whiteSpace: 'pre-wrap',
                            }}
                          >
                            {command}
                          </div>
                          <Space>
                            <Button
                              size="small"
                              onClick={() => {
                                setEditingKey(key)
                                setEditingValue(command)
                              }}
                            >
                              编辑
                            </Button>
                            <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(command)}>
                              复制
                            </Button>
                            <Button
                              size="small"
                              danger
                              onClick={() => handleDelete(version, 'pd-disaggregation', nt, hardware)}
                            >
                              删除
                            </Button>
                          </Space>
                        </>
                      ) : (
                        <Text type="secondary">暂无命令配置</Text>
                      )}
                    </Space>
                  ),
                })
              })
            })
            
            items.push({
              key: pdKeyItem,
              label: (
                <Space>
                  <Tag color="blue">{version}</Tag>
                  <Tag color="green">PD 分离</Tag>
                </Space>
              ),
              children: (
                <Collapse
                  items={pdChildren}
                  expandIconPosition="end"
                  ghost
                />
              ),
            })
          }
          return
        }
        
        // 标准场景
        Object.entries(hardwareConfigs).forEach(([hardware, command]) => {
          const key = `${version}:${scenario}:${hardware}`
          const isEditing = editingKey === key

          const scenarioLabel = SCENARIO_LABELS[scenario] || scenario

          items.push({
            key,
            label: (
              <Space>
                <Tag color="blue">{version}</Tag>
                <Tag color="green">{scenarioLabel}</Tag>
                <Tag color="orange">{HARDWARE_LABELS[hardware] || hardware}</Tag>
              </Space>
            ),
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                {isEditing ? (
                  <>
                    <TextArea
                      value={editingValue}
                      onChange={(e) => setEditingValue(e.target.value)}
                      rows={6}
                      style={{ fontFamily: 'monospace', fontSize: 12 }}
                      placeholder="vllm serve Qwen/Qwen3-8B --tensor-parallel-size 2 ..."
                    />
                    <Space>
                      <Button
                        type="primary"
                        size="small"
                        onClick={handleSave}
                      >
                        保存
                      </Button>
                      <Button size="small" onClick={handleCancel}>
                        取消
                      </Button>
                    </Space>
                  </>
                ) : (
                  <>
                    {command ? (
                      <div
                        style={{
                          backgroundColor: '#f5f5f5',
                          padding: 12,
                          borderRadius: 4,
                          fontFamily: 'monospace',
                          fontSize: 12,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {command}
                      </div>
                    ) : (
                      <Text type="secondary">暂无命令配置</Text>
                    )}
                    <Space>
                      <Button
                        size="small"
                        onClick={() => {
                          setEditingKey(key)
                          setEditingValue(command)
                        }}
                      >
                        编辑
                      </Button>
                      <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(command)}>
                        复制
                      </Button>
                      <Button
                        size="small"
                        danger
                        onClick={() => handleDelete(version, scenario, undefined, hardware)}
                      >
                        删除
                      </Button>
                    </Space>
                  </>
                )}
              </Space>
            ),
          })
        })
      })
    })
    
    return items
  }

  return (
    <div>
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Text>配置不同 vLLM 版本、部署场景、硬件的启动命令</Text>
          <Button
            type="link"
            size="small"
            icon={<ImportOutlined />}
            onClick={handleImportYaml}
          >
            导入 YAML
          </Button>
        </Space>
        <Button
          type="dashed"
          size="small"
          icon={<PlusOutlined />}
          onClick={handleAdd}
        >
          添加配置
        </Button>
      </div>
      <Collapse items={renderConfigItems()} expandIconPosition="end" />

      {/* 添加配置弹窗 */}
      <Modal
        title="添加启动命令配置"
        open={isAddModalVisible}
        onCancel={() => setIsAddModalVisible(false)}
        onOk={handleAddSave}
        okText="确定"
        cancelText="取消"
        width={700}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%', marginTop: 16 }}>
          <div>
            <Text strong>vLLM 版本：</Text>
            <Input
              value={newVersion}
              onChange={(e) => setNewVersion(e.target.value)}
              style={{ marginTop: 8 }}
              placeholder="例如：default, v0.16.0, v0.17.0, v0.18.0, v0.19.0"
            />
          </div>
          <div>
            <Text strong>部署场景：</Text>
            <Select
              value={newScenario}
              onChange={setNewScenario}
              showSearch
              style={{ marginTop: 8 }}
              options={[
                ...DEPLOYMENT_SCENARIO_OPTIONS,
                // 如果当前输入的值不在选项中，动态添加为选项
                ...(newScenario && !DEPLOYMENT_SCENARIO_OPTIONS.find(opt => opt.value === newScenario)
                  ? [{ value: newScenario, label: `(自定义) ${newScenario}` }]
                  : []),
              ]}
              placeholder="选择或输入部署场景，如：standard, pd-disaggregation"
              filterOption={(input, option) =>
                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
              onSearch={(value) => setNewScenario(value)}
              allowClear
            />
            <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
              提示：PD 分离场景请选择 <code>pd-disaggregation</code>，然后选择节点类型
            </div>
          </div>
          {(newScenario.includes('pd-disaggregation') || newScenario === 'pd-disaggregation') && (
            <div>
              <Text strong>节点类型：</Text>
              <Select
                value={newNodeType}
                onChange={setNewNodeType}
                style={{ width: '100%', marginTop: 8 }}
                options={PD_NODE_TYPES}
              />
              <div style={{ fontSize: 11, color: '#999', marginTop: 4 }}>
                <ul style={{ margin: '4px 0', paddingLeft: 16 }}>
                  <li>P 节点（Prefill）：负责预填充阶段，处理输入 prompt</li>
                  <li>D 节点（Decode）：负责解码阶段，生成 token</li>
                </ul>
              </div>
            </div>
          )}
          <div>
            <Text strong>部署硬件：</Text>
            <Select
              value={newHardware}
              onChange={setNewHardware}
              style={{ width: '100%', marginTop: 8 }}
              options={HARDWARE_OPTIONS}
            />
          </div>
          <div>
            <Text strong>启动命令：</Text>
            <TextArea
              placeholder="vllm serve Qwen/Qwen3-8B --tensor-parallel-size 2 ..."
              value={newCommand}
              onChange={(e) => setNewCommand(e.target.value)}
              rows={6}
              style={{ fontFamily: 'monospace', fontSize: 12, marginTop: 8 }}
            />
          </div>
        </Space>
      </Modal>

      {/* YAML/JSON 导入弹窗 */}
      <Modal
        title="导入多维度配置（YAML/JSON）"
        open={isYamlModalVisible}
        onCancel={() => setIsYamlModalVisible(false)}
        footer={null}
        width={1000}
        style={{ top: 50 }}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {/* 上传文件区域 */}
          <div>
            <Text strong>上传 YAML/JSON 文件：</Text>
            <Upload.Dragger
              accept=".yaml,.yml,.json"
              multiple={false}
              beforeUpload={handleFileUpload}
              showUploadList={false}
              style={{ marginTop: 8 }}
            >
              <p className="ant-upload-drag-icon">
                <UploadOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽文件到此区域</p>
              <p className="ant-upload-hint">支持 .yaml、.yml、.json 格式</p>
            </Upload.Dragger>
          </div>
          <Divider style={{ margin: '12px 0' }}>或直接粘贴配置内容</Divider>
          <div>
            <Text strong>配置内容：</Text>
            <TextArea
              value={yamlContent}
              onChange={(e) => setYamlContent(e.target.value)}
              rows={10}
              style={{
                fontFamily: 'monospace',
                fontSize: 12,
                marginTop: 8,
                whiteSpace: 'pre-wrap',
              }}
              placeholder="支持 YAML 或 JSON 格式，例如：&#10;&#10;YAML:&#10;default:&#10;  standard:&#10;    A2:&#10;      model_name: &quot;Qwen/Qwen3-8B&quot;&#10;      tensor_parallel_size: 2&#10;&#10;JSON:&#10;{&#10;  &quot;default&quot;: {&#10;    &quot;standard&quot;: {&#10;      &quot;A2&quot;: {&#10;        &quot;model_name&quot;: &quot;Qwen/Qwen3-8B&quot;,&#10;        &quot;tensor_parallel_size&quot;: 2&#10;      }&#10;    }&#10;  }&#10;}"
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
            <Button onClick={() => setIsYamlModalVisible(false)}>
              取消
            </Button>
            <Button
              type="primary"
              onClick={handleParseAndImport}
            >
              导入
            </Button>
          </div>

          <Collapse
            items={[{
              key: 'help',
              label: <strong>📖 配置说明（点击展开）</strong>,
              children: (
                <div style={{ fontSize: 12 }}>
                  <p style={{ marginBottom: 12 }}>
                    支持 <strong>vLLM 版本 × 部署场景 × 硬件</strong> 多维度配置
                    <br />
                    结构：<code>version → scenario → hardware → config</code>
                  </p>

                  <Tabs
                    size="small"
                    items={[
                      {
                        key: 'yaml',
                        label: 'YAML 格式',
                        children: (
                          <pre style={{
                            backgroundColor: '#f5f5f5',
                            padding: 12,
                            borderRadius: 4,
                            fontFamily: 'monospace',
                            fontSize: 11,
                            overflow: 'auto',
                            maxHeight: '300px',
                            whiteSpace: 'pre-wrap',
                            margin: 0,
                          }}>
{`# 标准部署 - Atlas A2
default:
  standard:
    A2:
      model_name: "Qwen/Qwen3-8B"
      tensor_parallel_size: 2
      max_model_len: 8192
      gpu_memory_utilization: 0.9
      dtype: "float16"
      enable_prefix_caching: true
    
    # 标准部署 - Atlas A3（张量并行更大）
    A3:
      model_name: "Qwen/Qwen3-8B"
      tensor_parallel_size: 4
      max_model_len: 16384
      gpu_memory_utilization: 0.95

  # PD 分离部署 - P 节点（Prefill，负责预填充）
  pd-disaggregation:
    p:  # P 节点
      A2:
        model_name: "Qwen/Qwen3-8B"
        tensor_parallel_size: 2
        max_model_len: 8192
        gpu_memory_utilization: 0.9
        prefill_only: true
        
    d:  # D 节点
      A2:
        model_name: "Qwen/Qwen3-8B"
        tensor_parallel_size: 2
        max_model_len: 8192
        gpu_memory_utilization: 0.9
        decode_only: true

# v0.18.0 版本 - 使用 AWQ 量化
v0.18.0:
  standard:
    A2:
      model_name: "Qwen/Qwen3-8B"
      tensor_parallel_size: 2
      quantization: "awq"
      max_model_len: 8192

# v0.18.0 版本 - 标准场景
v0.18.0:
  standard:
    A2:
      model_name: "Qwen/Qwen3-8B"
      tensor_parallel_size: 2
      quantization: "awq"
      max_model_len: 8192`}
                          </pre>
                        ),
                      },
                      {
                        key: 'json',
                        label: 'JSON 格式',
                        children: (
                          <pre style={{
                            backgroundColor: '#f5f5f5',
                            padding: 12,
                            borderRadius: 4,
                            fontFamily: 'monospace',
                            fontSize: 11,
                            overflow: 'auto',
                            maxHeight: '300px',
                            whiteSpace: 'pre-wrap',
                            margin: 0,
                          }}>
{`{
  "default": {
    "standard": {
      "A2": {
        "model_name": "Qwen/Qwen3-8B",
        "tensor_parallel_size": 2,
        "max_model_len": 8192,
        "gpu_memory_utilization": 0.9,
        "dtype": "float16",
        "enable_prefix_caching": true
      },
      "A3": {
        "model_name": "Qwen/Qwen3-8B",
        "tensor_parallel_size": 4,
        "max_model_len": 16384,
        "gpu_memory_utilization": 0.95
      }
    },
    "pd-disaggregation": {
      "p": {
        "A2": {
          "model_name": "Qwen/Qwen3-8B",
          "tensor_parallel_size": 2,
          "max_model_len": 8192,
          "gpu_memory_utilization": 0.9,
          "prefill_only": true
        }
      },
      "d": {
        "A2": {
          "model_name": "Qwen/Qwen3-8B",
          "tensor_parallel_size": 2,
          "max_model_len": 8192,
          "gpu_memory_utilization": 0.9,
          "decode_only": true
        }
      }
    }
  },
  "v0.18.0": {
    "standard": {
      "A2": {
        "model_name": "Qwen/Qwen3-8B",
        "tensor_parallel_size": 2,
        "quantization": "awq",
        "max_model_len": 8192
      }
    }
  }
}`}
                          </pre>
                        ),
                      },
                    ]}
                  />
                  
                  <div style={{ marginTop: 16 }}>
                    <p style={{ fontWeight: 500, marginBottom: 8 }}>参数说明：</p>
                    <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.8 }}>
                      <li><strong>version</strong>：vLLM 版本，如 <code>default</code>、<code>v0.16.0</code>、<code>v0.17.0</code>、<code>v0.18.0</code>、<code>v0.19.0</code></li>
                      <li><strong>scenario</strong>：部署场景
                        <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                          <li><code>standard</code> - 标准部署</li>
                          <li><code>pd-disaggregation</code> - PD 分离
                            <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                              <li><code>p</code> - P 节点（Prefill，负责预填充）</li>
                              <li><code>d</code> - D 节点（Decode，负责解码）</li>
                            </ul>
                          </li>
                        </ul>
                      </li>
                      <li><strong>hardware</strong>：硬件类型
                        <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                          <li><code>A2</code> - Atlas A2</li>
                          <li><code>A3</code> - Atlas A3</li>
                          <li><code>A5</code> - Atlas A5</li>
                          <li><code>310P</code> - Ascend 310P</li>
                        </ul>
                      </li>
                      <li><strong>config</strong>：vLLM 配置参数
                        <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                          <li><code>model_name</code>：模型名称（必填）</li>
                          <li><code>tensor_parallel_size</code>：张量并行数</li>
                          <li><code>max_model_len</code>：最大模型长度</li>
                          <li><code>gpu_memory_utilization</code>：GPU 内存利用率（0-1）</li>
                          <li><code>dtype</code>：数据类型，如 <code>float16</code>、<code>bfloat16</code></li>
                          <li><code>quantization</code>：量化方法，如 <code>awq</code>、<code>gptq</code></li>
                          <li><code>enable_prefix_caching</code>：启用前缀缓存（true/false）</li>
                        </ul>
                      </li>
                    </ul>
                  </div>
                </div>
              ),
            }]}
            style={{ marginTop: 8 }}
          />
        </Space>
      </Modal>
    </div>
  )
}

/**
 * 解析 YAML 或 JSON 内容为多维度启动命令配置
 * 支持结构：
 *   - 标准：version -> scenario -> hardware -> config
 *   - PD 分离：version -> scenario -> node_type -> hardware -> config
 */
function parseYamlOrJsonContent(content: string): StartupCommandConfig {
  const result: StartupCommandConfig = {}

  // 尝试解析 JSON
  try {
    const parsed = JSON.parse(content)
    if (typeof parsed === 'object' && parsed !== null) {
      return parseConfigObject(parsed)
    }
  } catch (e) {
    // 不是 JSON，继续尝试 YAML 解析
  }

  try {
    // 尝试使用 js-yaml 库（如果可用）
    const yaml = (window as any).jsyaml
    if (yaml) {
      const parsed = yaml.load(content)
      if (typeof parsed === 'object' && parsed !== null) {
        return parseConfigObject(parsed)
      }
    }
  } catch (e) {
    console.error('js-yaml 解析失败:', e)
  }

  // 简单解析：嵌套 YAML 结构（支持四维）
  const lines = content.split('\n')
  let currentVersion = ''
  let currentScenario = ''
  let currentNodeType = ''
  let currentHardware = ''
  let currentConfig: Record<string, any> = {}

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue

    const indentLevel = line.search(/\S/)

    // 版本键（无缩进）
    if (indentLevel === 0 && trimmed.includes(':')) {
      // 保存之前的配置
      saveCurrentConfig()
      currentVersion = trimmed.split(':')[0].trim()
      currentScenario = ''
      currentNodeType = ''
      currentHardware = ''
      currentConfig = {}
    }
    // 场景键（2 格缩进）
    else if (indentLevel === 2 && trimmed.includes(':')) {
      saveCurrentConfig()
      currentScenario = trimmed.split(':')[0].trim()
      currentNodeType = ''
      currentHardware = ''
      currentConfig = {}
    }
    // 节点类型键（4 格缩进，仅 PD 分离场景）
    else if (indentLevel === 4 && trimmed.includes(':') && currentScenario === 'pd-disaggregation') {
      saveCurrentConfig()
      currentNodeType = trimmed.split(':')[0].trim()
      currentHardware = ''
      currentConfig = {}
    }
    // 硬件键（4 格缩进，标准场景 或 6 格缩进 PD 分离）
    else if (indentLevel === 4 && trimmed.includes(':') && currentScenario !== 'pd-disaggregation') {
      saveCurrentConfig()
      currentHardware = trimmed.split(':')[0].trim()
      currentConfig = {}
    }
    else if (indentLevel === 6 && trimmed.includes(':') && currentScenario === 'pd-disaggregation' && currentNodeType) {
      saveCurrentConfig()
      currentHardware = trimmed.split(':')[0].trim()
      currentConfig = {}
    }
    // 配置项（6 格或更多缩进）
    else if (indentLevel >= 6 && trimmed.includes(':') && currentHardware) {
      parseConfigLine(trimmed, currentConfig)
    }
  }

  // 保存最后一个配置
  saveCurrentConfig()

  function saveCurrentConfig() {
    if (currentVersion && currentScenario && currentHardware && Object.keys(currentConfig).length > 0) {
      if (!result[currentVersion]) result[currentVersion] = {}
      if (currentScenario === 'pd-disaggregation' && currentNodeType) {
        // PD 分离：version -> scenario -> node_type -> hardware
        const scenarioObj = result[currentVersion][currentScenario] || {}
        result[currentVersion][currentScenario] = scenarioObj
        const nodeTypeObj = (scenarioObj as any)[currentNodeType] || {}
        ;(scenarioObj as any)[currentNodeType] = nodeTypeObj
        nodeTypeObj[currentHardware] = configToCommand(currentConfig)
      } else {
        // 标准：version -> scenario -> hardware
        if (!result[currentVersion][currentScenario]) result[currentVersion][currentScenario] = {}
        result[currentVersion][currentScenario][currentHardware] = configToCommand(currentConfig)
      }
    }
  }

  function parseConfigLine(trimmed: string, config: Record<string, any>) {
    const [key, ...valueParts] = trimmed.split(':')
    let value = valueParts.join(':').trim()
    value = value.replace(/^["']|["']$/g, '')
    if (value === 'true') {
      config[key.trim()] = true
    } else if (value === 'false') {
      config[key.trim()] = false
    } else if (/^\d+$/.test(value)) {
      config[key.trim()] = parseInt(value, 10)
    } else if (/^\d+\.\d+$/.test(value)) {
      config[key.trim()] = parseFloat(value)
    } else {
      config[key.trim()] = value
    }
  }

  return result
}

/**
 * 解析配置对象（从 JSON 或 YAML 解析后的对象）
 * 支持四维结构：version -> scenario -> node_type -> hardware -> config
 */
function parseConfigObject(parsed: any): StartupCommandConfig {
  const result: StartupCommandConfig = {}
  
  for (const [version, scenarios] of Object.entries(parsed)) {
    if (typeof scenarios === 'object' && scenarios !== null) {
      result[version] = {}
      for (const [scenario, nodeTypesOrHardware] of Object.entries(scenarios)) {
        if (typeof nodeTypesOrHardware === 'object' && nodeTypesOrHardware !== null) {
          // 检查是否是 PD 分离场景（有 p/d 节点类型）
          const hasNodeTypes = Object.keys(nodeTypesOrHardware).some(k => k === 'p' || k === 'd')
          
          if (hasNodeTypes && scenario === 'pd-disaggregation') {
            // PD 分离场景：version -> scenario -> node_type -> hardware
            result[version][scenario] = {}
            for (const [nodeType, hardwareConfigs] of Object.entries(nodeTypesOrHardware)) {
              if (typeof hardwareConfigs === 'object' && hardwareConfigs !== null) {
                const scenarioKey = `pd-disaggregation-${nodeType}`
                result[version][scenarioKey] = {}
                for (const [hardware, config] of Object.entries(hardwareConfigs)) {
                  if (typeof config === 'object' && config !== null) {
                    result[version][scenarioKey][hardware] = configToCommand(config as Record<string, any>)
                  } else if (typeof config === 'string') {
                    result[version][scenarioKey][hardware] = config
                  }
                }
              }
            }
          } else {
            // 标准场景：version -> scenario -> hardware
            result[version][scenario] = {}
            for (const [hardware, config] of Object.entries(nodeTypesOrHardware)) {
              if (typeof config === 'object' && config !== null) {
                result[version][scenario][hardware] = configToCommand(config as Record<string, any>)
              } else if (typeof config === 'string') {
                result[version][scenario][hardware] = config
              }
            }
          }
        }
      }
    }
  }
  
  return result
}

/**
 * 将配置字典转换为命令字符串
 */
function configToCommand(config: Record<string, any>): string {
  const parts: string[] = ['vllm serve']

  // 参数映射
  const paramMapping: Record<string, string> = {
    model_name: '--model',
    tensor_parallel_size: '--tensor-parallel-size',
    pipeline_parallel_size: '--pipeline-parallel-size',
    max_model_len: '--max-model-len',
    gpu_memory_utilization: '--gpu-memory-utilization',
    dtype: '--dtype',
    quantization: '--quantization',
    enable_prefix_caching: '--enable-prefix-caching',
    trust_remote_code: '--trust-remote-code',
    enforce_eager: '--enforce-eager',
    enable_chunked_prefill: '--enable-chunked-prefill',
  }

  const booleanParams = ['enable_prefix_caching', 'trust_remote_code', 'enforce_eager', 'enable_chunked_prefill']

  for (const [key, value] of Object.entries(config)) {
    const param = paramMapping[key]
    if (!param) continue

    if (booleanParams.includes(key)) {
      if (value) {
        parts.push(param)
      }
    } else if (typeof value === 'string') {
      parts.push(`${param} "${value}"`)
    } else if (typeof value === 'number') {
      parts.push(`${param} ${value}`)
    } else if (typeof value === 'boolean') {
      if (value) {
        parts.push(param)
      }
    }
  }

  return parts.join(' \\\n  ')
}

function ModelBoardConfig() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState('models')

  // 模型同步配置状态
  const [isSyncModalVisible, setIsSyncModalVisible] = useState(false)
  const [editingSyncConfig, setEditingSyncConfig] = useState<ModelSyncConfig | null>(null)
  const [syncForm] = Form.useForm()

  // 同步配置状态
  const [isGlobalSyncConfigModalOpen, setIsGlobalSyncConfigModalOpen] = useState(false)
  const [globalSyncConfigForm] = Form.useForm()
  const [globalSyncConfig, setGlobalSyncConfig] = useState<{
    sync_interval_minutes: number
    days_back: number
  } | null>(null)

  // 模型管理状态
  const [isModelModalVisible, setIsModelModalVisible] = useState(false)
  const [editingModel, setEditingModel] = useState<ModelConfig | null>(null)
  const [modelForm] = Form.useForm()
  const [searchText, setSearchText] = useState('')
  // 收集已使用的系列选项，用于下拉选择
  const [existingSeries, setExistingSeries] = useState<string[]>([])
  // 用于强制刷新 StartupCommandsEditor 组件
  const [editorKey, setEditorKey] = useState(0)
  // 模型详情弹窗状态
  const [viewingModel, setViewingModel] = useState<ModelConfig | null>(null)
  const [isModelDetailModalVisible, setIsModelDetailModalVisible] = useState(false)

  // 报告管理状态
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null)
  const [isUploadModalVisible, setIsUploadModalVisible] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [reportContent, setReportContent] = useState('')
  const [deletingReportId, setDeletingReportId] = useState<number | null>(null)

  // ============ 模型同步配置 ============

  // 获取同步配置列表
  const { data: configs = [], isLoading: configsLoading } = useQuery({
    queryKey: ['model-sync-configs'],
    queryFn: () => getSyncConfigs(),
  })

  // 获取同步配置（从系统配置中获取）
  const { data: systemConfig } = useSystemConfig()

  // 当系统配置加载完成后，更新同步配置
  useEffect(() => {
    if (systemConfig?.sync_config?.model_sync_config) {
      setGlobalSyncConfig({
        sync_interval_minutes: systemConfig.sync_config.model_sync_config.sync_interval_minutes,
        days_back: systemConfig.sync_config.model_sync_config.days_back,
      })
    }
  }, [systemConfig])

  // 更新同步配置
  const updateGlobalSyncConfigMutation = useMutation({
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
      const res = await fetch(`/api/v1/system/sync?${params}`, {
        method: 'PUT',
      })
      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.detail || '更新失败')
      }
      return res.json()
    },
    onSuccess: () => {
      message.success('同步配置已更新')
      setIsGlobalSyncConfigModalOpen(false)
      queryClient.invalidateQueries({ queryKey: ['system-config'] })
    },
    onError: (error: any) => {
      message.error(error.message || '更新失败')
    },
  })

  // 删除同步配置
  const deleteSyncMutation = useMutation({
    mutationFn: deleteSyncConfig,
    onSuccess: () => {
      message.success('配置已删除')
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '删除失败')
    },
  })

  // 创建/更新同步配置
  const createSyncMutation = useMutation({
    mutationFn: createSyncConfig,
    onSuccess: () => {
      message.success('配置已创建')
      setIsSyncModalVisible(false)
      syncForm.resetFields()
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '创建失败')
    },
  })

  const updateSyncMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: ModelSyncConfigCreate }) =>
      updateSyncConfig(id, data),
    onSuccess: () => {
      message.success('配置已更新')
      setIsSyncModalVisible(false)
      syncForm.resetFields()
      setEditingSyncConfig(null)
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '更新失败')
    },
  })

  // 触发同步配置同步
  const syncConfigMutation = useMutation({
    mutationFn: triggerSync,
    onSuccess: (data, _configId) => {
      message.success(data.message || '同步已触发')
      queryClient.invalidateQueries({ queryKey: ['model-sync-configs'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '同步失败')
    },
  })

  // 处理同步配置创建/更新
  const handleSyncFinish = (values: any) => {
    const data: ModelSyncConfigCreate = {
      workflow_name: values.workflow_name,
      workflow_file: values.workflow_file,
      artifacts_pattern: values.artifacts_pattern,
      file_patterns: values.file_patterns?.split('\n').filter((l: string) => l.trim()),
      enabled: values.enabled,
      branch: values.branch,
    }

    if (editingSyncConfig) {
      updateSyncMutation.mutate({ id: editingSyncConfig.id, data })
    } else {
      createSyncMutation.mutate(data)
    }
  }

  // 打开同步配置弹窗
  const openSyncModal = (config?: ModelSyncConfig) => {
    if (config) {
      setEditingSyncConfig(config)
      syncForm.setFieldsValue({
        workflow_name: config.workflow_name,
        workflow_file: config.workflow_file,
        artifacts_pattern: config.artifacts_pattern,
        file_patterns: config.file_patterns?.join('\n'),
        enabled: config.enabled,
        branch: config.branch || 'main',
      })
    } else {
      setEditingSyncConfig(null)
      syncForm.resetFields()
    }
    setIsSyncModalVisible(true)
  }

  // 打开同步配置编辑弹窗
  const handleOpenGlobalSyncConfig = () => {
    globalSyncConfigForm.setFieldsValue({
      model_sync_interval_minutes: globalSyncConfig?.sync_interval_minutes || 60,
      model_sync_days_back: globalSyncConfig?.days_back || 3,
    })
    setIsGlobalSyncConfigModalOpen(true)
  }

  // 更新同步配置
  const handleUpdateGlobalSyncConfig = (values: any) => {
    updateGlobalSyncConfigMutation.mutate({
      model_sync_interval_minutes: values.model_sync_interval_minutes,
      model_sync_days_back: values.model_sync_days_back,
    })
  }

  // ============ 模型管理 ============

  // 获取模型列表
  const { data: models = [], isLoading: modelsLoading } = useQuery({
    queryKey: ['admin-models', { search: searchText }],
    queryFn: () => getModels({ search: searchText || undefined }),
  })

  // 当模型列表变化时，更新已有系列选项
  useEffect(() => {
    const seriesSet = new Set(models.map((m: ModelConfig) => m.series).filter(Boolean))
    setExistingSeries(Array.from(seriesSet) as string[])
  }, [models])

  // 删除模型
  const deleteModelMutation = useMutation({
    mutationFn: deleteModel,
    onSuccess: () => {
      message.success('模型已删除')
      queryClient.invalidateQueries({ queryKey: ['admin-models'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '删除失败')
    },
  })

  // 创建/更新模型
  const createModelMutation = useMutation({
    mutationFn: createModel,
    onSuccess: () => {
      message.success('模型已创建')
      setIsModelModalVisible(false)
      modelForm.resetFields()
      queryClient.invalidateQueries({ queryKey: ['admin-models'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '创建失败')
    },
  })

  const updateModelMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<ModelConfig> }) =>
      updateModel(id, data),
    onSuccess: () => {
      message.success('模型已更新')
      setIsModelModalVisible(false)
      modelForm.resetFields()
      setEditingModel(null)
      queryClient.invalidateQueries({ queryKey: ['admin-models'] })
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '更新失败')
    },
  })

  // 处理模型创建/更新
  const handleModelFinish = (values: any) => {
    const data = { ...values }
    // 将 series 数组转换为字符串（取第一个值）
    if (Array.isArray(data.series) && data.series.length > 0) {
      data.series = data.series[0]
    }
    // 直接从表单获取 startup_commands，确保是最新值
    data.startup_commands = modelForm.getFieldValue('startup_commands')

    if (editingModel) {
      updateModelMutation.mutate({ id: editingModel.id, data })
    } else {
      createModelMutation.mutate(data)
    }
  }

  // 打开模型弹窗
  const openModelModal = (model?: ModelConfig) => {
    if (model) {
      setEditingModel(model)
      const startupCommands = model.startup_commands || {}
      modelForm.setFieldsValue({
        model_name: model.model_name,
        series: model.series ? [model.series] : undefined,
        status: model.status,
        official_doc_url: model.official_doc_url,
        startup_commands: startupCommands,
      })
      setEditorKey(k => k + 1)  // 强制刷新组件
    } else {
      setEditingModel(null)
      modelForm.resetFields()
      modelForm.setFieldValue('series', [])
      modelForm.setFieldValue('startup_commands', {})
      setEditorKey(k => k + 1)  // 强制刷新组件
    }
    setIsModelModalVisible(true)
  }

  // ============ 报告管理 ============

  // 报告列表状态
  const [reportListModelId, setReportListModelId] = useState<number | null>(null)
  const [viewingReport, setViewingReport] = useState<ModelReport | null>(null)
  const [isDetailModalVisible, setIsDetailModalVisible] = useState(false)
  const [editingReport, setEditingReport] = useState<ModelReport | null>(null)
  const [isEditModalVisible, setIsEditModalVisible] = useState(false)
  
  // 报告筛选状态
  const [filterDtype, setFilterDtype] = useState<string | null>(null)
  const [filterFeatures, setFilterFeatures] = useState<string[]>([])

  // 获取报告列表
  const { data: reports = [], refetch: refetchReports } = useQuery({
    queryKey: ['admin-model-reports', reportListModelId],
    queryFn: () => reportListModelId ? getModelReports(reportListModelId) : Promise.resolve([]),
    enabled: !!reportListModelId,
  })
  
  // 计算所有唯一的 features
  const uniqueFeatures = Array.from(
    new Set(
      reports
        .flatMap(r => r.features || [])
        .filter(Boolean)
    )
  )
  
  // 应用筛选器到报告列表
  const filteredReports = reports.filter(report => {
    // dtype 筛选
    if (filterDtype && report.dtype !== filterDtype) {
      return false
    }
    // features 筛选（报告包含所有选中的 features）
    if (filterFeatures.length > 0) {
      const reportFeatures = report.features || []
      if (!filterFeatures.every(f => reportFeatures.includes(f))) {
        return false
      }
    }
    return true
  })

  // 上传报告
  const uploadReportMutation = useMutation({
    mutationFn: ({ modelId, file }: { modelId: number; file: File }) =>
      uploadReport(modelId, file),
    onMutate: () => {
      setUploading(true)
    },
    onSuccess: () => {
      message.success('报告上传成功')
      setIsUploadModalVisible(false)
      if (reportListModelId) {
        refetchReports()
      }
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '上传失败')
    },
    onSettled: () => {
      setUploading(false)
    },
  })

  // 同步报告
  const reportSyncMutation = useMutation({
    mutationFn: (modelId: number) => syncReports(modelId),
    onMutate: () => {
      setSyncing(true)
    },
    onSuccess: (data) => {
      message.success(data.message || '同步已触发，请稍后刷新查看结果')
      if (reportListModelId) {
        refetchReports()
      }
    },
    onError: (error: any) => {
      message.error(error.response?.data?.detail || '同步失败')
    },
    onSettled: () => {
      setSyncing(false)
    },
  })

  // 删除报告
  const deleteReportMutation = useMutation({
    mutationFn: async ({ modelId, reportId }: { modelId: number; reportId: number }) => {
      const result = await deleteReport(modelId, reportId)
      return result
    },
    onMutate: ({ reportId }) => {
      setDeletingReportId(reportId)
    },
    onSuccess: (_data) => {
      message.success('报告已删除')
      setDeletingReportId(null)
      if (reportListModelId) {
        refetchReports()
      }
    },
    onError: (error: any) => {
      console.error('删除失败:', error)
      message.error(error.response?.data?.detail || '删除失败')
      setDeletingReportId(null)
    },
  })

  // 报告列表刷新回调
  const handleReportUpdated = () => {
    if (reportListModelId) {
      refetchReports()
    }
  }

  // 上传文件处理
  const handleUpload = (file: File) => {
    if (selectedModelId) {
      uploadReportMutation.mutate({ modelId: selectedModelId, file })
    }
    return false
  }

  // 手动提交报告内容
  const handleSubmitReportContent = () => {
    if (!reportContent.trim()) {
      message.error('请输入报告内容')
      return
    }
    if (!selectedModelId) {
      message.error('请选择模型')
      return
    }

    // 创建 Blob 文件
    const blob = new Blob([reportContent], {
      type: 'application/json',
    })
    const file = new File([blob], 'report.json', {
      type: blob.type,
    })

    uploadReportMutation.mutate({ modelId: selectedModelId, file })
  }

  // ============ 同步配置表格列 ============

  const syncColumns: ColumnsType<ModelSyncConfig> = [
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
      title: '文件模式',
      key: 'file_patterns',
      width: 150,
      render: (record: ModelSyncConfig) => {
        if (!record.file_patterns || record.file_patterns.length === 0) return '-'
        return (
          <Space direction="vertical" size={0}>
            {record.file_patterns.slice(0, 3).map((p, i) => (
              <Text key={i} code style={{ fontSize: 12 }}>
                {p}
              </Text>
            ))}
            {record.file_patterns.length > 3 && (
              <Text type="secondary">+{record.file_patterns.length - 3} more</Text>
            )}
          </Space>
        )
      },
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
            onClick={() => syncConfigMutation.mutate(record.id)}
            loading={syncConfigMutation.isPending && syncConfigMutation.variables === record.id}
          >
            同步
          </Button>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => openSyncModal(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定要删除此配置吗？"
            onConfirm={() => deleteSyncMutation.mutate(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // ============ 模型管理表格列 ============

  const modelColumns: ColumnsType<ModelConfig> = [
    {
      title: '模型名称',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 300,
      sorter: (a, b) => a.model_name.localeCompare(b.model_name),
      render: (text: string, record: ModelConfig) => (
        <Space direction="vertical" size={0}>
          <Text strong>{text}</Text>
          {record.series && (
            <Tag color="blue">{record.series}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={status === 'active' ? 'green' : 'default'}>
          {status === 'active' ? '活跃' : '未激活'}
        </Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (date: string) => dayjs(date).add(8, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      fixed: 'right',
      render: (_: any, record: ModelConfig) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={(e) => {
              e.stopPropagation()
              openModelModal(record)
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定要删除此模型吗？"
            onConfirm={(e) => {
              e?.stopPropagation()
              deleteModelMutation.mutate(record.id)
            }}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={(e) => e.stopPropagation()}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      {/* 页面标题 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0 }}>
          模型看板配置
        </Title>
        <Text type="secondary">
          配置模型看板相关规则
        </Text>
      </div>

      {/* 配置选项卡 */}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        className="stripe-page-tabs"
        items={[
          {
            key: 'models',
            label: (
              <Space>
                <DatabaseOutlined />
                <span>模型管理</span>
              </Space>
            ),
            children: (
              <div>
                {/* 操作栏 */}
                <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Space>
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={() => openModelModal()}
                    >
                      创建模型
                    </Button>
                  </Space>
                  <Input
                    placeholder="搜索模型名称"
                    style={{ width: 250 }}
                    value={searchText}
                    onChange={(e) => setSearchText(e.target.value)}
                    allowClear
                  />
                </div>

                {/* 模型列表 */}
                <Card>
                  <Table
                    columns={modelColumns}
                    dataSource={models}
                    loading={modelsLoading}
                    rowKey="id"
                    pagination={{
                      pageSize: 20,
                      showSizeChanger: false,
                    }}
                    scroll={{ x: 1000 }}
                    onRow={(record) => ({
                      onClick: () => {
                        setViewingModel(record)
                        setIsModelDetailModalVisible(true)
                      },
                      style: { cursor: 'pointer' },
                    })}
                  />
                </Card>
              </div>
            ),
          },
          {
            key: 'reports',
            label: (
              <Space>
                <UploadOutlined />
                <span>报告管理</span>
              </Space>
            ),
            children: (
              <div>
                {/* 操作栏 */}
                <div style={{ marginBottom: 16 }}>
                  <Space>
                    <Select
                      placeholder="选择模型"
                      style={{ width: 300 }}
                      value={reportListModelId}
                      onChange={(value) => {
                        setReportListModelId(value)
                        setSelectedModelId(value)
                      }}
                      options={models.map((m) => ({
                        label: m.model_name,
                        value: m.id,
                      }))}
                      showSearch
                      optionFilterProp="label"
                    />
                    <Button
                      type="primary"
                      icon={<UploadOutlined />}
                      onClick={() => setIsUploadModalVisible(true)}
                      disabled={!selectedModelId}
                      loading={uploading}
                    >
                      上传报告
                    </Button>
                    <Button
                      icon={<SyncOutlined />}
                      onClick={() => selectedModelId && reportSyncMutation.mutate(selectedModelId)}
                      loading={reportSyncMutation.isPending}
                      disabled={!selectedModelId}
                    >
                      同步报告
                    </Button>
                  </Space>
                </div>

                {/* 报告列表 */}
                {reportListModelId && (
                  <Card>
                    {/* 筛选器 */}
                    <Space wrap style={{ marginBottom: 16 }}>
                      <Select
                        placeholder="权重类型"
                        allowClear
                        style={{ width: 120 }}
                        options={[
                          { label: 'w8a8', value: 'w8a8' },
                          { label: 'fp16', value: 'fp16' },
                          { label: 'bf16', value: 'bf16' },
                        ]}
                        onChange={(value) => setFilterDtype(value)}
                        value={filterDtype || undefined}
                      />
                      <Select
                        placeholder="特性"
                        allowClear
                        mode="multiple"
                        style={{ width: 200 }}
                        options={uniqueFeatures.map(f => ({ label: f, value: f }))}
                        onChange={(values) => setFilterFeatures(values)}
                        value={filterFeatures.length > 0 ? filterFeatures : undefined}
                      />
                      <Button onClick={() => {
                        setFilterDtype(null)
                        setFilterFeatures([])
                      }}>
                        重置筛选
                      </Button>
                    </Space>

                    <Table
                      columns={[
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
                            <Tag color={record.pass_fail === 'pass' ? 'green' : 'red'}>
                              {record.pass_fail === 'pass' ? '通过' : '未通过'}
                            </Tag>
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
                        {
                          title: '权重类型',
                          dataIndex: 'dtype',
                          key: 'dtype',
                          width: 100,
                          render: (dtype?: string) => dtype ? <Tag color="blue">{dtype}</Tag> : '-',
                        },
                        {
                          title: '特性',
                          dataIndex: 'features',
                          key: 'features',
                          width: 200,
                          render: (features?: string[]) => {
                            if (!features || features.length === 0) return '-'
                            return (
                              <Space wrap>
                                {features.slice(0, 3).map((f, i) => (
                                  <Tag key={i} color="cyan">{f}</Tag>
                                ))}
                                {features.length > 3 && (
                                  <Tag>+{features.length - 3}</Tag>
                                )}
                              </Space>
                            )
                          },
                        },
                        {
                          title: '操作',
                          key: 'action',
                          width: 150,
                          fixed: 'right',
                          render: (_: any, record: ModelReport) => (
                            <Space>
                              <Button
                                type="link"
                                size="small"
                                icon={<EditOutlined />}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setEditingReport(record)
                                  setIsEditModalVisible(true)
                                }}
                              >
                                编辑
                              </Button>
                              <Button
                                type="link"
                                size="small"
                                danger
                                icon={<DeleteOutlined />}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  e.preventDefault()
                                  if (!reportListModelId) {
                                    message.error('未选择模型')
                                    return
                                  }
                                  Modal.confirm({
                                    title: '确定要删除此报告吗？',
                                    okText: '确定',
                                    cancelText: '取消',
                                    onOk: () => {
                                      deleteReportMutation.mutate({
                                        modelId: reportListModelId,
                                        reportId: record.id
                                      })
                                    },
                                  })
                                }}
                                loading={deleteReportMutation.isPending}
                                disabled={deleteReportMutation.isPending}
                              >
                                删除
                              </Button>
                            </Space>
                          ),
                        },
                      ]}
                      dataSource={filteredReports}
                      loading={!reports}
                      rowKey="id"
                      pagination={{ pageSize: 20 }}
                      scroll={{ x: 1000 }}
                      onRow={(record) => ({
                        onClick: () => {
                          // 删除过程中不打开详情弹窗
                          if (deletingReportId === record.id) return
                          setViewingReport(record)
                          setIsDetailModalVisible(true)
                        },
                        style: { cursor: 'pointer' },
                      })}
                    />
                  </Card>
                )}

                {/* 使用说明 */}
                {!reportListModelId && (
                  <Card>
                    <Alert
                      message="使用说明"
                      description={
                        <div>
                          <Text type="secondary">
                            <ol style={{ margin: '8px 0 0 0', paddingLeft: '20px' }}>
                              <li>从下拉列表选择要管理的模型</li>
                              <li>点击"上传报告"手动上传 JSON 格式的报告</li>
                              <li>点击"同步报告"从 GitHub Actions 自动同步最新报告</li>
                              <li>使用编辑/删除按钮管理已有报告</li>
                            </ol>
                          </Text>
                        </div>
                      }
                      type="info"
                      showIcon
                    />
                  </Card>
                )}
              </div>
            ),
          },
        ]}
      />

      {/* 同步配置弹窗 */}
      <Modal
        title={editingSyncConfig ? '编辑模型同步配置' : '创建模型同步配置'}
        open={isSyncModalVisible}
        onCancel={() => {
          setIsSyncModalVisible(false)
          syncForm.resetFields()
          setEditingSyncConfig(null)
        }}
        footer={null}
        width={700}
      >
        <Form
          form={syncForm}
          layout="vertical"
          onFinish={handleSyncFinish}
        >
          <Form.Item
            name="workflow_name"
            label="Workflow 名称"
            rules={[{ required: true, message: '请输入 Workflow 名称' }]}
          >
            <Input placeholder="例如：Model Validation" />
          </Form.Item>

          <Form.Item
            name="workflow_file"
            label="Workflow 文件名"
            rules={[{ required: true, message: '请输入 Workflow 文件名' }]}
            tooltip="GitHub workflow 文件路径，如 .github/workflows/schedule_model_validation.yaml"
          >
            <Input placeholder="schedule_model_validation.yaml" />
          </Form.Item>

          <Form.Item
            name="artifacts_pattern"
            label="Artifacts 匹配规则"
            tooltip="用于匹配 artifacts 的通配符模式，如 model-report-*"
          >
            <Input placeholder="model-report-*" />
          </Form.Item>

          <Form.Item
            name="file_patterns"
            label="文件路径模式"
            tooltip="需要下载的文件路径模式，每行一个，支持通配符"
          >
            <TextArea
              rows={4}
              placeholder="results/*.yaml&#10;lm_eval_results/*.json"
              style={{ fontFamily: 'monospace' }}
            />
          </Form.Item>

          <Form.Item
            name="branch"
            label="分支过滤"
            initialValue="main"
            tooltip="只同步指定分支的 workflow runs，默认 main"
          >
            <Input placeholder="main, zxy_fix_ci 等" style={{ width: '100%' }} />
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
                setIsSyncModalVisible(false)
                syncForm.resetFields()
                setEditingSyncConfig(null)
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createSyncMutation.isPending || updateSyncMutation.isPending}
            >
              {editingSyncConfig ? '更新' : '创建'}
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* 同步配置弹窗 */}
      <Modal
        title="编辑同步配置"
        open={isGlobalSyncConfigModalOpen}
        onCancel={() => {
          setIsGlobalSyncConfigModalOpen(false)
          globalSyncConfigForm.resetFields()
        }}
        footer={null}
        width={600}
      >
        <Alert
          message="提示"
          description="模型同步使用全局配置，所有启用的 workflow 将使用相同的同步间隔和策略"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Form
          form={globalSyncConfigForm}
          layout="vertical"
          onFinish={handleUpdateGlobalSyncConfig}
        >
          <Form.Item
            name="model_sync_interval_minutes"
            label="模型同步间隔（分钟）"
            rules={[{ required: true, message: '请输入同步间隔' }]}
            extra="模型报告自动同步的时间间隔，范围 1-10080 分钟"
          >
            <InputNumber min={1} max={10080} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="model_sync_days_back"
            label="模型同步天数范围（天）"
            rules={[{ required: true, message: '请输入同步天数' }]}
            extra="自动同步时采集最近 N 天的数据，范围 1-90 天"
          >
            <InputNumber min={1} max={90} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 24 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsGlobalSyncConfigModalOpen(false)
                globalSyncConfigForm.resetFields()
              }}>
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                loading={updateGlobalSyncConfigMutation.isPending}
              >
                保存
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 模型管理弹窗 */}
      <Modal
        title={editingModel ? '编辑模型' : '创建模型'}
        open={isModelModalVisible}
        onCancel={() => {
          setIsModelModalVisible(false)
          modelForm.resetFields()
          setEditingModel(null)
        }}
        footer={null}
        width={900}
      >
        <Form
          form={modelForm}
          layout="vertical"
          onFinish={handleModelFinish}
        >
          <Form.Item
            name="model_name"
            label="模型名称"
            rules={[{ required: true, message: '请输入模型名称' }]}
          >
            <Input placeholder="例如：Qwen/Qwen3-8B" />
          </Form.Item>

          <Form.Item
            name="series"
            label="模型系列"
          >
            <Select 
              placeholder="选择或输入模型系列" 
              allowClear
              showSearch
              mode="tags"
              maxCount={1}
              options={existingSeries.map((s) => ({ label: s, value: s }))}
              filterOption={(input, option) =>
                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
              onChange={(value: string[]) => {
                // 只保留第一个值
                if (value.length > 1) {
                  modelForm.setFieldValue('series', [value[value.length - 1]])
                }
              }}
            />
          </Form.Item>

          <Form.Item
            name="status"
            label="状态"
            initialValue="active"
          >
            <Select>
              <Select.Option value="active">活跃</Select.Option>
              <Select.Option value="inactive">未激活</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item
            name="official_doc_url"
            label="官方文档链接"
          >
            <Input placeholder="例如：https://huggingface.co/Qwen/Qwen3-8B" />
          </Form.Item>

          <Form.Item label="启动命令配置">
            <StartupCommandsEditor 
              key={editorKey}
              form={modelForm}
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Button
              style={{ marginRight: 8 }}
              onClick={() => {
                setIsModelModalVisible(false)
                modelForm.resetFields()
                setEditingModel(null)
              }}
            >
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createModelMutation.isPending || updateModelMutation.isPending}
            >
              {editingModel ? '更新' : '创建'}
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* 报告上传弹窗 */}
      <Modal
        title="上传模型报告"
        open={isUploadModalVisible}
        onCancel={() => {
          setIsUploadModalVisible(false)
          setReportContent('')
        }}
        footer={null}
        width={800}
      >
        <div style={{ padding: '20px 0' }}>
          <Alert
            message="支持格式"
            description="请上传 JSON 格式的模型报告文件，或在下方直接粘贴报告内容。vLLM 版本、硬件类型等信息将从报告内容中自动提取。"
            type="info"
            showIcon
            style={{ marginBottom: 20 }}
          />

          {/* 选择模型 */}
          <div style={{ marginBottom: 16 }}>
            <Text strong>选择模型：</Text>
            <Select
              placeholder="请选择要上传报告的模型"
              value={selectedModelId}
              onChange={(value) => setSelectedModelId(value)}
              style={{ width: '100%', marginTop: 8 }}
              options={models.map((m) => ({
                label: m.model_name,
                value: m.id,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </div>

          {/* 方式一：文件上传 */}
          <Upload.Dragger
            accept=".json"
            multiple={false}
            beforeUpload={handleUpload}
            showUploadList={false}
            disabled={uploading}
          >
            <p className="ant-upload-drag-icon">
              <UploadOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
            <p className="ant-upload-hint">
              支持 JSON 格式，单个文件
            </p>
          </Upload.Dragger>

          <Divider style={{ margin: '24px 0' }}>或</Divider>

          {/* 方式二：手动输入 */}
          <div style={{ marginBottom: 16 }}>
            <Text strong>手动输入报告内容：</Text>
            <TextArea
              value={reportContent}
              onChange={(e) => setReportContent(e.target.value)}
              rows={12}
              style={{
                fontFamily: 'monospace',
                fontSize: 12,
                marginTop: 8,
                whiteSpace: 'pre-wrap',
              }}
              placeholder={`{
  "model_name": "Qwen/Qwen3-8B",
  "hardware": "A2",
  "dtype": "bf16",
  "feature": ["mlp_prefetch"],
  "vllm_version": "0.18.0",
  "vllm_ascend_version": "releases/v0.18.0",
  "tasks": [
    {
      "name": "GSM8K-in3500-bs2800",
      "metrics": {
        "Prefill_Token_Throughput": 634.19,
        "Input_Token_Throughput": 848.38,
        "Output_Token_Throughput": 347.75,
        "Total_Token_Throughput": 1196.13
      },
      "test_input": {
        "num_prompts": 1,
        "max_out_len": 3000,
        "batch_size": 1,
        "request_rate": 11.2
      },
      "target": {
        "baseline": 1,
        "threshold": 0.97
      },
      "pass_fail": "pass"
    }
  ],
  "serve_cmd": {
    "mix": "vllm server --model Qwen/Qwen3-8B --tensor-parallel-size 4"
  },
  "environment": {
    "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
    "PYTORCH_NPU_ALLOC_CONF": "max_split_size_mb=32"
  }
}`}
            />
            <div style={{ marginTop: 12, textAlign: 'right' }}>
              <Button
                type="primary"
                onClick={handleSubmitReportContent}
                loading={uploadReportMutation.isPending}
                disabled={!selectedModelId}
              >
                提交报告
              </Button>
            </div>
          </div>

          <Divider>报告示例模版</Divider>

          <Collapse
            items={[{
              key: 'template',
              label: <strong>📖 查看报告示例模版和字段说明</strong>,
              children: (
                <div style={{ fontSize: 12 }}>
                  <p style={{ fontWeight: 500, marginBottom: 8 }}>JSON 格式示例：</p>
                  <pre style={{
                    backgroundColor: '#f5f5f5',
                    padding: 12,
                    borderRadius: 4,
                    fontFamily: 'monospace',
                    fontSize: 11,
                    overflow: 'auto',
                    maxHeight: '400px',
                    whiteSpace: 'pre-wrap',
                    margin: 0,
                  }}>
{`{
  "model_name": "Qwen/Qwen3-0.6B",
  "hardware": "A2",
  "dtype": "bffloat16",
  "feature": ["mlp_prefetch"],
  "vllm_version": "0.18.0",
  "vllm_ascend_version": "releases/v0.18.0",
  "tasks": [
    {
      "name": "GSM8K-in3500-bs2800",
      "metrics": {
        "Prefill_Token_Throughput": 634.19,
        "Input_Token_Throughput": 848.38,
        "Output_Token_Throughput": 347.75,
        "Total_Token_Throughput": 1196.13
      },
      "test_input": {
        "num_prompts": 1,
        "max_out_len": 3000,
        "batch_size": 1,
        "request_rate": 11.2
      },
      "target": {
        "baseline": 1,
        "threshold": 0.97
      },
      "pass_fail": "pass"
    },
    {
      "name": "gsm8k-lite",
      "metrics": {
        "accuracy": 100
      },
      "test_input": {
        "max_out_len": 4096,
        "batch_size": 64
      },
      "target": {
        "baseline": 1,
        "threshold": 0.97
      },
      "pass_fail": "pass"
    }
  ],
  "serve_cmd": {
    "mix": "vllm server --model Qwen/Qwen3-0.6B --tensor-parallel-size 4"
  },
  "environment": {
    "ASCEND_RT_VISIBLE_DEVICES": "0,1,2,3",
    "PYTORCH_NPU_ALLOC_CONF": "max_split_size_mb=32"
  }
}`}
                  </pre>

                  <div style={{ marginTop: 16 }}>
                    <p style={{ fontWeight: 500, marginBottom: 8 }}>字段说明：</p>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ backgroundColor: '#fafafa' }}>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>字段</th>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>类型</th>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>必填</th>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>model_name</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>是</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>模型名称，如 Qwen/Qwen3-32B</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>hardware</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>是</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>硬件类型：A2, A3, 310P 等</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>dtype</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>权重类型：w8a8, fp16, bf16 等</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>feature</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>array</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>启用的特性列表：["mlp_prefetch", "bbb"]</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>vllm_version</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>vLLM 版本：0.18.0, 0.19.0 等</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>vllm_ascend_version</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>vLLM Ascend 版本</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>tasks</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>array</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>是</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>测试任务列表</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>serve_cmd</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>object</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>启动命令：<code>{"{ mix: '...' }"}</code> 或 <code>{"{ pd: {...} }"}</code></td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>environment</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>object</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>否</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>环境变量：<code>{"{ ENV1: 'aaa', ... }"}</code></td>
                        </tr>
                      </tbody>
                    </table>

                    <p style={{ fontWeight: 500, margin: '16px 0 8px' }}>Task 结构：</p>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ backgroundColor: '#fafafa' }}>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>字段</th>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>类型</th>
                          <th style={{ border: '1px solid #d9d9d9', padding: '8px', textAlign: 'left' }}>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>name</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>任务名称，如 GSM8K-in3500-bs2800</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>metrics</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>object</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>性能指标键值对，如 Throughput、Accuracy 等</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>test_input</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>object</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>测试输入参数：num_prompts, max_out_len, batch_size, request_rate 等</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>target</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>object</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>目标阈值配置</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>target.baseline</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>number</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>基准值，通常为 1</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>target.threshold</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>number</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>阈值，如 0.97 表示达到基准值的 97% 即为 pass</td>
                        </tr>
                        <tr>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px', fontFamily: 'monospace' }}>pass_fail</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>string</td>
                          <td style={{ border: '1px solid #d9d9d9', padding: '8px' }}>单个任务的 pass/fail 结果</td>
                        </tr>
                      </tbody>
                    </table>

                    <p style={{ fontWeight: 500, margin: '16px 0 8px' }}>Pass/Fail 判定规则：</p>
                    <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.8 }}>
                      <li>每个 task 有自己的 <code>pass_fail</code> 字段，根据 metrics 是否达到 target 阈值自动判定</li>
                      <li><strong>总体 pass_fail</strong> = 所有 task 都 pass 才算 pass，否则为 fail</li>
                      <li>系统会自动计算总体 pass_fail，无需手动指定</li>
                    </ul>

                    <p style={{ fontWeight: 500, margin: '16px 0 8px' }}>serve_cmd 格式说明：</p>
                    <ul style={{ margin: 0, paddingLeft: 20, lineHeight: 1.8 }}>
                      <li><strong>标准部署（mix）模式</strong>：<code>{"{ \"mix\": \"vllm server --model ... --tensor-parallel-size 4\" }"}</code></li>
                      <li><strong>PD 分离（pd）模式</strong>：<code>{"{ \"pd\": { \"prefill-0\": \"...\", \"decode-0\": \"...\" } }"}</code></li>
                      <li><code>mix</code> 和 <code>pd</code> 是互斥的，只能使用其中一种</li>
                      <li>PD 分离模式下，每个节点都需要完整的启动命令</li>
                    </ul>

                    <p style={{ marginTop: 12, color: '#1890ff' }}>
                      <strong>提示：</strong>完整文档请参阅 <a href="https://github.com/ascend/vllm_ascend_dashboard/blob/main/docs/report_template.md" target="_blank">docs/report_template.md</a>
                    </p>
                  </div>
                </div>
              ),
            }]}
            style={{ marginTop: 16 }}
          />
        </div>
      </Modal>

      {/* 报告编辑弹窗 */}
      <ReportEditModal
        visible={isEditModalVisible}
        report={editingReport}
        modelId={reportListModelId || 0}
        onClose={() => {
          setIsEditModalVisible(false)
          setEditingReport(null)
        }}
        onSuccess={handleReportUpdated}
      />

      {/* 模型详情弹窗 */}
      <Modal
        title="模型详情"
        open={isModelDetailModalVisible}
        onCancel={() => {
          setIsModelDetailModalVisible(false)
          setViewingModel(null)
        }}
        footer={null}
        width={700}
      >
        {viewingModel && (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            {/* 基本信息 */}
            <Card size="small" title="基本信息">
              <Descriptions column={2} size="small">
                <Descriptions.Item label="模型名称">
                  {viewingModel.model_name}
                </Descriptions.Item>
                <Descriptions.Item label="系列">
                  {viewingModel.series || '-'}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag color={viewingModel.status === 'active' ? 'green' : 'default'}>
                    {viewingModel.status === 'active' ? '活跃' : '未激活'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="创建时间">
                  {dayjs(viewingModel.created_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
                <Descriptions.Item label="更新时间">
                  {dayjs(viewingModel.updated_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
              </Descriptions>
            </Card>

            {/* 官方文档链接 */}
            {viewingModel.official_doc_url && (
              <Card size="small" title="官方文档">
                <Space>
                  <LinkOutlined />
                  <a
                    href={viewingModel.official_doc_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {viewingModel.official_doc_url}
                  </a>
                </Space>
              </Card>
            )}

            {/* 启动命令配置 */}
            {viewingModel.startup_commands && (
              <Card size="small" title="启动命令配置">
                <StartupCommandDisplay
                  modelId={viewingModel.id}
                  commands={viewingModel.startup_commands as any}
                  editable={false}
                />
              </Card>
            )}

            {/* 操作按钮 */}
            <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
              <Button onClick={() => setIsModelDetailModalVisible(false)}>关闭</Button>
            </Space>
          </Space>
        )}
      </Modal>

      {/* 报告详情弹窗 */}
      <ReportDetailModal
        visible={isDetailModalVisible}
        report={viewingReport}
        onClose={() => {
          setIsDetailModalVisible(false)
          setViewingReport(null)
        }}
      />
    </div>
  )
}

export default ModelBoardConfig
