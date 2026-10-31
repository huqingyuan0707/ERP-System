/** 基础资料-供应商相关类型（字段 camelCase，与后端 SupplierOut/Create/Update 对齐）。 */

import type { PageQuery } from './common'

/** 供应商列表项（对应后端 SupplierOut）。 */
export interface Supplier {
  id: number
  code: string
  name: string
  contact: string | null
  phone: string | null
  address: string | null
  taxNo: string | null
  bank: string | null
  account: string | null
  /** 账期天数，用于推算应付到期日。 */
  creditDays: number
  status: number
  /** 未核销应付余额；后端以字符串返回金额，前端展示时再格式化。 */
  payableAmount: string | number
  remark: string | null
  createdAt: string | null
  updatedAt: string | null
}

/** 新增供应商请求体（对应后端 SupplierCreate，部分字段可选）。 */
export interface SupplierCreatePayload {
  code?: string | null
  name: string
  contact?: string | null
  phone?: string | null
  address?: string | null
  taxNo?: string | null
  bank?: string | null
  account?: string | null
  creditDays?: number
  status?: number
  remark?: string | null
}

/** 修改供应商请求体（对应后端 SupplierUpdate，部分更新，仅传需要变更的字段）。 */
export type SupplierUpdatePayload = Partial<SupplierCreatePayload>

/** 供应商列表查询参数（对应后端 SupplierQuery）。 */
export interface SupplierQuery extends PageQuery {
  keyword?: string
  status?: number | null
}
