/** 认证接口。 */

import { request } from '@/utils/request'
import type {
  CurrentUser,
  LoginPayload,
  PasswordChangePayload,
  TokenResult,
} from '@/types/auth'

export const login = (payload: LoginPayload): Promise<TokenResult> =>
  request({ url: '/auth/login', method: 'post', data: payload })

export const getCurrentUser = (): Promise<CurrentUser> =>
  request({ url: '/auth/me', method: 'get' })

export const logout = (): Promise<void> =>
  request({ url: '/auth/logout', method: 'post', _silentAuth: true })

export const refreshToken = (): Promise<TokenResult> =>
  request({ url: '/auth/refresh', method: 'post', _silentAuth: true })

export const changePassword = (payload: PasswordChangePayload): Promise<void> =>
  request({ url: '/auth/password', method: 'post', data: payload })
