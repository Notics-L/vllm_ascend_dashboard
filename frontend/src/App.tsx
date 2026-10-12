import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider, Spin } from 'antd'
import zhCN from 'antd/locale/zh_CN'

import Login from './pages/Login'
import Landing from './pages/Landing'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import CIBoard from './pages/CIBoard'
import WorkflowDetail from './pages/WorkflowDetail'
import JobDetail from './pages/JobDetail'
import JobRuns from './pages/JobRuns'
import CIDailyReport from './pages/CIDailyReport'
import Admin from './pages/Admin'
import Models from './pages/Models'
import ModelDetail from './pages/ModelDetail'
import ModelDailyReport from './pages/ModelDailyReport'
import ModelBoardConfig from './pages/ModelBoardConfig'
import CIBoardConfig from './pages/CIBoardConfig'
import GitHubActivityDetail from './pages/GitHubActivityDetail'
import CommitAnalysisDetail from './pages/CommitAnalysisDetail'
import ProjectBoard from './pages/ProjectBoard'
import ContributorBoard, { ContributorDetailPage } from './pages/ContributorBoard'
import ProjectBoardConfig from './pages/ProjectBoardConfig'
import PRPipelineBoard from './pages/PRPipelineBoard'
import PRDetail from './pages/PRDetail'
import ResourceDashboard from './pages/ResourceDashboard'
import ResourceDashboardConfig from './pages/ResourceDashboardConfig'
import DailyReportConfigPage from './pages/DailyReportConfig'
import SmtpConfig from './pages/SmtpConfig'
import AlertRulesManagement from './pages/AlertRulesManagement'
import IssueDiagnosis from './pages/IssueDiagnosis'
import CodeMetricsBoard from './pages/CodeMetricsBoard'
import Register from './pages/Register'
import UserStats from './pages/UserStats'
import TestObservabilityDashboard from './pages/TestObservabilityDashboard'
import LogCenter from './pages/LogCenter'
import ShareReport from './pages/ShareReport'
import { useCurrentUser } from './hooks/useCurrentUser'
import { appBasePath } from './utils/basePath'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    },
  },
})

