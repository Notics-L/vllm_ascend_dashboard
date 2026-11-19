import { useState, useEffect } from 'react'
import {
  Modal, Input, Select, Button, Space, message, Typography,
  Card, Descriptions, Tag, Table, InputNumber, Collapse,
  Radio, Popconfirm
} from 'antd'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { ModelReport, TaskReport } from '../types/models'
import { updateReport } from '../services/models'
import {
  EditOutlined, SaveOutlined, CodeOutlined,
  EnvironmentOutlined, SettingOutlined, PlusOutlined,
  DeleteOutlined
} from '@ant-design/icons'
import './Modal.css'

const { TextArea } = Input
const { Text, Title } = Typography

interface ReportEditModalProps {
  visible: boolean
  report: ModelReport | null
  modelId: number
  onClose: () => void
  onSuccess: () => void
}

/**
 * 报告编辑弹窗组件 - 按报告详情页结构呈现
 */
export function ReportEditModal({
  visible,
  report,
  modelId,
  onClose,
  onSuccess,
}: ReportEditModalProps) {
  const queryClient = useQueryClient()
  
  // 状态
  const [passFail, setPassFail] = useState<string>('pass')
  const [autoPassFail, setAutoPassFail] = useState<string | null>(null)
  const [manualOverride, setManualOverride] = useState(false)
  const [vllmVersion, setVllmVersion] = useState('')
  const [vllmAscendVersion, setVllmAscendVersion] = useState('')
  const [hardware, setHardware] = useState('')
  const [dtype, setDtype] = useState('')
  const [features, setFeatures] = useState<string[]>([])

  // 部署配置
  const [serveCmdType, setServeCmdType] = useState<'mix' | 'pd'>('mix')
  const [serveCmdMix, setServeCmdMix] = useState('')
  const [serveCmdPd, setServeCmdPd] = useState<Record<string, string>>({})

  // 环境变量
  const [envList, setEnvList] = useState<Array<{ key: string; value: string }>>([])

  // Tasks
  const [tasks, setTasks] = useState<TaskReport[]>([])

  // 更新报告
  const updateMutation = useMutation({
    mutationFn: (data: any) => updateReport(modelId, report!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['model-reports', modelId] })
      message.success('报告已更新')
      onSuccess()
      onClose()
    },
    onError: (error: any) => {
      message.error('更新失败：' + (error.response?.data?.detail || '未知错误'))
    },
  })

  // 初始化数据
  useEffect(() => {
    if (report && visible) {
      setPassFail(report.pass_fail || 'pass')
      setAutoPassFail(report.auto_pass_fail || null)
      setManualOverride(report.manual_override || false)
      setVllmVersion(report.vllm_version || '')
      setVllmAscendVersion(report.report_json?.['vllm_ascend_version'] || '')
      setHardware(report.hardware || '')
      setDtype(report.dtype || report.report_json?.['dtype'] || '')
      setFeatures(report.features || report.report_json?.['feature'] || [])
      
      // 初始化部署配置
      const serveCmd = report.serve_cmd || report.report_json?.['serve_cmd'] || {}
      if (serveCmd.mix) {
        setServeCmdType('mix')
        setServeCmdMix(serveCmd.mix)
        setServeCmdPd({})
      } else if (serveCmd.pd) {
        setServeCmdType('pd')
        setServeCmdMix('')
        setServeCmdPd(serveCmd.pd)
      }
      
      // 初始化环境变量
      const env = report.environment || report.report_json?.['environment'] || {}
      setEnvList(Object.entries(env).map(([key, value]) => ({ key, value: value as string })))
      
      // 初始化 tasks
      setTasks(report.tasks || report.report_json?.['tasks'] || [])
    }
  }, [report, visible])

  // 添加特性
  const addFeature = (value: string) => {
    if (value && !features.includes(value)) {
      setFeatures([...features, value])
    }
  }

  // 删除特性
  const removeFeature = (index: number) => {
    setFeatures(features.filter((_, i) => i !== index))
  }

  // 添加环境变量
  const addEnv = () => {
    setEnvList([...envList, { key: '', value: '' }])
  }

  // 更新环境变量
  const updateEnv = (index: number, field: 'key' | 'value', value: string) => {
    const newList = [...envList]
    newList[index][field] = value
    setEnvList(newList)
  }

  // 删除环境变量
  const removeEnv = (index: number) => {
    setEnvList(envList.filter((_, i) => i !== index))
  }

  // 添加 PD 节点
  const addPdNode = () => {
    const newNodeKey = `node-${Object.keys(serveCmdPd).length + 1}`
    setServeCmdPd({ ...serveCmdPd, [newNodeKey]: '' })
  }

  // 更新 PD 节点
  const updatePdNode = (key: string, value: string) => {
    const newPd = { ...serveCmdPd }
    if (value === '') {
      delete newPd[key]
    } else {
      newPd[key] = value
    }
    setServeCmdPd(newPd)
  }

  // 删除 PD 节点
  const removePdNode = (key: string) => {
    const newPd = { ...serveCmdPd }
    delete newPd[key]
    setServeCmdPd(newPd)
  }

  // 保存
  const handleSave = () => {
    // 构建 serve_cmd
    const serveCmd = serveCmdType === 'mix' 
      ? { mix: serveCmdMix }
      : { pd: serveCmdPd }
    
    // 构建 environment
    const environment: Record<string, string> = {}
    envList.forEach(item => {
      if (item.key && item.value) {
        environment[item.key] = item.value
      }
    })

    // 构建 report_json
    const updatedReportJson = {
      ...(report?.report_json || {}),
      vllm_ascend_version: vllmAscendVersion,
      dtype,
      feature: features,
      serve_cmd: serveCmd,
      environment: environment,
      tasks: tasks,
    }

    const updateData = {
      pass_fail: passFail,
      auto_pass_fail: autoPassFail,
      manual_override: manualOverride,
      report_json: updatedReportJson,
      // 同步更新顶层字段
      vllm_version: vllmVersion,
      hardware,
      dtype,
      features,
      serve_cmd: serveCmd,
      environment: environment,
      tasks,
    }

    updateMutation.mutate(updateData)
  }

  if (!report) return null

  return (
    <Modal
      title={
        <Space>
          <EditOutlined />
          编辑报告 #{report.id}
        </Space>
      }
      open={visible}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSave}
            loading={updateMutation.isPending}
          >
            保存
          </Button>
        </Space>
      }
      width={1200}
      className="stripe-modal"
    >
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        
        {/* 基本信息 */}
        <div>
          <Title level={5}>基本信息</Title>
          <Descriptions column={3} bordered size="small">
            <Descriptions.Item label="报告时间">
              {new Date(report.created_at).toLocaleString('zh-CN')}
            </Descriptions.Item>
            <Descriptions.Item label="Workflow Run ID">
              {report.workflow_run_id || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="Pass/Fail 状态">
              <Select 
                value={passFail} 
                onChange={setPassFail}
                style={{ width: 120 }}
              >
                <Select.Option value="pass">通过</Select.Option>
                <Select.Option value="fail">未通过</Select.Option>
              </Select>
            </Descriptions.Item>
            <Descriptions.Item label="vLLM 版本">
              <Input 
                value={vllmVersion} 
                onChange={(e) => setVllmVersion(e.target.value)}
                style={{ width: 120 }}
                placeholder="v0.19.0"
              />
            </Descriptions.Item>
            <Descriptions.Item label="vLLM Ascend 版本">
              <Input 
                value={vllmAscendVersion} 
                onChange={(e) => setVllmAscendVersion(e.target.value)}
                style={{ width: 120 }}
                placeholder="0.5.0"
              />
            </Descriptions.Item>
            <Descriptions.Item label="硬件类型">
              <Select 
                value={hardware} 
                onChange={setHardware}
                style={{ width: 100 }}
              >
                <Select.Option value="A2">A2</Select.Option>
                <Select.Option value="A3">A3</Select.Option>
                <Select.Option value="A5">A5</Select.Option>
                <Select.Option value="310P">310P</Select.Option>
              </Select>
            </Descriptions.Item>
            <Descriptions.Item label="权重类型">
              <Input 
                value={dtype} 
                onChange={(e) => setDtype(e.target.value)}
                style={{ width: 120 }}
                placeholder="w8a8, fp16, bf16 等"
              />
            </Descriptions.Item>
          </Descriptions>
        </div>

        {/* 特性列表 */}
        <div>
          <Title level={5}>特性列表</Title>
          <Space wrap>
            {features.map((feature: string, index: number) => (
              <Tag 
                key={index} 
                color="cyan" 
                closable 
                onClose={() => removeFeature(index)}
              >
                {feature}
              </Tag>
            ))}
            <Input 
              placeholder="+ 添加特性" 
              size="small"
              style={{ width: 150 }}
              onPressEnter={(e) => {
                const value = e.currentTarget.value.trim()
                if (value) {
                  addFeature(value)
                  e.currentTarget.value = ''
                }
              }}
            />
          </Space>
        </div>

        {/* 部署配置 */}
        <div>
          <Title level={5}>
            <SettingOutlined /> 部署配置
          </Title>
          <Card size="small" type="inner">
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Radio.Group 
                value={serveCmdType} 
                onChange={(e) => setServeCmdType(e.target.value)}
              >
                <Radio.Button value="mix">标准部署 (Mix)</Radio.Button>
                <Radio.Button value="pd">PD 分离部署</Radio.Button>
              </Radio.Group>

              {serveCmdType === 'mix' ? (
                <div>
                  <Text strong>启动命令：</Text>
                  <TextArea
                    value={serveCmdMix}
                    onChange={(e) => setServeCmdMix(e.target.value)}
                    rows={3}
                    style={{ fontFamily: 'monospace', marginTop: 8 }}
                    placeholder="vllm server --model ..."
                  />
                </div>
              ) : (
                <div>
                  <Space style={{ marginBottom: 8 }}>
                    <Text strong>PD 节点配置：</Text>
                    <Button 
                      type="dashed" 
                      size="small" 
                      icon={<PlusOutlined />}
                      onClick={addPdNode}
                    >
                      添加节点
                    </Button>
                  </Space>
                  {Object.entries(serveCmdPd).map(([key, cmd]) => (
                    <Card key={key} size="small" type="inner" style={{ marginBottom: 8 }}>
                      <Space style={{ width: '100%' }}>
                        <Input 
                          value={key}
                          onChange={(e) => {
                            const newKey = e.target.value
                            if (newKey !== key) {
                              const newPd = { ...serveCmdPd }
                              delete newPd[key]
                              newPd[newKey] = cmd
                              setServeCmdPd(newPd)
                            }
                          }}
                          style={{ width: 150 }}
                          placeholder="节点名称"
                        />
                        <TextArea
                          value={cmd}
                          onChange={(e) => updatePdNode(key, e.target.value)}
                          rows={2}
                          style={{ flex: 1, fontFamily: 'monospace' }}
                          placeholder="启动命令"
                        />
                        <Popconfirm
                          title="确定删除此节点？"
                          onConfirm={() => removePdNode(key)}
                        >
                          <Button danger size="small" icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </Space>
                    </Card>
                  ))}
                </div>
              )}
            </Space>
          </Card>
        </div>

        {/* 环境变量 */}
        <div>
          <Title level={5}>
            <EnvironmentOutlined /> 环境变量
          </Title>
          <Card size="small" type="inner">
            <Table
              size="small"
              pagination={false}
              dataSource={envList.map((item, index) => ({ ...item, _index: index }))}
              columns={[
                {
                  title: '变量名 (Key)',
                  dataIndex: 'key',
                  key: 'key',
                  width: 200,
                  render: (text: string, record: any) => (
                    <Input
                      value={text}
                      onChange={(e) => updateEnv(record._index, 'key', e.target.value)}
                      placeholder="例如：ASCEND_RT_VISIBLE_DEVICES"
                    />
                  ),
                },
                {
                  title: '变量值 (Value)',
                  dataIndex: 'value',
                  key: 'value',
                  render: (text: string, record: any) => (
                    <Input
                      value={text}
                      onChange={(e) => updateEnv(record._index, 'value', e.target.value)}
                      placeholder="例如：0,1,2,3"
                      style={{ fontFamily: 'monospace' }}
                    />
                  ),
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 80,
                  render: (_: any, record: any) => (
                    <Button
                      type="link"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      onClick={() => removeEnv(record._index)}
                    />
                  ),
                },
              ]}
            />
            <Button
              type="dashed"
              block
              icon={<PlusOutlined />}
              onClick={addEnv}
              style={{ marginTop: 8 }}
            >
              添加环境变量
            </Button>
          </Card>
        </div>

        {/* Tasks 列表 */}
        {tasks && tasks.length > 0 && (
          <div>
            <Title level={5}><CodeOutlined /> 测试任务详情 ({tasks.length})</Title>
            <Collapse
              defaultActiveKey={[]}
              expandIconPosition="end"
              items={tasks.map((task, taskIndex) => ({
                key: taskIndex,
                label: (
                  <Space>
                    <Tag color={task.pass_fail === 'pass' ? 'green' : 'red'}>
                      {task.pass_fail === 'pass' ? '✓' : '✗'}
                    </Tag>
                    <Input
                      value={task.name}
                      onChange={(e) => {
                        const newTasks = [...tasks]
                        newTasks[taskIndex].name = e.target.value
                        setTasks(newTasks)
                      }}
                      style={{ width: 300 }}
                      placeholder="Task 名称"
                    />
                    <Select
                      value={task.pass_fail}
                      onChange={(val) => {
                        const newTasks = [...tasks]
                        newTasks[taskIndex].pass_fail = val
                        setTasks(newTasks)
                      }}
                      style={{ width: 100 }}
                    >
                      <Select.Option value="pass">pass</Select.Option>
                      <Select.Option value="fail">fail</Select.Option>
                    </Select>
                  </Space>
                ),
                children: (
                  <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                    {/* 测试输入参数 */}
                    <Card title="测试输入参数" size="small" type="inner">
                      <Table
                        size="small"
                        pagination={false}
                        dataSource={
                          Object.entries(task.test_input || {}).map(([key, value], idx) => ({
                            _key: key,
                            _value: value,
                            _idx: idx,
                          }))
                        }
                        columns={[
                          {
                            title: '参数名',
                            dataIndex: '_key',
                            key: '_key',
                            width: 200,
                            render: (text: string, record: any) => (
                              <Input
                                value={text}
                                onChange={(e) => {
                                  const newTasks = [...tasks]
                                  const newInput = { ...newTasks[taskIndex].test_input }
                                  delete newInput[record._key]
                                  newInput[e.target.value] = record._value
                                  newTasks[taskIndex].test_input = newInput
                                  setTasks(newTasks)
                                }}
                                placeholder="例如：batch_size"
                              />
                            ),
                          },
                          {
                            title: '参数值',
                            dataIndex: '_value',
                            key: '_value',
                            width: 150,
                            render: (value: any, record: any) => (
                              <InputNumber
                                value={value as number}
                                onChange={(val) => {
                                  const newTasks = [...tasks]
                                  newTasks[taskIndex].test_input = {
                                    ...newTasks[taskIndex].test_input,
                                    [record._key]: val,
                                  }
                                  setTasks(newTasks)
                                }}
                                style={{ width: '100%' }}
                              />
                            ),
                          },
                          {
                            title: '操作',
                            key: 'action',
                            width: 80,
                            render: (_: any, record: any) => (
                              <Button
                                type="link"
                                danger
                                size="small"
                                icon={<DeleteOutlined />}
                                onClick={() => {
                                  const newTasks = [...tasks]
                                  const newInput = { ...newTasks[taskIndex].test_input }
                                  delete newInput[record._key]
                                  newTasks[taskIndex].test_input = newInput
                                  setTasks(newTasks)
                                }}
                              />
                            ),
                          },
                        ]}
                      />
                      <Button
                        type="dashed"
                        block
                        size="small"
                        icon={<PlusOutlined />}
                        onClick={() => {
                          const newTasks = [...tasks]
                          newTasks[taskIndex].test_input = {
                            ...newTasks[taskIndex].test_input,
                            'new_param': 0,
                          }
                          setTasks(newTasks)
                        }}
                        style={{ marginTop: 8 }}
                      >
                        添加参数
                      </Button>
                    </Card>

                    {/* 目标阈值 */}
                    <Card title="目标阈值" size="small" type="inner">
                      <Table
                        size="small"
                        pagination={false}
                        dataSource={
                          Object.entries(task.target || {}).map(([key, value], idx) => ({
                            _key: key,
                            _value: value,
                            _idx: idx,
                          }))
                        }
                        columns={[
                          {
                            title: '阈值名',
                            dataIndex: '_key',
                            key: '_key',
                            width: 200,
                            render: (text: string, record: any) => (
                              <Input
                                value={text}
                                onChange={(e) => {
                                  const newTasks = [...tasks]
                                  const newTarget = { ...newTasks[taskIndex].target }
                                  delete newTarget[record._key]
                                  newTarget[e.target.value] = record._value
                                  newTasks[taskIndex].target = newTarget
                                  setTasks(newTasks)
                                }}
                                placeholder="例如：baseline"
                              />
                            ),
                          },
                          {
                            title: '阈值',
                            dataIndex: '_value',
                            key: '_value',
                            width: 150,
                            render: (value: any, record: any) => (
                              <InputNumber
                                value={value as number}
                                onChange={(val) => {
                                  const newTasks = [...tasks]
                                  newTasks[taskIndex].target = {
                                    ...newTasks[taskIndex].target,
                                    [record._key]: val,
                                  }
                                  setTasks(newTasks)
                                }}
                                style={{ width: '100%' }}
                                step={0.01}
                              />
                            ),
                          },
                          {
                            title: '操作',
                            key: 'action',
                            width: 80,
                            render: (_: any, record: any) => (
                              <Button
                                type="link"
                                danger
                                size="small"
                                icon={<DeleteOutlined />}
                                onClick={() => {
                                  const newTasks = [...tasks]
                                  const newTarget = { ...newTasks[taskIndex].target }
                                  delete newTarget[record._key]
                                  newTasks[taskIndex].target = newTarget
                                  setTasks(newTasks)
                                }}
                              />
                            ),
                          },
                        ]}
                      />
                      <Button
                        type="dashed"
                        block
                        size="small"
                        icon={<PlusOutlined />}
                        onClick={() => {
                          const newTasks = [...tasks]
                          newTasks[taskIndex].target = {
                            ...newTasks[taskIndex].target,
                            'new_threshold': 0,
                          }
                          setTasks(newTasks)
                        }}
                        style={{ marginTop: 8 }}
                      >
                        添加阈值
                      </Button>
                    </Card>

                    {/* Metrics */}
                    <Card title="性能指标" size="small" type="inner">
                      <Table
                        size="small"
                        pagination={false}
                        dataSource={
                          Object.entries(task.metrics || {}).map(([key, value], idx) => ({
                            _key: key,
                            _value: value,
                            _idx: idx,
                          }))
                        }
                        columns={[
                          {
                            title: '指标名',
                            dataIndex: '_key',
                            key: '_key',
                            width: 250,
                            render: (text: string, record: any) => (
                              <Input
                                value={text}
                                onChange={(e) => {
                                  const newTasks = [...tasks]
                                  const newMetrics = { ...newTasks[taskIndex].metrics }
                                  delete newMetrics[record._key]
                                  newMetrics[e.target.value] = record._value
                                  newTasks[taskIndex].metrics = newMetrics
                                  setTasks(newTasks)
                                }}
                                placeholder="例如：accuracy"
                              />
                            ),
                          },
                          {
                            title: '指标值',
                            dataIndex: '_value',
                            key: '_value',
                            render: (value: any, record: any) => (
                              <InputNumber
                                value={value as number}
                                onChange={(val) => {
                                  const newTasks = [...tasks]
                                  newTasks[taskIndex].metrics = {
                                    ...newTasks[taskIndex].metrics,
                                    [record._key]: val,
                                  }
                                  setTasks(newTasks)
                                }}
                                style={{ width: '100%' }}
                                step={0.0001}
                              />
                            ),
                          },
                          {
                            title: '操作',
                            key: 'action',
                            width: 80,
                            render: (_: any, record: any) => (
                              <Button
                                type="link"
                                danger
                                size="small"
                                icon={<DeleteOutlined />}
                                onClick={() => {
                                  const newTasks = [...tasks]
                                  const newMetrics = { ...newTasks[taskIndex].metrics }
                                  delete newMetrics[record._key]
                                  newTasks[taskIndex].metrics = newMetrics
                                  setTasks(newTasks)
                                }}
                              />
                            ),
                          },
                        ]}
                      />
                    </Card>
                  </Space>
                ),
              }))}
            />
          </div>
        )}

      </Space>
    </Modal>
  )
}
