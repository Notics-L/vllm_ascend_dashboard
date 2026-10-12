import { useEffect, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Avatar,
  Button,
  Drawer,
  Dropdown,
  Layout as AntLayout,
  Menu,
  Space,
  Tag,
  Tooltip,
  message,
} from 'antd'
import {
  AlertOutlined,
  BarChartOutlined,
  BellOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  GithubOutlined,
  LeftOutlined,
  LockOutlined,
  LogoutOutlined,
  MailOutlined,
  MenuOutlined,
  PullRequestOutlined,
  ReadOutlined,
  RightOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'
import { logout } from '../services/auth'
import { useCurrentUser } from '../hooks/useCurrentUser'
import BrandMark from './BrandMark'
import ChangePasswordModal from './ChangePasswordModal'
import './Layout.css'

const { Header, Sider, Content } = AntLayout

type NavigationItem = NonNullable<MenuProps['items']>[number]

const primaryNavigation: NavigationItem[] = [
  { key: '/', icon: <DashboardOutlined />, label: '运营总览' },
  {
    type: 'group',
    label: '研发交付',
    children: [
      { key: '/project', icon: <GithubOutlined />, label: '项目动态' },
      { key: '/contributor-board', icon: <CodeOutlined />, label: '贡献者分析' },
      { key: '/pr-pipeline', icon: <PullRequestOutlined />, label: 'PR 流水线' },
      { key: '/ci', icon: <CheckCircleOutlined />, label: 'CI 运行' },
    ],
  },
  {
    type: 'group',
    label: '质量与模型',
    children: [
      { key: '/test-board', icon: <DashboardOutlined />, label: '测试质量' },
      { key: '/models', icon: <ExperimentOutlined />, label: '模型验证' },
      { key: '/code-metrics', icon: <CodeOutlined />, label: '代码健康' },
    ],
  },
  {
    type: 'group',
    label: '资源与响应',
    children: [
      { key: '/resources', icon: <CloudServerOutlined />, label: '算力资源' },
      { key: '/alert-rules', icon: <BellOutlined />, label: '告警规则' },
      { key: '/issue-diagnosis', icon: <SearchOutlined />, label: 'AI 问题定位' },
      ...(import.meta.env.DEV
        ? [{ key: '/logs', icon: <ReadOutlined />, label: '日志中心' }]
        : []),
    ],
  },
]

const adminNavigation: NavigationItem = {
  type: 'group',
  label: '管理中心',
  children: [
    { key: '/user-stats', icon: <BarChartOutlined />, label: '用户统计' },
    { key: '/admin', icon: <SettingOutlined />, label: '系统配置' },
    { key: '/admin/smtp-config', icon: <MailOutlined />, label: '邮件服务' },
    { key: '/admin/daily-report', icon: <SendOutlined />, label: '每日运行报告' },
  ],
}

const routeMeta = [
  { prefix: '/issue-diagnosis', title: 'AI 问题定位', section: '资源与响应' },
  { prefix: '/code-metrics', title: '代码健康', section: '质量与模型' },
  { prefix: '/pr-pipeline', title: 'PR 流水线', section: '研发交付' },
  { prefix: '/test-board', title: '测试质量', section: '质量与模型' },
  { prefix: '/resources', title: '算力资源', section: '资源与响应' },
  { prefix: '/alert-rules', title: '告警规则', section: '资源与响应' },
  { prefix: '/models', title: '模型验证', section: '质量与模型' },
  { prefix: '/project', title: '项目动态', section: '研发交付' },
  { prefix: '/contributor-board', title: '贡献者分析', section: '研发交付' },
  { prefix: '/ci', title: 'CI 运行', section: '研发交付' },
  { prefix: '/logs', title: '日志中心', section: '资源与响应' },
  { prefix: '/user-stats', title: '用户统计', section: '管理中心' },
  { prefix: '/admin/smtp-config', title: '邮件服务', section: '管理中心' },
  { prefix: '/admin/daily-report', title: '每日运行报告', section: '管理中心' },
  { prefix: '/admin/ci-board-config', title: 'CI 看板配置', section: '管理中心' },
  { prefix: '/admin/model-board-config', title: '模型看板配置', section: '管理中心' },
  { prefix: '/admin/project-board-config', title: '项目看板配置', section: '管理中心' },
  { prefix: '/admin/resource-dashboard-config', title: '资源看板配置', section: '管理中心' },
  { prefix: '/admin', title: '系统配置', section: '管理中心' },
]

function getSelectedNavigation(pathname: string) {
  if (pathname === '/') return '/'
  // 匹配最长前缀，避免 /admin 吞掉所有 /admin/xxx 子路由
  const match = routeMeta
    .filter((item) => pathname.startsWith(item.prefix))
    .sort((a, b) => b.prefix.length - a.prefix.length)[0]
  return match?.prefix || pathname
}

function Layout() {
  const [changePasswordModalOpen, setChangePasswordModalOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('navigation_collapsed') === 'true')
  const navigate = useNavigate()
  const location = useLocation()
  const { data: currentUser } = useCurrentUser()
  const hasAdminRole = currentUser?.role === 'admin' || currentUser?.role === 'super_admin'

  useEffect(() => {
    localStorage.setItem('navigation_collapsed', String(collapsed))
  }, [collapsed])

  const navigationItems = useMemo(
    () => (hasAdminRole ? [...primaryNavigation, adminNavigation] : primaryNavigation),
    [hasAdminRole],
  )
  const selectedKey = getSelectedNavigation(location.pathname)
  const currentPage = routeMeta.find((item) => location.pathname.startsWith(item.prefix)) || {
    title: '运营总览',
    section: '社区运维',
  }

  const handleLogout = async () => {
    try {
      await logout()
    } catch (error) {
      console.error('Logout error:', error)
    } finally {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_info')
      message.success('已安全退出')
      navigate('/login')
    }
  }

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'change-password',
      icon: <LockOutlined />,
      label: '修改密码',
      onClick: () => setChangePasswordModalOpen(true),
    },
    { type: 'divider' },
    { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: handleLogout },
  ]

  const roleLabel: Record<string, string> = {
    super_admin: '超级管理员',
    admin: '管理员',
    user: '社区成员',
  }

  const handleNavigate: MenuProps['onClick'] = ({ key }) => {
    navigate(key)
    setMobileMenuOpen(false)
  }

  return (
    <AntLayout className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <Sider
        className="app-sider"
        width={264}
        collapsedWidth={80}
        collapsed={collapsed}
        trigger={null}
        theme="dark"
      >
        <div className="app-brand">
          <BrandMark title="vLLM Ascend" className="app-brand-logo" />
        </div>

        <nav className="app-nav" aria-label="主导航">
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selectedKey]}
            items={navigationItems}
            onClick={handleNavigate}
            inlineCollapsed={collapsed}
            className="app-menu"
          />
        </nav>

        <div className="app-sider-footer">
          {!collapsed && (
            <div className="environment-status">
              <span className="status-dot" />
              <div>
                <strong>社区服务运行中</strong>
                <span>Production workspace</span>
              </div>
            </div>
          )}
          <Tooltip title={collapsed ? '展开导航' : '收起导航'} placement="right">
            <Button
              type="text"
              icon={collapsed ? <RightOutlined /> : <LeftOutlined />}
              onClick={() => setCollapsed((value) => !value)}
              className="sider-collapse-button"
              aria-label={collapsed ? '展开导航' : '收起导航'}
            />
          </Tooltip>
        </div>
      </Sider>

      <AntLayout className="app-main-layout">
        <Header className="app-header">
          <div className="app-header-left">
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setMobileMenuOpen(true)}
              className="mobile-menu-toggle"
              aria-label="打开导航菜单"
            />
            <div className="mobile-brand">
              <BrandMark />
            </div>
            <div className="page-context">
              <span>{currentPage.section}</span>
              <strong>{currentPage.title}</strong>
            </div>
          </div>

          <Space className="app-header-actions" size={10}>
            <Tooltip title="进入 AI 问题定位">
              <Button
                icon={<AlertOutlined />}
                onClick={() => navigate('/issue-diagnosis')}
                className="header-action-button"
                aria-label="进入 AI 问题定位"
              >
                快速诊断
              </Button>
            </Tooltip>
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight" arrow>
              <Button type="text" className="user-menu-trigger" aria-label="打开用户菜单">
                <Avatar className="user-avatar">
                  {(currentUser?.username || 'U').charAt(0).toUpperCase()}
                </Avatar>
                <span className="user-copy">
                  <strong>{currentUser?.username || '用户'}</strong>
                  <span>{roleLabel[currentUser?.role || 'user'] || currentUser?.role}</span>
                </span>
              </Button>
            </Dropdown>
          </Space>
        </Header>

        <Content id="main-content" className="app-content">
          <Outlet />
        </Content>
      </AntLayout>

      <Drawer
        title={<span className="drawer-title">vLLM Ascend · 导航</span>}
        placement="left"
        width={304}
        onClose={() => setMobileMenuOpen(false)}
        open={mobileMenuOpen}
        className="app-mobile-drawer"
      >
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={navigationItems}
          onClick={handleNavigate}
        />
        <Tag className="drawer-environment-tag" color="success">Production · 服务运行中</Tag>
      </Drawer>

      <ChangePasswordModal
        open={changePasswordModalOpen}
        onClose={() => setChangePasswordModalOpen(false)}
        onSuccess={() => {}}
      />
    </AntLayout>
  )
}

export default Layout
