/** 采购报表类型（R-02，与后端 camelCase 契约对齐）。 */

export type PurchaseReportView = 'detail' | 'summary' | 'rank'
export type PurchaseReportGroupBy = 'goods' | 'supplier'

export interface PurchaseReportQuery {
  page: number
  pageSize: number
  view: PurchaseReportView
  groupBy: PurchaseReportGroupBy
  startDate: string | null
  endDate: string | null
  supplierId: number | null
  goodsId: number | null
  warehouseId: number | null
}

export interface PurchaseReportTotals {
  inboundQty: string | number
  returnQty: string | number
  netQty: string | number
  inboundAmount: string | number
  returnAmount: string | number
  netAmount: string | number
  rowCount: number
}

export interface PurchaseReportDetailItem {
  type: 'inbound' | 'return'
  sourceNo: string
  docNo: string
  date: string
  supplierId: number
  supplierName: string | null
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  warehouseId: number | null
  warehouseName: string | null
  qty: string | number
  price: string | number
  amount: string | number
}

export interface PurchaseReportSummaryItem {
  groupId: number
  groupName: string | null
  inboundQty: string | number
  returnQty: string | number
  netQty: string | number
  inboundAmount: string | number
  returnAmount: string | number
  netAmount: string | number
}

export interface PurchaseReportRankItem {
  rank: number
  supplierId: number
  supplierName: string | null
  netQty: string | number
  netAmount: string | number
}

export interface PurchaseReportOut {
  view: PurchaseReportView
  total: number
  page: number
  pageSize: number
  items: Array<
    PurchaseReportDetailItem | PurchaseReportSummaryItem | PurchaseReportRankItem
  >
  totals: PurchaseReportTotals
}
