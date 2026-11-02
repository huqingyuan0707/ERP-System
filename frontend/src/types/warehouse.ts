/** 基础资料-仓库相关类型（字段 camelCase，与后端 WarehouseOut/Create/Update 对齐）。 */

import type { PageQuery } from './common'

/** 仓库列表项（对应后端 WarehouseOut）。 */
export interface Warehouse {
  id: number
  code: string
  name: string
  managerId: number | null
  managerName: string | null
  isDefault: boolean
  address: string | null
  status: number
  remark: string | null
  createdAt: string | null
}

/** 新增仓库请求体（对应后端 WarehouseCreate，部分字段可选）。 */
export interface WarehouseCreatePayload {
  code?: string | null
  name: string
  managerId?: number | null
  isDefault?: boolean
  address?: string | null
  status?: number
  remark?: string | null
}

/** 修改仓库请求体（对应后端 WarehouseUpdate，部分更新，仅传需要变更的字段）。 */
export type WarehouseUpdatePayload = Partial<Omit<WarehouseCreatePayload, 'code'>> & { code?: string }

/** 仓库列表查询参数（对应后端 WarehouseQuery）。 */
export interface WarehouseQuery extends PageQuery {
  keyword?: string
  status?: number | null
}

/** 可选负责人选项（用于下拉框，仓库专用接口，暂用用户，员工待 B06）。 */
export interface ManagerOption {
  value: number
  label: string
}
