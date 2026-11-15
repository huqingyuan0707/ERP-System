/** 采购订单类型（P-02，字段 camelCase，与后端 PurOrderOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'
import type { DecimalValue } from './goods'

/** 采购订单状态：0 草稿 / 1 待审 / 2 已审 / 3 部分入库 / 4 完成 / 5 关闭。 */
export const ORDER_STATUS = {
  DRAFT: 0,
  PENDING: 1,
  APPROVED: 2,
  PARTIAL_INBOUND: 3,
  COMPLETED: 4,
  CLOSED: 5,
} as const

/** 状态中文标签：列表、详情与打印统一展示口径。 */
export const ORDER_STATUS_LABEL: Record<number, string> = {
  [ORDER_STATUS.DRAFT]: '草稿',
  [ORDER_STATUS.PENDING]: '待审',
  [ORDER_STATUS.APPROVED]: '已审',
  [ORDER_STATUS.PARTIAL_INBOUND]: '部分入库',
  [ORDER_STATUS.COMPLETED]: '完成',
  [ORDER_STATUS.CLOSED]: '关闭',
}

/** 状态标签颜色：草稿灰、待审橙、已审/部分入库蓝、完成绿、关闭红。 */
export const ORDER_STATUS_TAG: Record<
  number,
  'info' | 'warning' | 'success' | 'danger' | 'primary'
> = {
  [ORDER_STATUS.DRAFT]: 'info',
  [ORDER_STATUS.PENDING]: 'warning',
  [ORDER_STATUS.APPROVED]: 'primary',
  [ORDER_STATUS.PARTIAL_INBOUND]: 'primary',
  [ORDER_STATUS.COMPLETED]: 'success',
  [ORDER_STATUS.CLOSED]: 'danger',
}

/** 可提交 / 编辑 / 删除的状态。 */
export const isDraft = (status: number): boolean => status === ORDER_STATUS.DRAFT
/** 可关闭的状态：待审 / 已审 / 部分入库。 */
export const isClosable = (status: number): boolean =>
  status === ORDER_STATUS.PENDING ||
  status === ORDER_STATUS.APPROVED ||
  status === ORDER_STATUS.PARTIAL_INBOUND

/** 订单明细行（对应后端 PurOrderItemOut）。 */
export interface PurOrderItem {
  id: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitId: number
  unitName: string | null
  qty: DecimalValue
  /** 含税单价 */
  price: DecimalValue
  /** 含税行金额，由后端重算 */
  amount: DecimalValue
  /** 税率 0～1，0.13 表示 13% */
  taxRate: DecimalValue
  /** 累计已入库数量 */
  inboundQty: DecimalValue
  /** 剩余可入库数量，关闭后为 0 */
  remainingQty: DecimalValue
  applyItemId: number | null
}

/** 采购订单（对应后端 PurOrderOut）；列表接口 items 为空数组，但含入库进度汇总。 */
export interface PurOrder {
  id: number
  orderNo: string
  supplierId: number
  supplierName: string | null
  orderDate: string
  status: number
  /** 含税合计 */
  totalAmount: DecimalValue
  /** 其中税额（价税分离倒推，不再加到合计上） */
  taxAmount: DecimalValue
  auditBy: number | null
  auditByName: string | null
  auditAt: string | null
  /** 审核意见 / 驳回原因 / 关闭原因 */
  auditRemark: string | null
  remark: string | null
  sourceApplyId: number | null
  sourceApplyNo: string | null
  /** 乐观锁版本号，提交动作时回传 */
  version: number
  itemsCount: number
  totalQty: DecimalValue
  inboundQty: DecimalValue
  /** 已入库进度百分比 0～100 */
  inboundProgress: DecimalValue
  createdAt: string | null
  items: PurOrderItem[]
}

/** 明细行入参（对应后端 PurOrderItemIn），金额由后端重算，禁止前端传 amount。 */
export interface PurOrderItemPayload {
  goodsId: number
  unitId: number
  qty: string
  price: string
  taxRate: string
  applyItemId?: number | null
}

/** 新增订单入参（对应后端 PurOrderCreate）。 */
export interface PurOrderCreatePayload {
  supplierId: number
  orderDate: string
  remark?: string | null
  sourceApplyId?: number | null
  items: PurOrderItemPayload[]
}

/** 修改草稿入参（对应后端 PurOrderUpdate），version 用于乐观锁冲突检测。 */
export interface PurOrderUpdatePayload {
  supplierId?: number
  orderDate?: string
  remark?: string | null
  items?: PurOrderItemPayload[]
  version?: number
}

/** 审核入参：驳回必须填写原因。 */
export interface PurOrderAuditPayload {
  result: 'approved' | 'rejected'
  remark?: string | null
  version?: number
}

/** 关闭入参：记录关闭原因。 */
export interface PurOrderClosePayload {
  reason?: string | null
  version?: number
}

/** 列表查询参数（对应后端 PurOrderQuery）。 */
export interface PurOrderQuery extends PageQuery {
  status?: number | null
  supplierId?: number | null
  orderDateStart?: string | null
  orderDateEnd?: string | null
  sourceApplyId?: number | null
}
