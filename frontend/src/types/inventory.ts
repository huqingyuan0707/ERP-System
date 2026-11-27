/** 库存查询类型（I-01/I-02，与后端 camelCase 契约对齐）。 */

export interface StockQuery {
  page: number
  pageSize: number
  keyword: string | null
  warehouseId: number | null
  goodsId: number | null
  onlyAvailable: boolean
  belowLower: boolean
}

export interface StockItem {
  id: number
  warehouseId: number
  warehouseCode: string
  warehouseName: string
  goodsId: number
  goodsCode: string
  goodsName: string
  goodsSpec: string | null
  unitName: string | null
  qty: string | number
  lockedQty: string | number
  availableQty: string | number
  avgCost: string | number
  stockAmount: string | number
  stockLower: string | number
  stockUpper: string | number
  isBelowLower: boolean
  updatedAt: string
}

export interface StockSummary {
  totalQty: string | number
  totalLockedQty: string | number
  totalAvailableQty: string | number
  totalStockAmount: string | number
  lineCount: number
}

export type FlowType =
  | 'purchase_in'
  | 'purchase_return'
  | 'sale_out'
  | 'sale_return'
  | 'transfer_in'
  | 'transfer_out'
  | 'check_gain'
  | 'check_loss'

export interface FlowQuery {
  page: number
  pageSize: number
  keyword: string | null
  warehouseId: number | null
  goodsId: number | null
  flowType: FlowType | null
  direction: number | null
  sourceNo: string | null
  startDate: string | null
  endDate: string | null
}

export interface FlowItem {
  id: number
  warehouseId: number
  warehouseCode: string
  warehouseName: string
  goodsId: number
  goodsCode: string
  goodsName: string
  goodsSpec: string | null
  flowType: FlowType
  direction: number
  qty: string | number
  beforeQty: string | number
  afterQty: string | number
  unitCost: string | number
  amount: string | number
  sourceType: string | null
  sourceNo: string | null
  bizTime: string
  operatorId: number | null
  remark: string | null
  createdAt: string
}
