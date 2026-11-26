import { useQuery } from '@tanstack/react-query'
import { getCurrentUser } from '../services/auth'
import { credentialsRejected, serviceUnavailable } from '../services/api'

export const useCurrentUser = () => {
  const isLoggedIn = !!localStorage.getItem('access_token')

  return useQuery({
    queryKey: ['current-user'],
    queryFn: getCurrentUser,
    enabled: isLoggedIn,
    staleTime: 0, // 总是视为过期，确保切换用户后立即刷新
    // 凭据被拒（401/403）时重试没有意义；数据库/网关短暂不可用时才值得重试。
    retry: (failureCount, error) => {
      if (credentialsRejected(error)) return false
      if (failureCount >= 3) return false
      return serviceUnavailable(error) || failureCount < 1
    },
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 5000),
  })
}
