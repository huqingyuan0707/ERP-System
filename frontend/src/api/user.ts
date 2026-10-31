/**
 * 系统管理-用户接口。
 *
 * 约定：
 *  - 路径前缀 /api/v1 由 request.ts 的 baseURL 统一拼接，此处只写相对路径；
 *  - 请求体字段为 camelCase，与后端 Pydantic alias 一致；
 *  - 所有方法返回 Promise<T>，T 为后端 data 字段的实际结构（已完成解包）。
 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type { User, UserCreatePayload, UserQuery, UserUpdatePayload } from '@/types/user'

/** 分页查询用户列表，支持关键字（用户名/姓名/手机号）、状态、部门筛选。 */
export const getUserList = (params: UserQuery): Promise<PageResult<User>> =>
  request({ url: '/sys/users', method: 'get', params })

/** 查询用户详情，含已分配角色 id 列表与最后登录时间。 */
export const getUserDetail = (id: number): Promise<User> =>
  request({ url: `/sys/users/${id}`, method: 'get' })

/** 新增用户；用户名重复返回 409，部门/角色不存在返回 422。 */
export const createUser = (payload: UserCreatePayload): Promise<User> =>
  request({ url: '/sys/users', method: 'post', data: payload })

/** 修改用户（部分更新）；后端禁止禁用当前登录用户与内置 admin，返回 400。 */
export const updateUser = (id: number, payload: UserUpdatePayload): Promise<User> =>
  request({ url: `/sys/users/${id}`, method: 'put', data: payload })

/** 删除用户：后端为软删除，记录保留以追溯历史单据的 created_by。 */
export const deleteUser = (id: number): Promise<null> =>
  request({ url: `/sys/users/${id}`, method: 'delete' })

/**
 * 重置用户密码。
 *
 * @param id       目标用户 id
 * @param password 新密码；不传则后端重置为默认密码 123456
 *
 * 后端会递增该用户的 token_version，使旧 Token 全部失效。
 */
export const resetUserPassword = (id: number, password?: string): Promise<null> =>
  request({ url: `/sys/users/${id}/reset-password`, method: 'post', data: { password } })
