/** 角色相关类型（与后端 RoleOut/RoleCreate/RoleUpdate、DataScopeAssign 对齐）。 */

/** 数据范围：1 仅本人 / 2 本部门 / 3 全部。 */
export const DATA_SCOPE_SELF = 1
export const DATA_SCOPE_DEPT = 2
export const DATA_SCOPE_ALL = 3

export interface Role {
  id: number
  code: string
  name: string
  dataScope: number
  status: number
  remark: string | null
  deptIds: number[]
}

export interface RoleCreatePayload {
  code: string
  name: string
  dataScope?: number
  remark?: string | null
}

export interface RoleUpdatePayload {
  name?: string
  remark?: string | null
}

export interface DataScopePayload {
  dataScope: number
  deptIds: number[]
}
