/** 角色管理接口。 */

import { request } from '@/utils/request'
import type { DataScopePayload, Role, RoleCreatePayload, RoleUpdatePayload } from '@/types/role'

export const getRoleList = (): Promise<Role[]> =>
  request({ url: '/sys/roles', method: 'get' })

export const createRole = (payload: RoleCreatePayload): Promise<Role> =>
  request({ url: '/sys/roles', method: 'post', data: payload })

export const updateRole = (id: number, payload: RoleUpdatePayload): Promise<Role> =>
  request({ url: `/sys/roles/${id}`, method: 'put', data: payload })

export const deleteRole = (id: number): Promise<null> =>
  request({ url: `/sys/roles/${id}`, method: 'delete' })

export const assignRoleDataScope = (id: number, payload: DataScopePayload): Promise<null> =>
  request({ url: `/sys/roles/${id}/data-scope`, method: 'put', data: payload })
