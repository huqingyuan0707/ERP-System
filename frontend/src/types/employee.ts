/** 基础资料-员工相关类型（B-06，与后端 EmployeeOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'

/** 员工档案（对应后端 EmployeeOut）。 */
export interface Employee {
  id: number
  code: string
  name: string
  deptId: number
  deptName: string | null
  position: string | null
  mobile: string | null
  /** 入职日期 YYYY-MM-DD。 */
  entryDate: string | null
  /** 1 启用、0 停用；停用后新单据不可选。 */
  status: number
  /** 关联的系统账号 id。 */
  userId: number | null
  /** 关联的系统账号登录名。 */
  username: string | null
  remark: string | null
  createdAt: string | null
}

/** 新增员工请求体；编码留空由后端自动生成。 */
export interface EmployeeCreatePayload {
  code?: string | null
  name: string
  deptId: number
  position?: string | null
  mobile?: string | null
  entryDate?: string | null
  status?: number
  userId?: number | null
  remark?: string | null
}

/** 修改员工请求体（部分更新）。 */
export type EmployeeUpdatePayload = Partial<EmployeeCreatePayload>

/** 员工列表查询参数。 */
export interface EmployeeQuery extends PageQuery {
  status?: number | null
  deptId?: number | null
  /** 为真时按所选部门及其全部下级部门筛选。 */
  includeSubDept?: boolean
}

/** 可关联的系统用户选项。 */
export interface UserOption {
  id: number
  username: string
  realName: string | null
  deptId: number | null
  status: number
  /** 已被其他员工关联时的员工 id。 */
  linkedEmployeeId: number | null
}
