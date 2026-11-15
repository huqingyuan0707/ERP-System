/** 基础资料-部门相关类型（B-06，与后端 DeptOut/Create/Update 对齐）。 */

/** 部门节点（扁平列表，前端按 parentId 组装树）。 */
export interface Dept {
  id: number
  name: string
  parentId: number | null
  leader: string | null
  sort: number
  /** 1 启用、0 停用。 */
  status: number
  remark: string | null
  /** 子部门数，>0 时后端删除返回 409。 */
  childrenCount: number
  /** 部门下员工数，>0 时后端删除返回 409。 */
  employeeCount: number
  /** 部门下系统用户数，>0 时后端删除返回 409。 */
  userCount: number
  createdAt: string | null
}

/** 新增部门请求体。 */
export interface DeptCreatePayload {
  name: string
  parentId?: number | null
  leader?: string | null
  sort?: number
  status?: number
  remark?: string | null
}

/** 修改部门请求体（部分更新，parentId 传 null 表示移到顶层）。 */
export type DeptUpdatePayload = Partial<DeptCreatePayload>

/** 批量排序项。 */
export interface DeptSortItem {
  id: number
  sort: number
}
