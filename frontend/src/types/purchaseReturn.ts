/** 采购退货类型（P-04，字段 camelCase，与后端 PurReturnOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'
import type { DecimalValue } from './goods'

/** 采购退货状态：0 草稿 / 1 已确认。 */
export const RETURN_STATUS = {
  DRAFT: 0,
  CONFIRMED: 1,
} as const

/** 状态中文标签：列表、详情与打印统一展示口径。 */
export const RETURN_STATUS_LABEL: Record<number, string> = {
  [RETURN_STATUS.DRAFT]: '草稿',
  [RETURN_STATUS.CONFIRMED]: '已确认',
}

/** 状态标签颜色：草稿灰、已确认绿。 */
export const RETURN_STATUS_TAG: Record<number, 'info' | 'success'> = {
  [RETURN_STATUS.DRAFT]: 'info',
  [RETURN_STATUS.CONFIRMED]: 'success',
}

/** 可编辑 / 可删除 / 可确认的状态。 */
export const isReturnDraft = (status: number): boolean => status === RETURN_STATUS.DRAFT

/** 退货明细行（对应后端 PurReturnItemOut），含原入库/已退/可退数量。 */
export interface PurReturnItem {
  id: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: DecimalValue
  price: DecimalValue
  amount: DecimalValue
  originalQty: DecimalValue
  returnedQty: DecimalValue
  returnableQty: DecimalValue
}

/** 采购退货单（对应后端 PurReturnOut）。 */
export interface PurReturn {
  id: number
  returnNo: string
  supplierId: number
  supplierName: string | null
  warehouseId: number
  warehouseName: string | null
  sourceNo: string
  returnDate: string
  status: number
  totalAmount: DecimalValue
  remark: string | null
  version: number
  itemsCount: number
  createdAt: string | null
  items: PurReturnItem[]
}

/** 明细行入参（对应后端 PurReturnItemIn）：金额由后端重算，禁止传 amount。 */
export interface PurReturnItemPayload {
  goodsId: number
  qty: string
  price: string
}

/** 新增退货单入参（对应后端 PurReturnCreate）。 */
export interface PurReturnCreatePayload {
  supplierId: number
  warehouseId: number
  sourceNo: string
  returnDate: string
  remark?: string | null
  items: PurReturnItemPayload[]
}

/** 修改草稿入参（对应后端 PurReturnUpdate），version 用于乐观锁冲突检测。 */
export interface PurReturnUpdatePayload {
  supplierId?: number
  warehouseId?: number
  sourceNo?: string
  returnDate?: string
  remark?: string | null
  items?: PurReturnItemPayload[]
  version?: number
}

/** 列表查询参数（对应后端 PurReturnQuery）。 */
export interface PurReturnQuery extends PageQuery {
  status?: number | null
  supplierId?: number | null
  warehouseId?: number | null
  sourceNo?: string | null
  returnDateStart?: string | null
  returnDateEnd?: string | null
}

/** 原入库单按商品聚合的可退明细（对应后端 SourceInboundItem）。 */
export interface SourceInboundItem {
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  originalQty: DecimalValue
  returnedQty: DecimalValue
  returnableQty: DecimalValue
  price: DecimalValue
}

/** 原应付快照（对应后端 SourcePayableOut）。 */
export interface SourcePayable {
  amount: DecimalValue
  paidAmount: DecimalValue
  unpaidAmount: DecimalValue
  status: number
}

/** 按原入库单号查询的可退数据（对应后端 SourceInboundOut）。 */
export interface SourceInbound {
  inboundId: number
  inboundNo: string
  supplierId: number
  supplierName: string | null
  warehouseId: number
  warehouseName: string | null
  inboundDate: string
  payable: SourcePayable | null
  items: SourceInboundItem[]
}
