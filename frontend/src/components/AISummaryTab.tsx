/**
 * AI 总结 Tab 组件
 */
import React, { useEffect } from 'react'
import { Empty, Button, Spin, Alert, Space, Tag, Typography, message } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { RobotOutlined, ReloadOutlined, DownloadOutlined } from '@ant-design/icons'
import { useDailySummary, useRegenerateDailySummary } from '../hooks/useDailySummary'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import timezone from 'dayjs/plugin/timezone'

dayjs.extend(utc)
dayjs.extend(timezone)

const BEIJING_TIMEZONE = 'Asia/Shanghai'

const { Text } = Typography

interface AISummaryTabProps {
  project: string | undefined
  date: string
  isSuperAdmin?: boolean  // 是否为超级管理员
}

export const AISummaryTab: React.FC<AISummaryTabProps> = ({ project, date, isSuperAdmin = false }) => {
  const { data, isLoading, error, refetch } = useDailySummary(project || '', date)
  const regenerateMutation = useRegenerateDailySummary()

  const handleRegenerate = () => {
    if (!project) return

    regenerateMutation.mutate({
      project,
      date,
    })
  }

  // 导出总结为 Markdown 文件
  const handleExport = () => {
    if (!data?.summary_markdown) {
      message.warning('暂无可导出的总结内容')
      return
    }

    // 构建导出内容
    const exportContent = `# ${project === 'vllm' ? 'vLLM' : 'vLLM Ascend'} 项目动态总结

日期：${date}

---

${data.summary_markdown}

---

## 数据统计
- PR 数量：${data.pr_count}
- Issue 数量：${data.issue_count}
- Commit 数量：${data.commit_count}

生成时间：${dayjs(data.generated_at).tz(BEIJING_TIMEZONE).format('YYYY-MM-DD HH:mm:ss')}
`

    // 创建 Blob 并下载
    const blob = new Blob([exportContent], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${project}_summary_${date}.md`
    link.click()
    URL.revokeObjectURL(link.href)
    
    message.success('总结已导出')
  }

  // 监听 mutation 状态，显示成功/失败消息
  useEffect(() => {
    if (regenerateMutation.isSuccess) {
      message.success('总结生成成功！')
      // 自动刷新数据
      refetch()
    }
  }, [regenerateMutation.isSuccess])

  useEffect(() => {
    if (regenerateMutation.isError) {
      const errorMsg = (regenerateMutation.error as any)?.response?.data?.detail || 
                       (regenerateMutation.error as any)?.message || 
                       '生成失败，请稍后重试'
      message.error(errorMsg)
    }
  }, [regenerateMutation.isError, regenerateMutation.error])

  // 显示加载状态
  if (regenerateMutation.isPending) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px' }}>
        <Spin size="large" tip="正在调用 AI 生成总结，预计需要 30-60 秒..." />
        <div style={{ marginTop: 16, color: '#8c8c8c' }}>
          <p>AI 正在分析项目数据并生成报告，请耐心等待...</p>
          <p style={{ fontSize: 12, marginTop: 8 }}>
            提示：生成过程需要调用大模型 API，耗时较长，请勿关闭页面
          </p>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return <Spin tip="加载中..." />
  }

  if (error) {
    // 判断是否是 404 错误（总结未生成）
    const errorMsg = (error as any).response?.data?.detail || (error as any).message
    if (errorMsg?.includes('未找到') || errorMsg?.includes('404')) {
      return (
        <Empty
          description={isSuperAdmin ? "暂未生成 AI 总结，请点击'生成'按钮生成" : "暂未生成 AI 总结"}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        >
          {isSuperAdmin && (
            <Button type="primary" icon={<RobotOutlined />} onClick={handleRegenerate}>
              生成总结
            </Button>
          )}
        </Empty>
      )
    }
    return (
      <Alert
        message="获取总结失败"
        description={errorMsg || '请稍后重试'}
        type="error"
        showIcon
        action={
          <Button size="small" onClick={() => refetch()}>
            重试
          </Button>
        }
      />
    )
  }

  // 总结尚未生成
  if (!data || data.status === 'not_generated' || !data.summary_markdown) {
    return (
      <Empty
        description={isSuperAdmin ? "暂未生成 AI 总结，请点击'生成'按钮生成" : "暂未生成 AI 总结"}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      >
        {isSuperAdmin && (
          <Button
            type="primary"
            icon={<RobotOutlined />}
            onClick={handleRegenerate}
            loading={regenerateMutation.isPending}
          >
            {regenerateMutation.isPending ? '生成中...' : '生成总结'}
          </Button>
        )}
      </Empty>
    )
  }

  return (
    <div className="ai-summary-container">
      <div className="summary-meta" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Tag>生成时间：{dayjs(data.generated_at).tz(BEIJING_TIMEZONE).format('YYYY-MM-DD HH:mm')}</Tag>
          {data.has_data === false && (
            <Tag color="default">无数据</Tag>
          )}
        </Space>
        <Space style={{ marginLeft: 16 }}>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            onClick={handleExport}
          >
            导出
          </Button>
          {isSuperAdmin && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={regenerateMutation.isPending}
              onClick={handleRegenerate}
            >
              重新生成
            </Button>
          )}
        </Space>
      </div>
      <div className="summary-content" style={{ 
        padding: 20, 
        background: '#fafafa',
        borderRadius: 8,
        maxHeight: '600px',
        overflowY: 'auto'
      }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            table: ({ node: _node, ...props }) => (
              <div style={{ overflowX: 'auto', marginBottom: 16 }}>
                <table style={{ borderCollapse: 'collapse', width: '100%' }} {...props} />
              </div>
            ),
            th: ({ node: _node, ...props }) => (
              <th style={{ border: '1px solid #d9d9d9', padding: '8px 12px', background: '#f5f5f5', textAlign: 'left' }} {...props} />
            ),
            td: ({ node: _node, ...props }) => (
              <td style={{ border: '1px solid #d9d9d9', padding: '8px 12px', verticalAlign: 'top' }} {...props} />
            ),
          }}
        >
          {data.summary_markdown}
        </ReactMarkdown>
      </div>
    </div>
  )
}
