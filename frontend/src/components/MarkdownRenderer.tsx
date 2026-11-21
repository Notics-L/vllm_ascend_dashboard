import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownRendererProps {
  content: string
  skipHtml?: boolean
  maxHeight?: number
  style?: React.CSSProperties
}

const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  skipHtml = false,
  maxHeight,
  style,
}) => {
  return (
    <div
      style={{
        ...style,
        ...(maxHeight ? { maxHeight, overflowY: 'auto' } : {}),
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml={skipHtml}
        components={{
          h1: ({ node: _node, ...props }) => <h1 style={{ fontSize: 20, fontWeight: 700, margin: '16px 0 8px' }} {...props} />,
          h2: ({ node: _node, ...props }) => <h2 style={{ fontSize: 18, fontWeight: 700, margin: '14px 0 8px', borderBottom: '1px solid #f0f0f0', paddingBottom: 6 }} {...props} />,
          h3: ({ node: _node, ...props }) => <h3 style={{ fontSize: 15, fontWeight: 600, margin: '12px 0 6px', color: '#1890ff' }} {...props} />,
          h4: ({ node: _node, ...props }) => <h4 style={{ fontSize: 14, fontWeight: 600, margin: '10px 0 4px' }} {...props} />,
          p: ({ node: _node, ...props }) => <p style={{ margin: '6px 0', lineHeight: 1.8, fontSize: 13 }} {...props} />,
          ul: ({ node: _node, ...props }) => <ul style={{ margin: '6px 0', paddingLeft: 20, fontSize: 13 }} {...props} />,
          ol: ({ node: _node, ...props }) => <ol style={{ margin: '6px 0', paddingLeft: 20, fontSize: 13 }} {...props} />,
          li: ({ node: _node, ...props }) => <li style={{ margin: '3px 0', lineHeight: 1.7 }} {...props} />,
          blockquote: ({ node: _node, ...props }) => (
            <blockquote style={{ margin: '10px 0', padding: '8px 16px', borderLeft: '3px solid #d9d9d9', background: '#fafafa', color: '#555', fontSize: 13 }} {...props} />
          ),
          table: ({ node: _node, ...props }) => (
            <div style={{ overflowX: 'auto', margin: '10px 0' }}>
              <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }} {...props} />
            </div>
          ),
          th: ({ node: _node, ...props }) => (
            <th style={{ border: '1px solid #d9d9d9', padding: '6px 10px', background: '#f5f5f5', textAlign: 'left', fontSize: 12, fontWeight: 600 }} {...props} />
          ),
          td: ({ node: _node, ...props }) => (
            <td style={{ border: '1px solid #d9d9d9', padding: '6px 10px', verticalAlign: 'top', fontSize: 12 }} {...props} />
          ),
          code: ({ node: _node, ...props }) => (
            <code style={{ background: '#f5f5f5', padding: '1px 4px', borderRadius: 3, fontSize: 12, fontFamily: 'monospace' }} {...props} />
          ),
          pre: ({ node: _node, ...props }) => (
            <pre style={{ background: '#f5f5f5', padding: '10px 14px', borderRadius: 4, overflowX: 'auto', marginBottom: 10, fontSize: 12 }} {...props} />
          ),
          strong: ({ node: _node, ...props }) => <strong style={{ fontWeight: 600 }} {...props} />,
          a: ({ node: _node, ...props }) => <a style={{ color: '#1890ff' }} target="_blank" rel="noopener noreferrer" {...props} />,
          hr: () => <hr style={{ margin: '16px 0', border: 'none', borderTop: '1px solid #f0f0f0' }} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

export default MarkdownRenderer
