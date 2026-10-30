/** 系统管理公共类型：部门。 */

export interface Dept {
  id: number
  name: string
  parentId: number | null
  sort: number
  status: number
}
