import { useState } from 'react'
import { Tabs } from 'antd'
import { useCurrentUser } from '../../../hooks/useCurrentUser'
import DailyCasePage from '../cases/DailyCasePage'
import DailyTriggerSettings from '../trigger'
import NotificationConfig from './NotificationConfig'
import '../shared/DailyStableTabs.css'

export default function DailyPipelineConfiguration() {
  const [activeTab, setActiveTab] = useState('trigger')
  const { data: user } = useCurrentUser()
  const canEdit = user?.role === 'admin' || user?.role === 'super_admin'
  return <DailyCasePage title="配置">
    <Tabs className="daily-stable-tabs" activeKey={activeTab} onChange={setActiveTab} animated={false} items={[
      { key: 'trigger', label: '流水线触发', forceRender: true, children: <DailyTriggerSettings active={activeTab === 'trigger'} /> },
      { key: 'notification', label: '消息通知', forceRender: true, children: <NotificationConfig active={activeTab === 'notification'} canEdit={canEdit} /> },
    ]} />
  </DailyCasePage>
}
