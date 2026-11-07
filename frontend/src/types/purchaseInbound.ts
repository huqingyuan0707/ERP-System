/** 采购入库类型（P-03，字段 camelCase，与后端 PurInboundOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'
import type { DecimalValue } from './goods'

/** 采购入库状态：0 草稿 / 1 已确认。 */
export const INBOUND_STATUS = {
  DRAFT: 0,
  CONFIRMED: 1,
} as const

/** 状态中文标签：列表、详情与打印统一展示口径。 */
export const INBOUND_STATUS_LABEL: Record<number, string> = {
  [INBOUND_STATUS.DRAFT]: '草稿',
  [INBOUND_STATUS.CONFIRMED]: '已确认',
}

/** 状态标签颜色：草稿灰、已确认绿。 */
export const INBOUND_STATUS_TAG: Record<number, 'info' | 'success'> = {
  [INBOUND_STATUS.DRAFT]: 'info',
  [INBOUND_STATUS.CONFIRMED]: 'success',
}

/** 可编辑 / 可删除 / 可确认的状态。 */
export const isInboundDraft = (status: number): boolean => status === INBOUND_STATUS.DRAFT

/** 入库明细行（对应后端 PurInboundItemOut）；单价取来源订单明细，不可修改。 */
export interface PurInboundItem {
  id: number
  orderItemId: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: DecimalValue
  price: DecimalValue
  amount: DecimalValue
  taxRate: DecimalValue
  batchNo: string | null
}

/** 采购入库单（对应后端 PurInboundOut）。 */
export interface PurInbound {
  id: number
  inboundNo: string
  orderId: number
  orderNo: string | null
  supplierId: number
  supplierName: string | null
  warehouseId: number
  warehouseName: string | null
  inboundDate: string
  status: number
  totalAmount: DecimalValue
  taxAmount: DecimalValue
  remark: string | null
  version: number
  itemsCount: number
  createdAt: string | null
  items: PurInboundItem[]
}

/** 明细行入参（对应后端 PurInboundItemIn）：只指定订单明细行与本次数量。 */
export interface PurInboundItemPayload {
  orderItemId: number
  qty: string
  batchNo?: string | null
}

/** 新增入库单入参（对应后端 PurInboundCreate）。 */
export interface PurInboundCreatePayload {
  orderId: number
  warehouseId: number
  inboundDate: string
  remark?: string | null
  items: PurInboundItemPayload[]
}

/** 修改草稿入参（对应后端 PurInboundUpdate），version 用于乐观锁冲突检测。 */
export interface PurInboundUpdatePayload {
  warehouseId?: number
  inboundDate?: string
  remark?: string | null
  items?: PurInboundItemPayload[]
  version?: number
}

/** 列表查询参数（对应后端 PurInboundQuery）。 */
export interface PurInboundQuery extends PageQuery {
  status?: number | null
  orderId?: number | null
  supplierId?: number | null
  warehouseId?: number | null
  inboundDateStart?: string | null
  inboundDateEnd?: string | null
}

/** 按订单预填的明细行（对应后端 PurInboundFromOrderItem）。 */
export interface PurInboundFromOrderItem {
  orderItemId: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: DecimalValue
  inboundQty: DecimalValue
  remainingQty: DecimalValue
  price: DecimalValue
  taxRate: DecimalValue
}

/** 按订单预填出参（对应后端 PurInboundFromOrderOut）。 */
export interface PurInboundFromOrder {
  orderId: number
  orderNo: string
  supplierId: number
  supplierName: string | null
  inboundDate: string
  remark: string | null
  items: PurInboundFromOrderItem[]
}
