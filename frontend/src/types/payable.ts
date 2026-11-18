/** 应付管理类型（F-02，与后端 camelCase 契约对齐）。 */

export const PAYABLE_STATUS: Record<number, string> = {
  0: '未付',
  1: '部分付',
  2: '已付',
}

export interface Payable {
  id: number
  supplierId: number
  supplierName: string | null
  sourceType: string
  sourceNo: string
  billDate: string
  dueDate: string | null
  amount: string | number
  paidAmount: string | number
  unpaidAmount: string | number
  /** 未付为负时的应退/预付正数金额 */
  creditAmount: string | number
  status: number
  agingDays: number
  agingBucket: string
  remark: string | null
  createdAt: string | null
}

export interface PayableQuery {
  page: number
  pageSize: number
  keyword: string | null
  supplierId: number | null
  status: number | null
  sourceNo: string | null
  overdue: boolean | null
  billDateStart: string | null
  billDateEnd: string | null
}

export interface PayableSummary {
  totalAmount: string | number
  totalPaid: string | number
  totalUnpaid: string | number
  creditAmount: string | number
  overdueAmount: string | number
  count: number
}

export interface AgingBucket {
  bucket: string
  count: number
  amount: string | number
}

export interface WriteoffRecord {
  paymentId: number
  writeoffAmount: string | number
  writeoffAt: string
}

export interface ReturnRecord {
  id: number
  returnNo: string
  returnDate: string
  totalAmount: string | number
}

export interface PayableDetail extends Payable {
  writeoffs: WriteoffRecord[]
  returns: ReturnRecord[]
}
