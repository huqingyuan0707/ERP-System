/** 基础资料-客户相关类型（字段 camelCase，与后端 CustomerOut/Create/Update 对齐）。 */
import type { PageQuery } from './common'

/** 客户列表项（对应后端 CustomerOut）。 */
export interface Customer {
  id: number
  code: string
  name: string
  contact: string | null
  address: string | null
  phone: string | null
  taxNo: string | null
  bank: string | null
  account: string | null
  /** 信用额度，0 表示不限。 */
  creditLimit: string | number
  /** 账期天数，用于推算应收到期日。 */
  creditDays: number
  status: number
  /** 未核销应收余额；后端以字符串返回金额。 */
  receivableAmount: string | number
  remark: string | null
  createdAt: string | null
}

/** 新增客户请求体（对应后端 CustomerCreate）。 */
export interface CustomerCreatePayload {
  code?: string | null
  name: string
  contact?: string | null
  address?: string | null
  phone?: string | null
  taxNo?: string | null
  bank?: string | null
  account?: string | null
  creditLimit?: string | number
  creditDays?: number
  status?: number
  remark?: string | null
}

/** 修改客户请求体（对应后端 CustomerUpdate，部分更新）。 */
export type CustomerUpdatePayload = Partial<Omit<CustomerCreatePayload, 'code'>> & { code?: string }

/** 客户信用概况（对应后端 CustomerCreditOut）。 */
export interface CustomerCredit {
  creditLimit: string | number
  receivableAmount: string | number
  availableCredit: string | number | null
  unlimited: boolean
  mode: 'block' | 'warn' | 'off'
  overLimit: boolean
}

/** 客户列表查询参数（对应后端 CustomerQuery）。 */
export interface CustomerQuery extends PageQuery {
  keyword?: string
  status?: number | null
}
