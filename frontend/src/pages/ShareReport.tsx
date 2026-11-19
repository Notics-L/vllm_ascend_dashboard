import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { Spin, Alert, Typography, Button, Space, Tag, Divider, Descriptions } from 'antd'
import { DownloadOutlined, ShareAltOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const { Title, Text } = Typography

export default function ShareReport() {
  const { token } = useParams<{ token: string }>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [analysis, setAnalysis] = useState<any>(null)
  const [report, setReport] = useState('')

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const resp = await fetch(`/api/v1/ci/public/analysis/${token}`)
      if (!resp.ok) throw new Error('报告不存在或已过期')
      const data = await resp.json()
      setAnalysis(data)
      if (data.report_file_path) {
        const r = await fetch(`/api/v1/ci/public/analysis/${token}/report`)
        if (r.ok) {
          const rd = await r.json()
          setReport(rd.content || '')
        }
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => { load() }, [load])

  const handlePrint = () => {
    window.print()
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" tip="加载中..." /></div>
  if (error) return <Alert message="错误" description={error} type="error" showIcon style={{ margin: 40 }} />

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '40px 20px' }}>
      <Space style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>CI 失败分析报告</Title>
        {analysis?.problem_category && <Tag color="orange">{analysis.problem_category}</Tag>}
      </Space>

      <Descriptions column={2} bordered size="small" style={{ marginBottom: 16 }}>
        <Descriptions.Item label="Workflow">{analysis?.workflow_name || '-'}</Descriptions.Item>
        <Descriptions.Item label="Job ID">{analysis?.job_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="分类">
          <Tag color="orange">{analysis?.problem_category || '-'}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color="green">{analysis?.analysis_status === 'completed' ? '已完成' : analysis?.analysis_status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="根因摘要" span={2}>{analysis?.root_cause_summary || '-'}</Descriptions.Item>
        <Descriptions.Item label="改进建议" span={2}>{analysis?.improvement_measures_summary || '-'}</Descriptions.Item>
        {analysis?.llm_provider && (
          <Descriptions.Item label="LLM">{analysis.llm_provider}/{analysis.llm_model || '-'}</Descriptions.Item>
        )}
        {analysis?.generation_time_seconds && (
          <Descriptions.Item label="耗时">{analysis.generation_time_seconds.toFixed(1)}s</Descriptions.Item>
        )}
        {analysis?.error_message && (
          <Descriptions.Item label="错误信息" span={2}><Text type="danger">{analysis.error_message}</Text></Descriptions.Item>
        )}
      </Descriptions>

      <Space style={{ marginBottom: 24 }}>
        <Button type="primary" icon={<DownloadOutlined />} onClick={() => { window.location.href = `/api/v1/ci/public/analysis/${token}/pdf` }}>下载 PDF</Button>
        <Button icon={<ShareAltOutlined />} onClick={() => {
          const url = window.location.href
          if (navigator.clipboard) {
            navigator.clipboard.writeText(url).catch(() => { const i=document.createElement('input'); i.value=url; document.body.appendChild(i); i.select(); document.execCommand('copy'); i.remove() })
          } else {
            const i=document.createElement('input'); i.value=url; document.body.appendChild(i); i.select(); document.execCommand('copy'); i.remove()
          }
        }}>复制链接</Button>
        <Button onClick={() => { window.location.href = `/api/v1/ci/public/analysis/${token}/report?download=1` }}>下载报告(.md)</Button>
      </Space>

      <Divider />

      {report ? (
        <div style={{ padding: 20, background: '#fafafa', borderRadius: 8, color: '#333', fontSize: 14, lineHeight: 1.8 }}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              table: ({ node: _node, ...props }: any) => (
                <div style={{ overflowX: 'auto', marginBottom: 16 }}>
                  <table style={{ borderCollapse: 'collapse', width: '100%' }} {...props} />
                </div>
              ),
              th: ({ node: _node, ...props }: any) => (
                <th style={{ border: '1px solid #d9d9d9', padding: '8px 12px', background: '#f5f5f5', textAlign: 'left' }} {...props} />
              ),
              td: ({ node: _node, ...props }: any) => (
                <td style={{ border: '1px solid #d9d9d9', padding: '8px 12px', verticalAlign: 'top' }} {...props} />
              ),
            }}
          >
            {report}
          </ReactMarkdown>
        </div>
      ) : (
        <div style={{ padding: 20 }}>
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="分类">{analysis?.problem_category || '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">{analysis?.analysis_status || '-'}</Descriptions.Item>
            <Descriptions.Item label="根因摘要" span={2}>{analysis?.root_cause_summary || '-'}</Descriptions.Item>
            <Descriptions.Item label="改进建议" span={2}>{analysis?.improvement_measures_summary || '-'}</Descriptions.Item>
          </Descriptions>
        </div>
      )}

      <div style={{ textAlign: 'center', marginTop: 40, color: '#999', fontSize: 12 }}>
        Powered by vLLM Ascend Dashboard
      </div>
    </div>
  )
}
