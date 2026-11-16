import type { ReactNode } from 'react'
import { Button, ConfigProvider, Space, Typography } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { ProCard } from '@ant-design/pro-components'
import './DailyCases.css'

interface DailyCasePageProps {
  title?: string
  saving?: boolean
  onBack?: () => void
  actions?: ReactNode
  children: ReactNode
}

function DailyCasePage({ title, saving = false, onBack, actions, children }: DailyCasePageProps) {
  return (
    <ConfigProvider theme={{
      token: {
        colorPrimary: '#1677ff', borderRadius: 6, controlHeight: 32, fontSize: 13,
        colorText: '#10213a', colorTextSecondary: '#334155', colorTextTertiary: '#475569',
        colorTextQuaternary: '#64748b', colorTextDisabled: '#64748b', colorTextPlaceholder: '#64748b',
      },
      components: {
        Button: { fontWeight: 400, primaryShadow: 'none' },
        Select: { optionFontSize: 13, optionSelectedFontWeight: 400 },
      },
    }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {(title || onBack || actions) && <div className="daily-case-page-header">
          <Space className="daily-cases-actions" size={12}>
            {onBack && <Button icon={<ArrowLeftOutlined />} onClick={onBack} disabled={saving}>返回</Button>}
            {title && <Typography.Text strong>{title}</Typography.Text>}
          </Space>
          {actions && <Space className="daily-cases-actions" size={8}>{actions}</Space>}
        </div>}
        <ProCard>{children}</ProCard>
      </Space>
    </ConfigProvider>
  )
}

export default DailyCasePage
