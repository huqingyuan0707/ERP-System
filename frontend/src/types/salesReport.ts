/** 销售报表类型（R-03，与后端 camelCase 契约对齐）。 */

export type SalesReportView = 'detail' | 'summary' | 'customer_rank' | 'goods_rank'
export type SalesReportGroupBy = 'goods' | 'customer'

export interface SalesReportQuery {
  page: number
  pageSize: number
  view: SalesReportView
  groupBy: SalesReportGroupBy
  startDate: string | null
  endDate: string | null
  customerId: number | null
  goodsId: number | null
  warehouseId: number | null
}

export interface SalesReportTotals {
  outboundQty: string | number
  returnQty: string | number
  netQty: string | number
  outboundAmount: string | number
  returnAmount: string | number
  netAmount: string | number
  rowCount: number
}

export interface SalesReportDetailItem {
  type: 'outbound' | 'return'
  sourceNo: string
  docNo: string
  date: string
  customerId: number
  customerName: string | null
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  warehouseId: number | null
  warehouseName: string | null
  qty: string | number
  price: string | number
  amount: string | number
}

export interface SalesReportSummaryItem {
  groupId: number
  groupName: string | null
  outboundQty: string | number
  returnQty: string | number
  netQty: string | number
  outboundAmount: string | number
  returnAmount: string | number
  netAmount: string | number
}

export interface SalesReportRankItem {
  rank: number
  rankId: number
  rankName: string | null
  netQty: string | number
  netAmount: string | number
}

export interface SalesReportOut {
  view: SalesReportView
  total: number
  page: number
  pageSize: number
  items: Array<SalesReportDetailItem | SalesReportSummaryItem | SalesReportRankItem>
  totals: SalesReportTotals
}
