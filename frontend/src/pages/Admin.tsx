import { Tabs } from 'antd'
import { useHasAdminRole, useHasSuperAdminRole } from '../hooks/useRoles'
import UserManagement from './UserManagement'
import SystemConfig from './SystemConfig'
import CIBoardConfig from './CIBoardConfig'
import ModelBoardConfig from './ModelBoardConfig'
import ProjectBoardConfig from './ProjectBoardConfig'
import ResourceDashboardConfig from './ResourceDashboardConfig'
import WorkflowActionPermissions from '../components/WorkflowActionPermissions'

function Admin() {
  const hasAdminRole = useHasAdminRole()
  const hasSuperAdminRole = useHasSuperAdminRole()

  // 如果还没有加载完用户信息，显示加载中
  if (hasAdminRole === undefined) {
    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <div>加载中...</div>
      </div>
    )
  }

  // 如果没有权限，显示错误提示
  if (hasAdminRole === false) {
    return (
      <div style={{ padding: 24 }}>
        <div style={{
          textAlign: 'center',
          padding: '40px 0',
          color: '#999'
        }}>
          <h2>⛔ 权限不足</h2>
          <p>您需要管理员角色才能访问此页面</p>
          <p style={{ fontSize: 14, marginTop: 16 }}>
            当前角色：普通用户 | 所需角色：管理员或超级管理员
          </p>
        </div>
      </div>
    )
  }

  // 构建 Tab 列表
  const tabItems: any[] = []

  // 只有超级管理员才能访问系统配置
  if (hasSuperAdminRole) {
    tabItems.push({
      key: 'config',
      label: '系统配置',
      children: <SystemConfig />,
    })
  }

  // 管理员和超级管理员都可以访问其他配置页面
  tabItems.push(
    {
      key: 'project-board',
      label: '项目看板配置',
      children: <ProjectBoardConfig />,
    },
    {
      key: 'ci-board',
      label: 'CI 看板配置',
      children: <CIBoardConfig />,
    },
    {
      key: 'model-board',
      label: '模型看板配置',
      children: <ModelBoardConfig />,
    },
    {
      key: 'resource-dashboard',
      label: '资源看板配置',
      children: <ResourceDashboardConfig />,
    },
    {
      key: 'workflow-actions',
      label: 'Actions 权限',
      children: <WorkflowActionPermissions />,
    },
  )

  // 只有超级管理员才能访问用户管理
  if (hasSuperAdminRole) {
    tabItems.push({
      key: 'users',
      label: '用户管理',
      children: <UserManagement />,
    })
  }

  return (
    <div className="stripe-page-container">
      <Tabs
        defaultActiveKey="config"
        items={tabItems}
        className="stripe-page-tabs"
      />
    </div>
  )
}

export default Admin