// 需要登录的路由保护组件
function ProtectedRoute({ children, allowLanding = false }: { children: React.ReactNode; allowLanding?: boolean }) {
  const token = localStorage.getItem('access_token')
  const location = useLocation()
  const { data: currentUser, isLoading, error } = useCurrentUser()

  // 首先检查 token 是否存在
  if (!token) {
    if (allowLanding && location.pathname === '/') return <Landing />
    return <Navigate to="/login" replace />
  }

  // 使用 React Query 获取最新用户信息
  // 加载中，显示加载状态
  if (isLoading) {
    return (
      <div className="route-loading-state">
        <Spin size="large" />
        <span>正在进入社区工作台…</span>
      </div>
    )
  }

  // 如果获取用户信息失败（可能是 token 过期），重定向到登录页
  if (error || !currentUser) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// 仅管理员路由需要登录和权限
function AdminRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('access_token')
  const { data: currentUser, isLoading, error } = useCurrentUser()

  // 首先检查 token 是否存在
  if (!token) {
    return <Navigate to="/login" replace />
  }

  // 使用 React Query 获取最新用户信息
  // 加载中，显示加载状态
  if (isLoading) {
    return (
      <div className="route-loading-state">
        <Spin size="large" />
        <span>正在验证管理权限…</span>
      </div>
    )
  }

  // 如果获取用户信息失败（可能是 token 过期），重定向到登录页
  if (error || !currentUser) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    return <Navigate to="/login" replace />
  }

  // 检查用户是否为管理员
  if (currentUser.role !== 'admin' && currentUser.role !== 'super_admin') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: '#5b46f5',
            colorInfo: '#5b46f5',
            colorSuccess: '#16a34a',
            colorWarning: '#d97706',
            colorError: '#dc2626',
            colorText: '#10213a',
            colorTextSecondary: '#52627a',
            colorBorder: '#dfe6ef',
            colorBgLayout: '#f6f8fc',
            borderRadius: 10,
            controlHeight: 38,
            fontFamily: "'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          },
          components: {
            Button: { fontWeight: 600, primaryShadow: '0 8px 20px rgba(91, 70, 245, 0.2)' },
            Card: { headerBg: '#ffffff' },
            Table: { headerBg: '#f7f9fc', headerColor: '#52627a' },
            Menu: { itemBorderRadius: 9 },
          },
        }}
      >
        <BrowserRouter basename={appBasePath}>
          <Routes>
            {/* 公开分享页面 */}
            <Route path="/share/:token" element={<ShareReport />} />
            {/* 登录页面 */}
            <Route path="/login" element={<Login />} />
            {/* 注册页面 */}
            <Route path="/register" element={<Register />} />

            {/* 需要登录的路由（默认） */}
            <Route path="/" element={
              <ProtectedRoute allowLanding>
                <Layout />
              </ProtectedRoute>
            }>
              <Route index element={<Dashboard />} />
              <Route path="ci" element={<CIBoard />} />
              {/* Project Dashboard */}
              <Route path="project" element={<ProjectBoard />} />
              {/* Contributor Board */}
              <Route path="contributor-board" element={<ContributorBoard />} />
              <Route path="contributor-board/detail/:login" element={<ContributorDetailPage />} />
              {/* CI 详情页面 */}
              <Route path="ci/runs/:runId" element={<WorkflowDetail />} />
              <Route path="ci/jobs/:jobId" element={<JobDetail />} />
              <Route path="ci/jobs/:workflowName/:jobName" element={<JobRuns />} />
              {/* CI 每日报告页面 */}
              <Route path="ci/reports/:date" element={<CIDailyReport />} />
              {/* 模型管理页面 */}
              <Route path="models" element={<Models />} />
              <Route path="models/:id" element={<ModelDetail />} />
              <Route path="resources" element={<ResourceDashboard />} />
              {/* 模型每日报告页面 */}
              <Route path="models/reports/:date" element={<ModelDailyReport />} />
              {/* 告警规则 */}
              <Route path="alert-rules" element={<AlertRulesManagement />} />
              {/* 问题定位（所有用户） */}
              <Route
                path="issue-diagnosis"
                element={<IssueDiagnosis />}
              />
              {/* 代码度量（所有用户） */}
              <Route
                path="code-metrics"
                element={<CodeMetricsBoard />}
              />
              {/* 用户统计（管理员） */}
              <Route
                path="user-stats"
                element={
                  <AdminRoute>
                    <UserStats />
                  </AdminRoute>
                }
              />
              {/* GitHub 动态详情页面 */}
              <Route path="github-activity/:project" element={<GitHubActivityDetail />} />
              <Route path="github-activity/:project/commits/:sha" element={<CommitAnalysisDetail />} />
              {/* PR Pipeline Kanban */}
              <Route path="pr-pipeline" element={<PRPipelineBoard />} />
              <Route path="pr-pipeline/:prNumber" element={<PRDetail />} />
              {/* Test Observability Dashboard */}
              <Route path="test-board" element={<TestObservabilityDashboard />} />
              {/* 日志中心（仅开发模式） */}
              {import.meta.env.DEV && <Route path="logs" element={<LogCenter />} />}

              {/* 仅管理员访问的路由 */}
              <Route
                path="admin"
                element={
                  <AdminRoute>
                    <Admin />
                  </AdminRoute>
                }
              />
              {/* CI 看板配置（管理员） */}
              <Route
                path="admin/ci-board-config"
                element={
                  <AdminRoute>
                    <CIBoardConfig />
                  </AdminRoute>
                }
              />
              {/* 模型看板配置（管理员） */}
              <Route
                path="admin/model-board-config"
                element={
                  <AdminRoute>
                    <ModelBoardConfig />
                  </AdminRoute>
                }
              />
              {/* 项目看板配置（管理员） */}
              <Route
                path="admin/project-board-config"
                element={
                  <AdminRoute>
                    <ProjectBoardConfig />
                  </AdminRoute>
                }
              />
              {/* 资源看板配置（管理员） */}
              <Route
                path="admin/resource-dashboard-config"
                element={
                  <AdminRoute>
                    <ResourceDashboardConfig />
                  </AdminRoute>
                }
              />
              {/* SMTP 邮件服务器（管理员） */}
              <Route
                path="admin/smtp-config"
                element={
                  <AdminRoute>
                    <SmtpConfig />
                  </AdminRoute>
                }
              />
              {/* 每日运行报告（管理员） */}
              <Route
                path="admin/daily-report"
                element={
                  <AdminRoute>
                    <DailyReportConfigPage />
                  </AdminRoute>
                }
              />
            </Route>

            {/* 404 重定向 */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  )
}

export default App
