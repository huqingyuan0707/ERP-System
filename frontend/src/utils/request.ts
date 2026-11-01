/** Axios 统一封装：注入 token、解包 {code,message,data}、错误提示、401 单飞刷新并重放。 */

import axios, { type AxiosError, type AxiosInstance, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import type { TokenResult } from '@/types/auth'
import type { ApiResponse } from '@/types/common'

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 静默 401：不刷新、不提示、不跳转（内部刷新 / 登出请求使用）。 */
    _silentAuth?: boolean
    /** 重放标记：刷新成功后重放的请求再遇 401 直接跳登录，避免循环刷新。 */
    _retried?: boolean
  }
}

const TOKEN_KEY = 'erp_access_token'
const TENANT_KEY = 'erp_tenant_id'
const LOGIN_URL = '/auth/login'
const REFRESH_URL = '/auth/refresh'

export const getToken = (): string => sessionStorage.getItem(TOKEN_KEY) ?? ''
export const setToken = (token: string): void => sessionStorage.setItem(TOKEN_KEY, token)
export const clearToken = (): void => sessionStorage.removeItem(TOKEN_KEY)
export const getTenantId = (): string | null => sessionStorage.getItem(TENANT_KEY) ?? null

const http: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 15000,
  withCredentials: true,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const tenantId = getTenantId()
  if (tenantId) {
    config.headers['X-Tenant-Id'] = tenantId
  }
  return config
})

const redirectToLogin = (): void => {
  clearToken()
  if (window.location.pathname !== '/login') {
    window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`
  }
}

/** 单飞刷新：并发 401 共享同一次 /auth/refresh 请求，成功返回新 token，失败返回 null。 */
let refreshPromise: Promise<string | null> | null = null

const refreshAccessToken = (): Promise<string | null> => {
  refreshPromise ??= (async (): Promise<string | null> => {
    try {
      const response = await http.post<ApiResponse<TokenResult>>(REFRESH_URL, null, {
        _silentAuth: true,
      })
      const accessToken = response.data.data.accessToken
      if (!accessToken) {
        return null
      }
      setToken(accessToken)
      return accessToken
    } catch {
      return null
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

const isAuthUrl = (url: string): boolean => url === LOGIN_URL || url === REFRESH_URL

http.interceptors.response.use(
  (response) => {
    // blob 响应（文件下载）直接透传
    if (response.config.responseType === 'blob') {
      return response
    }
    const body = response.data as ApiResponse<unknown>
    if (body.code !== 0) {
      if (!response.config._silentAuth) {
        ElMessage.error(body.message || '请求失败')
      }
      return Promise.reject(new Error(body.message))
    }
    return response
  },
  async (error: AxiosError<ApiResponse<unknown>>) => {
    const config = error.config
    const url = config?.url ?? ''
    const status = error.response?.status

    // 401 且非认证接口：单飞刷新一次，成功后携带新 token 重放原请求
    if (status === 401 && config && !isAuthUrl(url) && !config._retried && !config._silentAuth) {
      const newToken = await refreshAccessToken()
      if (newToken) {
        config.headers.Authorization = `Bearer ${newToken}`
        config._retried = true
        return http.request(config)
      }
      redirectToLogin()
      ElMessage.error('登录已过期，请重新登录')
      return Promise.reject(error)
    }

    if (status === 401 && !config?._silentAuth) {
      redirectToLogin()
      ElMessage.error('登录已过期，请重新登录')
    } else if (status === 403) {
      ElMessage.error('没有权限执行该操作')
    } else if (status !== 401) {
      ElMessage.error(error.response?.data?.message || error.message || '网络异常')
    }
    return Promise.reject(error)
  },
)

/** 统一解包 data 的请求方法。 */
export const request = async <T>(config: AxiosRequestConfig): Promise<T> => {
  const response = await http.request<ApiResponse<T>>(config)
  return response.data.data
}

/** 下载文件（blob），保留文件名解析。 */
export const download = async (
  url: string,
  params: Record<string, unknown>,
  fallbackName: string,
): Promise<void> => {
  const response = await http.get(url, { params, responseType: 'blob' })
  const disposition = response.headers['content-disposition'] as string | undefined
  let filename = fallbackName
  if (disposition) {
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
    if (match?.[1]) filename = decodeURIComponent(match[1])
  }
  const blobUrl = URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  link.click()
  URL.revokeObjectURL(blobUrl)
}

export default http
