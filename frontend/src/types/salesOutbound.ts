/** 销售出库/退货类型（SA-03/SA-04，与后端 camelCase 对齐）。 */

export interface OutboundItem {
  id: number
  orderItemId: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  qty: string | number
  price: string | number
  amount: string | number
  returnedQty?: string | number
  returnableQty?: string | number
}

export interface Outbound {
  id: number
  outboundNo: string
  customerId: number
  customerName: string | null
  warehouseId: number
  warehouseName: string | null
  orderId: number
  orderNo: string | null
  outboundDate: string
  status: number
  totalAmount: string | number
  taxAmount: string | number
  remark: string | null
  version: number
  items: OutboundItem[]
}

export interface OutboundQuery {
  page: number
  pageSize: number
  status: number | null
  customerId: number | null
  warehouseId: number | null
  startDate: string | null
  endDate: string | null
  keyword: string | null
}

export interface OutboundFromOrderItem {
  orderItemId: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: string | number
  outboundQty: string | number
  remainingQty: string | number
  price: string | number
  taxRate: string | number
}

export interface OutboundFromOrder {
  orderId: number
  orderNo: string
  customerId: number
  customerName: string | null
  outboundDate: string
  remark: string | null
  items: OutboundFromOrderItem[]
}

export interface OutboundCreateItem {
  orderItemId: number
  qty: string | number
}

export interface OutboundCreate {
  orderId: number
  warehouseId: number
  outboundDate: string
  remark?: string | null
  items: OutboundCreateItem[]
}

export interface ReturnItem {
  id: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  unitName: string | null
  qty: string | number
  price: string | number
  amount: string | number
}

export interface SalesReturn {
  id: number
  returnNo: string
  customerId: number
  customerName: string | null
  warehouseId: number
  warehouseName: string | null
  sourceNo: string
  returnDate: string
  status: number
  totalAmount: string | number
  remark: string | null
  version: number
  items: ReturnItem[]
}

export interface ReturnQuery {
  page: number
  pageSize: number
  status: number | null
  customerId: number | null
  warehouseId: number | null
  sourceNo: string | null
  startDate: string | null
  endDate: string | null
  keyword: string | null
}

export interface ReturnSourceItem {
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  unitName: string | null
  outboundQty: string | number
  returnedQty: string | number
  returnableQty: string | number
  price: string | number
}

export interface ReturnSource {
  outboundId: number
  outboundNo: string
  customerId: number
  customerName: string | null
  warehouseId: number
  warehouseName: string | null
  outboundDate: string
  totalAmount: string | number
  items: ReturnSourceItem[]
}

export interface ReturnCreateItem {
  goodsId: number
  qty: string | number
}

export interface ReturnCreate {
  sourceNo: string
  warehouseId: number
  returnDate: string
  remark?: string | null
  items: ReturnCreateItem[]
}
