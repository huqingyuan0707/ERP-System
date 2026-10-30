/** 系统管理-用户接口。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type { User, UserCreatePayload, UserQuery, UserUpdatePayload } from '@/types/user'

export const getUserList = (params: UserQuery): Promise<PageResult<User>> =>
  request({ url: '/sys/users', method: 'get', params })

export const getUserDetail = (id: number): Promise<User> =>
  request({ url: `/sys/users/${id}`, method: 'get' })

export const createUser = (payload: UserCreatePayload): Promise<User> =>
  request({ url: '/sys/users', method: 'post', data: payload })

export const updateUser = (id: number, payload: UserUpdatePayload): Promise<User> =>
  request({ url: `/sys/users/${id}`, method: 'put', data: payload })

export const deleteUser = (id: number): Promise<null> =>
  request({ url: `/sys/users/${id}`, method: 'delete' })

export const resetUserPassword = (id: number, password?: string): Promise<null> =>
  request({ url: `/sys/users/${id}/reset-password`, method: 'post', data: { password } })
