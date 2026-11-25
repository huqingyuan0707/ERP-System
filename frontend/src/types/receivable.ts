/** 应收管理类型（F-01，与后端 camelCase 契约对齐）。 */

export const RECEIVABLE_STATUS: Record<number, string> = {
  0: '未收',
  1: '部分收',
  2: '已收',
}

export interface Receivable {
  id: number
  customerId: number
  customerName: string | null
  sourceType: string
  sourceNo: string
  billDate: string
  dueDate: string | null
  amount: string | number
  receivedAmount: string | number
  unreceivedAmount: string | number
  /** 未收为负时的应退/预收正数金额 */
  creditAmount: string | number
  status: number
  agingDays: number
  agingBucket: string
  remark: string | null
  createdAt: string | null
}

export interface ReceivableQuery {
  page: number
  pageSize: number
  keyword: string | null
  customerId: number | null
  status: number | null
  sourceNo: string | null
  overdue: boolean | null
  /** 仅未结清（未收 > 0）：F-03 收款核销选择应收用 */
  unsettled: boolean | null
  billDateStart: string | null
  billDateEnd: string | null
}

export interface ReceivableSummary {
  totalAmount: string | number
  totalReceived: string | number
  totalUnreceived: string | number
  creditAmount: string | number
  overdueAmount: string | number
  count: number
}

export interface AgingBucket {
  bucket: string
  count: number
  amount: string | number
}

export interface ReceiptWriteoffRecord {
  receiptId: number
  writeoffAmount: string | number
  writeoffAt: string
}

export interface SalesReturnRecord {
  id: number
  returnNo: string
  returnDate: string
  totalAmount: string | number
}

export interface ReceivableDetail extends Receivable {
  writeoffs: ReceiptWriteoffRecord[]
  returns: SalesReturnRecord[]
}
