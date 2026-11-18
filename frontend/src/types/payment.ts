/** 付款管理类型（F-04，与后端 camelCase 契约对齐）。 */

import type { DecimalValue } from './goods'

/** 核销进度标签（派生口径：allocated_amount vs amount，后端 fin_payment 无状态列）。 */
export const PAYMENT_STATUS: Record<number, string> = {
  0: '待核销',
  1: '部分核销',
  2: '已核销',
}

/** 核销进度对应的 el-tag 类型。 */
export const PAYMENT_STATUS_TAG: Record<number, 'info' | 'warning' | 'success'> = {
  0: 'info',
  1: 'warning',
  2: 'success',
}

export interface Payment {
  id: number
  paymentNo: string
  supplierId: number
  supplierName: string | null
  paymentDate: string
  amount: DecimalValue
  /** 结算方式（字典 settlement_type 的 value） */
  method: string | null
  account: string | null
  allocatedAmount: DecimalValue
  unallocatedAmount: DecimalValue
  /** 核销进度（后端派生，非数据库列） */
  status: number
  remark: string | null
  /** 乐观锁版本号：编辑时回传后端 */
  version: number
  createdAt: string | null
}

export interface PaymentItem {
  id: number
  payableId: number
  /** 被核销应付的原入库单号 */
  payableSourceNo: string | null
  writeoffAmount: DecimalValue
  writeoffAt: string | null
}

export interface PaymentDetail extends Payment {
  items: PaymentItem[]
}

export interface PaymentQuery {
  page: number
  pageSize: number
  keyword: string | null
  supplierId: number | null
  status: number | null
  paymentDateStart: string | null
  paymentDateEnd: string | null
}

export interface PaymentSummary {
  totalAmount: DecimalValue
  totalAllocated: DecimalValue
  totalUnallocated: DecimalValue
  count: number
}

export interface PaymentCreatePayload {
  supplierId: number
  paymentDate: string
  /** 金额以字符串提交，避免浮点精度问题；后端重算未分配余额 */
  amount: string
  method: string | null
  account: string | null
  remark: string | null
}

export interface PaymentUpdatePayload extends Partial<PaymentCreatePayload> {
  /** 不传时后端以当前版本为准（向后兼容） */
  version?: number
}

export interface WriteoffItemPayload {
  payableId: number
  writeoffAmount: string
}

export interface WriteoffPayload {
  items: WriteoffItemPayload[]
}
