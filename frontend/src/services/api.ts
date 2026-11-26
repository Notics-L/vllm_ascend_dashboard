import axios, { AxiosError, AxiosResponse } from 'axios'
import { appUrl } from '../utils/basePath'

// 获取 API 基础 URL - 使用简单的方式避免 TypeScript 错误
const API_BASE_URL = (typeof import.meta !== 'undefined' &&
  (import.meta as any).env?.VITE_API_BASE_URL) || '/api/v1'

// 创建 axios 实例
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000, // 默认 60 秒超时
  headers: {
    'Content-Type': 'application/json',
  },
  // 生产环境优化：禁用 withCredentials 除非需要
  withCredentials: false,
})

// 创建用于长耗时操作的 axios 实例（如 AI 生成）
export const longTimeoutApiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 600000, // 10 分钟超时，用于 AI 生成等长耗时操作
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: false,
})

for (const client of [apiClient, longTimeoutApiClient]) {
  client.interceptors.request.use((config) => {
    // Breakpoints can pause the entire API worker during local development.
    if (import.meta.env.DEV) config.timeout = 0
    const token = localStorage.getItem('access_token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })
}

// 是否正在刷新 token 的标志
let isRefreshing = false
// 刷新 token 后需要重试的请求队列
let refreshSubscribers: Array<{
  resolve: (token: string) => void
  reject: (error: unknown) => void
}> = []

// 添加到重试队列
const subscribeTokenRefresh = (
  resolve: (token: string) => void,
  reject: (error: unknown) => void,
) => {
  refreshSubscribers.push({ resolve, reject })
}

// 执行重试队列
const onTokenRefreshed = (token: string) => {
  refreshSubscribers.forEach(subscriber => subscriber.resolve(token))
  refreshSubscribers = []
}

const onTokenRefreshFailed = (error: unknown) => {
  refreshSubscribers.forEach(subscriber => subscriber.reject(error))
  refreshSubscribers = []
}

export function credentialsRejected(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false
  return error.response?.status === 401 || error.response?.status === 403
}

/**
 * 服务端暂时不可用：数据库重启、网关 502/503/504、请求超时或网络中断。
 *
 * 这类错误与"登录凭据"无关，绝不能清除 token 或跳转登录页；页面应当自动重试。
 */
export function serviceUnavailable(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false
  // 无响应：网络中断、超时、后端进程未就绪
  if (!error.response) return true
  return [502, 503, 504].includes(error.response.status)
}

// 响应拦截器 - 处理错误和 token 刷新
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError<{ detail?: string }>) => {
    const originalRequest = error.config as any

    if (error.response?.status === 401 && !originalRequest._retry) {
      // 如果是重复刷新 token 的请求，或者没有 refresh token，跳转登录
      if (originalRequest.url?.includes('/auth/refresh')) {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        if (!window.location.pathname.endsWith('/login')) {
          window.location.href = appUrl('/login')
        }
        return Promise.reject(error)
      }

      // 检查是否有 refresh token，没有则不自动刷新（允许公开访问的 API 返回 401）
      const refreshToken = localStorage.getItem('refresh_token')
      if (!refreshToken) {
        return Promise.reject(error)
      }

      originalRequest._retry = true

      // 如果正在刷新 token，将请求加入队列
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          subscribeTokenRefresh(
            (token: string) => {
              originalRequest.headers.Authorization = `Bearer ${token}`
              resolve(apiClient(originalRequest))
            },
            reject,
          )
        })
      }

      isRefreshing = true

      try {
        // 使用刷新 token 获取新的 access token
        const response = await axios.post(
          `${API_BASE_URL}/auth/refresh`,
          {},
          {
            headers: {
              Authorization: `Bearer ${refreshToken}`,
            },
          }
        )

        const { access_token, refresh_token: new_refresh_token } = response.data

        // 保存新的 token
        localStorage.setItem('access_token', access_token)
        localStorage.setItem('refresh_token', new_refresh_token)

        // 执行重试队列
        onTokenRefreshed(access_token)

        // 重试原请求
        originalRequest.headers.Authorization = `Bearer ${access_token}`
        return apiClient(originalRequest)
      } catch (refreshError) {
        onTokenRefreshFailed(refreshError)
        if (credentialsRejected(refreshError)) {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          if (!window.location.pathname.endsWith('/login')) {
            window.location.href = appUrl('/login')
          }
        }
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    // 5xx 错误记录详细日志
    if (error.response?.status && error.response.status >= 500) {
      console.error('Server error:', {
        status: error.response.status,
        statusText: error.response.statusText,
        data: error.response.data,
      })
    }

    return Promise.reject(error)
  }
)

export default apiClient
