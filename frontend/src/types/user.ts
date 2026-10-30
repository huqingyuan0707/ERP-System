/** 系统管理-用户相关类型（与后端 UserOut/UserCreate/UserUpdate/UserQuery 对齐）。 */

import type { PageQuery } from '@/types/common'

export interface User {
  id: number
  username: string
  realName: string | null
  mobile: string | null
  email: string | null
  deptId: number | null
  position: string | null
  status: number
  roleIds: number[]
  lastLoginAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface UserQuery extends PageQuery {
  status?: number | null
  deptId?: number | null
}

export interface UserCreatePayload {
  username: string
  password: string
  realName?: string | null
  mobile?: string | null
  email?: string | null
  deptId?: number | null
  position?: string | null
  status?: number
  roleIds?: number[]
}

export interface UserUpdatePayload {
  realName?: string | null
  mobile?: string | null
  email?: string | null
  deptId?: number | null
  position?: string | null
  status?: number
  roleIds?: number[]
}
