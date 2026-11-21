import React, { useEffect, useState } from 'react'
import { Modal, Spin, Alert, Space, Tag, Descriptions, Button, Typography, message, Tabs } from 'antd'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ReloadOutlined, FileSearchOutlined, ShareAltOutlined, DownloadOutlined, CloseCircleOutlined } from '@ant-design/icons'
import {
  useFailureAnalysis,
  useFailureAnalysisReport,
  useAnalyzeFailedJob,
  useCancelAnalysis,
} from '../hooks/useFailureAnalysis'
import {
  PROBLEM_CATEGORY_MAP,
  ANALYSIS_STATUS_MAP,
  type FailureAnalysis,
} from '../services/failureAnalysis'
import { FailureAnalysisKnowledgeGraph } from './FailureAnalysisKnowledgeGraph'

const { Text } = Typography

interface FailureAnalysisDetailModalProps {
  open: boolean
  onClose: () => void
  jobId: number | null
  existingAnalysis?: FailureAnalysis | null
}

export const FailureAnalysisDetailModal: React.FC<FailureAnalysisDetailModalProps> = ({
  open,
  onClose,
  jobId,
  existingAnalysis,
}) => {
  const [analysisId, setAnalysisId] = useState<number | null>(existingAnalysis?.id || null)

  const { data: analysis, isLoading: analysisLoading } = useFailureAnalysis(analysisId)
  const { data: reportData, isLoading: reportLoading } = useFailureAnalysisReport(
    analysis?.analysis_status === 'completed' ? analysisId : null
  )
  const analyzeMutation = useAnalyzeFailedJob()
  const cancelMutation = useCancelAnalysis()

  // analysis 优先：提交新分析后能反映最新状态，existingAnalysis 仅作为初始值
  const currentAnalysis = analysis || existingAnalysis

  useEffect(() => {
    setAnalysisId(existingAnalysis?.id || null)
  }, [jobId, existingAnalysis])

  useEffect(() => {
    if (analyzeMutation.isSuccess && analyzeMutation.data) {
      setAnalysisId(analyzeMutation.data.id)
      message.success('分析已提交，请等待完成...')
    }
  }, [analyzeMutation.isSuccess, analyzeMutation.data])

  useEffect(() => {
    if (analyzeMutation.isError) {
      const errorMsg = (analyzeMutation.error as any)?.response?.data?.detail ||
        (analyzeMutation.error as any)?.message || '分析失败，请稍后重试'
      message.error(errorMsg)
    }
  }, [analyzeMutation.isError, analyzeMutation.error])

  const handleReAnalyze = () => {
    if (!jobId) return
    analyzeMutation.mutate({ jobId, force: true })
  }

  const statusInfo = currentAnalysis
    ? ANALYSIS_STATUS_MAP[currentAnalysis.analysis_status] || { color: '#64748d', label: currentAnalysis.analysis_status }
    : null

  const categoryInfo = currentAnalysis?.problem_category
    ? PROBLEM_CATEGORY_MAP[currentAnalysis.problem_category] || { color: '#64748d', label: currentAnalysis.problem_category }
    : null

  const renderMarkdownContent = (content: string) => (
    <div style={{ padding: 20, background: '#fafafa', borderRadius: 8, maxHeight: '600px', overflowY: 'auto' }}>
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
        {content}
      </ReactMarkdown>
    </div>
  )

  return (
    <Modal
      title={
        <Space>
          <FileSearchOutlined />
          <span>失败分析报告</span>
          {currentAnalysis && statusInfo && (
            <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
          )}
          {categoryInfo && (
            <Tag color={categoryInfo.color}>{categoryInfo.label}</Tag>
          )}
          {currentAnalysis?.triggered_by && (
            <Tag color={currentAnalysis.triggered_by === 'scheduler' ? 'blue' : 'orange'}>
              {currentAnalysis.triggered_by === 'scheduler' ? '调度器' : '手动'}
            </Tag>
          )}
        </Space>
      }
      open={open}
      onCancel={onClose}
      width={1180}
      footer={[
        <Button key="close" onClick={onClose}>关闭</Button>,
        currentAnalysis?.share_token && currentAnalysis.analysis_status === 'completed' && (
          <Button
            key="pdf"
            icon={<DownloadOutlined />}
            onClick={() => { window.location.href = `/api/v1/ci/public/analysis/${currentAnalysis.share_token}/pdf` }}
          >
            下载 PDF
          </Button>
        ),
        currentAnalysis?.share_token && currentAnalysis.analysis_status === 'completed' && (
          <Button
            key="share"
            icon={<ShareAltOutlined />}
            onClick={() => {
              const url = `${window.location.origin}/share/${currentAnalysis.share_token}`
              if (navigator.clipboard) {
                navigator.clipboard.writeText(url).then(() => message.success('分享链接已复制')).catch(() => {
                  const input = document.createElement('input'); input.value = url; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); message.success('分享链接已复制')
                })
              } else {
                const input = document.createElement('input'); input.value = url; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); message.success('分享链接已复制')
              }
            }}
          >
            复制分享链接
          </Button>
        ),
        currentAnalysis && currentAnalysis.analysis_status === 'analyzing' && (
          <Button
            key="cancel"
            icon={<CloseCircleOutlined />}
            danger
            loading={cancelMutation.isPending}
            onClick={() => {
              if (!jobId) return
              cancelMutation.mutate(jobId, {
                onSuccess: () => message.success('已取消分析'),
                onError: (err: any) => message.error(err?.response?.data?.detail || '取消失败'),
              })
            }}
          >
            取消分析
          </Button>
        ),
        currentAnalysis && currentAnalysis.analysis_status !== 'analyzing' && (
          <Button
            key="reanalyze"
            icon={<ReloadOutlined />}
            loading={analyzeMutation.isPending}
            onClick={handleReAnalyze}
            type="primary"
          >
            重新分析
          </Button>
        ),
      ]}
    >
      {analyzeMutation.isPending && (
        <div style={{ textAlign: 'center', padding: '60px 20px' }}>
          <Spin size="large" />
          <div style={{ marginTop: 16, color: '#8c8c8c' }}>
            <p>AI 正在分析失败原因，请耐心等待...</p>
          </div>
        </div>
      )}

      {!analyzeMutation.isPending && !currentAnalysis && (
        <div style={{ textAlign: 'center', padding: '40px 20px' }}>
          <Alert
            message="暂无分析记录"
            description="该 Job 尚未进行失败分析，请点击“重新分析”按钮触发分析"
            type="info"
            showIcon
            action={
              <Button size="small" icon={<ReloadOutlined />} onClick={handleReAnalyze} loading={analyzeMutation.isPending}>
                开始分析
              </Button>
            }
          />
        </div>
      )}

      {!analyzeMutation.isPending && currentAnalysis && (
        <div>
          <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="分类">
              {categoryInfo ? (
                <Tag color={categoryInfo.color}>{categoryInfo.label}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              {statusInfo ? (
                <Tag color={statusInfo.color}>{statusInfo.label}</Tag>
              ) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="根因摘要" span={2}>
              {currentAnalysis.root_cause_summary || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="改进建议" span={2}>
              {currentAnalysis.improvement_measures_summary || '-'}
            </Descriptions.Item>
            {currentAnalysis.llm_provider && (
              <Descriptions.Item label="LLM">
                {currentAnalysis.llm_provider}/{currentAnalysis.llm_model || '-'}
              </Descriptions.Item>
            )}
            {currentAnalysis.generation_time_seconds && (
              <Descriptions.Item label="耗时">
                {currentAnalysis.generation_time_seconds.toFixed(1)}s
              </Descriptions.Item>
            )}
            {currentAnalysis.error_message && (
              <Descriptions.Item label="错误信息" span={2}>
                <Text type="danger">{currentAnalysis.error_message}</Text>
              </Descriptions.Item>
            )}
          </Descriptions>


          {currentAnalysis.analysis_status === 'completed' && (
            <Tabs
              items={[
                {
                  key: 'report',
                  label: '详细报告',
                  children: reportLoading ? (
                    <Spin tip="加载报告中..." />
                  ) : reportData?.content ? (
                    renderMarkdownContent(reportData.content)
                  ) : (
                    <Alert message="报告文件未找到" type="warning" showIcon />
                  ),
                },
                {
                  key: 'knowledge-graph',
                  label: '知识图谱',
                  children: <FailureAnalysisKnowledgeGraph analysisId={currentAnalysis.id} />,
                },
              ]}
            />
          )}

          {currentAnalysis.analysis_status === 'analyzing' && (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin size="large" tip="分析进行中..." />
              <div style={{ marginTop: 16, color: '#8c8c8c' }}>
                <p>系统正在自动分析中，状态和结果会自动更新</p>
                <p style={{ fontSize: 12, color: '#b0b0b0' }}>
                  通常需要 5-15 分钟，复杂分析可能更久
                </p>
              </div>
              <Button
                type="primary"
                danger
                icon={<CloseCircleOutlined />}
                loading={cancelMutation.isPending}
                onClick={() => {
                  if (!jobId) return
                  cancelMutation.mutate(jobId, {
                    onSuccess: () => message.success('已取消分析'),
                    onError: (err: any) => message.error(err?.response?.data?.detail || '取消失败'),
                  })
                }}
                style={{ marginTop: 16 }}
              >
                取消分析
              </Button>
            </div>
          )}

          {currentAnalysis.analysis_status === 'cancelled' && (
            <Alert
              message="分析已取消"
              description="该分析任务已被手动取消，可点击下方按钮重新分析"
              type="warning"
              showIcon
              action={
                <Button size="small" onClick={handleReAnalyze} loading={analyzeMutation.isPending}>
                  重新分析
                </Button>
              }
            />
          )}

          {currentAnalysis.analysis_status === 'failed' && (
            <Alert
              message="分析失败"
              description={currentAnalysis.error_message || '请点击重新分析按钮重试'}
              type="error"
              showIcon
              action={
                <Button size="small" onClick={handleReAnalyze} loading={analyzeMutation.isPending}>
                  重新分析
                </Button>
              }
            />
          )}
        </div>
      )}
    </Modal>
  )
}
