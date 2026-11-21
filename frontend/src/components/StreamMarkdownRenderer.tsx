import React, { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { DiagnosisMessage } from '../services/issueDiagnosis'

interface DiagnosisSummary {
  total_content_length: number
  duration_seconds: number
  chunk_count: number
  finish_reason?: string | null
  continuation_count?: number
}

interface StreamMarkdownRendererProps {
  content: string
  messages?: DiagnosisMessage[]
  isStreaming: boolean
  meta?: { provider: string; model: string } | null
  summary?: DiagnosisSummary | null
}

export type { DiagnosisSummary }

const StreamMarkdownRenderer: React.FC<StreamMarkdownRendererProps> = ({
  content,
  messages,
  isStreaming,
  meta,
  summary,
}) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true)
  const displayMessages: DiagnosisMessage[] = messages?.length
    ? messages
    : content
      ? [{ role: 'assistant', content }]
      : []
  const contentSignature = displayMessages.map(item => item.content).join('\u0000')

  useEffect(() => {
    if (shouldAutoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [contentSignature, shouldAutoScroll])

  const handleScroll = () => {
    if (!containerRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 100
    setShouldAutoScroll(isNearBottom)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {(meta || summary || isStreaming) && (
        <div style={{
          padding: '8px 12px',
          background: '#f0f0f0',
          borderRadius: '4px',
          marginBottom: 8,
          fontSize: 12,
          color: '#666',
          display: 'flex',
          justifyContent: 'space-between',
        }}>
          {meta && (
            <span>
              模型: {meta.provider}/{meta.model}
            </span>
          )}
          {summary && (
            <span>
              耗时: {summary.duration_seconds}s | 输出: {summary.total_content_length}字符
            </span>
          )}
          {isStreaming && !summary && (
            <span style={{ color: '#1890ff' }}>生成中...</span>
          )}
        </div>
      )}

      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          padding: 16,
          background: '#fafafa',
          borderRadius: 8,
          overflowY: 'auto',
          minHeight: 200,
        }}
      >
        {displayMessages.length ? (
          displayMessages.map((item, index) => {
            const isUser = item.role === 'user'
            const isActiveAssistant = isStreaming
              && item.role === 'assistant'
              && index === displayMessages.length - 1
            return (
              <div
                key={`${item.role}-${index}`}
                style={{
                  marginBottom: 16,
                  marginLeft: isUser ? '15%' : 0,
                  padding: '12px 16px',
                  borderRadius: 8,
                  background: isUser ? '#e6f4ff' : '#fff',
                  border: `1px solid ${isUser ? '#91caff' : '#e8e8e8'}`,
                }}
              >
                <div style={{ color: '#1677ff', fontWeight: 600, marginBottom: 8 }}>
                  {isUser ? '追问' : 'AI 分析'}
                </div>
                {item.content ? (
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
                    {item.content}
                  </ReactMarkdown>
                ) : (
                  <span style={{ color: '#8c8c8c' }}>
                    {isActiveAssistant ? '正在生成分析结果...' : '本轮未生成回答'}
                  </span>
                )}
                {isActiveAssistant && (
                  <span style={{ color: '#1890ff', fontWeight: 'bold' }}>▊</span>
                )}
              </div>
            )
          })
        ) : (
          <div style={{ textAlign: 'center', padding: 40, color: '#8c8c8c' }}>
            {isStreaming ? '正在生成分析结果...' : '点击"开始诊断"按钮进行问题定位'}
          </div>
        )}
      </div>
    </div>
  )
}

export default StreamMarkdownRenderer
