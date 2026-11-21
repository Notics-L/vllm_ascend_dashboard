import { useState } from 'react'
import { Card, Collapse, Typography, Space, Tag, Button, message } from 'antd'
import {
  CopyOutlined,
  CheckOutlined,
} from '@ant-design/icons'
import type { StartupCommands } from '../types/models'

const { Text, Paragraph } = Typography

interface StartupCommandDisplayProps {
  modelId: number
  commands: StartupCommands
  editable?: boolean
}

// 部署场景和硬件的显示名称
const SCENARIO_LABELS: Record<string, string> = {
  'standard': '标准部署',
  'pd-disaggregation': 'PD 分离',
}

const PD_NODE_LABELS: Record<string, string> = {
  'p': 'P 节点',
  'd': 'D 节点',
}

const HARDWARE_LABELS: Record<string, string> = {
  'A2': 'Atlas A2',
  'A3': 'Atlas A3',
  'A5': 'Atlas A5',
  '310P': 'Ascend 310P',
}

/**
 * 启动命令展示组件
 * 支持多维度展示（版本 × 场景 × 硬件）
 */
export function StartupCommandDisplay({
  commands,
  editable = false,
}: StartupCommandDisplayProps) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  // 复制命令
  const handleCopy = async (key: string, command: string) => {
    await navigator.clipboard.writeText(command)
    setCopiedKey(key)
    message.success('命令已复制到剪贴板')
    setTimeout(() => setCopiedKey(null), 2000)
  }

  if (!commands || Object.keys(commands).length === 0) {
    return (
      <Card title="vLLM 启动命令" size="small">
        <Text type="secondary">暂无启动命令配置</Text>
      </Card>
    )
  }

  // 渲染配置项
  const renderItems = () => {
    const items: any[] = []
    const processedPdKeys = new Set<string>()

    Object.entries(commands).forEach(([version, scenarios]) => {
      // 收集 PD 分离的 P 和 D 节点
      const pdNodes: Record<string, Record<string, string>> = {}
      
      Object.entries(scenarios).forEach(([scenario, hardwareConfigs]) => {
        if (scenario === 'pd-disaggregation-p' || scenario === 'pd-disaggregation-d') {
          const nodeType = scenario.replace('pd-disaggregation-', '')
          pdNodes[nodeType] = hardwareConfigs as Record<string, string>
        }
      })
      
      // 如果有 PD 分离节点，创建嵌套条目
      const pdKey = `${version}:pd-disaggregation`
      if (Object.keys(pdNodes).length > 0 && !processedPdKeys.has(pdKey)) {
        processedPdKeys.add(pdKey)
        
        const pdChildren: any[] = []
        Object.entries(pdNodes).forEach(([nodeType, hardwareConfigs]) => {
          Object.entries(hardwareConfigs).forEach(([hardware, command]) => {
            const key = `${version}:pd-disaggregation:${nodeType}:${hardware}`
            pdChildren.push({
              key,
              label: (
                <Space>
                  <Tag color="cyan">{PD_NODE_LABELS[nodeType] || nodeType}</Tag>
                  <Tag color="orange">{HARDWARE_LABELS[hardware] || hardware}</Tag>
                </Space>
              ),
              children: (
                <Space direction="vertical" style={{ width: '100%' }} size="small">
                  <Paragraph
                    style={{
                      backgroundColor: '#f5f5f5',
                      padding: 12,
                      borderRadius: 4,
                      fontFamily: 'monospace',
                      fontSize: 12,
                      whiteSpace: 'pre-wrap',
                      margin: 0,
                    }}
                  >
                    {command}
                  </Paragraph>
                  <Space>
                    <Button
                      type="link"
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopy(key, command)}
                    >
                      复制
                    </Button>
                  </Space>
                </Space>
              ),
            })
          })
        })
        
        items.push({
          key: pdKey,
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
      
      // 渲染标准场景（跳过 PD 分离）
      Object.entries(scenarios).forEach(([scenario, hardwareConfigs]) => {
        if (scenario === 'pd-disaggregation-p' || scenario === 'pd-disaggregation-d') {
          return // 跳过，已处理
        }
        
        Object.entries(hardwareConfigs).forEach(([hardware, command]) => {
          const key = `${version}:${scenario}:${hardware}`
          const isCopied = copiedKey === key
          
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
                <Paragraph
                  style={{
                    backgroundColor: '#f5f5f5',
                    padding: 12,
                    borderRadius: 4,
                    fontFamily: 'monospace',
                    fontSize: 12,
                    whiteSpace: 'pre-wrap',
                    margin: 0,
                  }}
                >
                  {command}
                </Paragraph>
                <Space>
                  <Button
                    type="link"
                    size="small"
                    icon={isCopied ? <CheckOutlined /> : <CopyOutlined />}
                    onClick={() => handleCopy(key, command)}
                  >
                    {isCopied ? '已复制' : '复制'}
                  </Button>
                </Space>
              </Space>
            ),
          })
        })
      })
    })
    
    return items
  }

  return (
    <Card
      title="vLLM 启动命令"
      size="small"
    >
      <Collapse items={renderItems()} expandIconPosition="end" />
    </Card>
  )
}
