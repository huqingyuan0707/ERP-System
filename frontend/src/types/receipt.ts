/** 收款管理类型（F-03，与后端 camelCase 契约对齐）。 */

export const RECEIPT_STATUS: Record<number, string> = {
  0: '待核销',
  1: '部分核销',
  2: '已核销',
}

export interface ReceiptItem {
  id: number
  receivableId: number
  receivableSourceNo: string | null
  writeoffAmount: string | number
  writeoffAt: string | null
}

export interface Receipt {
  id: number
  receiptNo: string
  customerId: number
  customerName: string | null
  receiptDate: string
  amount: string | number
  method: string | null
  account: string | null
  allocatedAmount: string | number
  unallocatedAmount: string | number
  status: number
  remark: string | null
  version: number
  createdAt: string | null
  items: ReceiptItem[]
}

export interface ReceiptQuery {
  page: number
  pageSize: number
  customerId: number | null
  status: number | null
  receiptDateStart: string | null
  receiptDateEnd: string | null
  keyword: string | null
}

export interface ReceiptSummary {
  totalAmount: string | number
  totalAllocated: string | number
  totalUnallocated: string | number
  count: number
}

export interface ReceiptCreatePayload {
  customerId: number
  receiptDate: string
  amount: string | number
  method?: string | null
  account?: string | null
  remark?: string | null
}

export interface ReceiptUpdatePayload {
  customerId?: number
  receiptDate?: string
  amount?: string | number
  method?: string | null
  account?: string | null
  remark?: string | null
  version?: number
}

export interface WriteoffItemPayload {
  receivableId: number
  writeoffAmount: string | number
}

export interface WriteoffPayload {
  items: WriteoffItemPayload[]
}
