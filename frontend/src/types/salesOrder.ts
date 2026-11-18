/** 销售订单类型（SAL-02/04~09/15，字段 camelCase，与后端 SalOrderOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'
import type { DecimalValue } from './goods'

/** 销售订单状态：0 草稿 / 1 待审 / 2 已审 / 3 部分出库 / 4 完成 / 5 关闭。 */
export const SAL_ORDER_STATUS = {
  DRAFT: 0,
  PENDING: 1,
  APPROVED: 2,
  PARTIAL_OUT: 3,
  COMPLETED: 4,
  CLOSED: 5,
} as const

/** 状态中文标签：列表、详情统一展示口径。 */
export const SAL_ORDER_STATUS_LABEL: Record<number, string> = {
  [SAL_ORDER_STATUS.DRAFT]: '草稿',
  [SAL_ORDER_STATUS.PENDING]: '待审',
  [SAL_ORDER_STATUS.APPROVED]: '已审',
  [SAL_ORDER_STATUS.PARTIAL_OUT]: '部分出库',
  [SAL_ORDER_STATUS.COMPLETED]: '完成',
  [SAL_ORDER_STATUS.CLOSED]: '关闭',
}

/** 状态标签颜色：草稿灰、待审橙、已审/部分出库蓝、完成绿、关闭红。 */
export const SAL_ORDER_STATUS_TAG: Record<
  number,
  'info' | 'warning' | 'success' | 'danger' | 'primary'
> = {
  [SAL_ORDER_STATUS.DRAFT]: 'info',
  [SAL_ORDER_STATUS.PENDING]: 'warning',
  [SAL_ORDER_STATUS.APPROVED]: 'primary',
  [SAL_ORDER_STATUS.PARTIAL_OUT]: 'primary',
  [SAL_ORDER_STATUS.COMPLETED]: 'success',
  [SAL_ORDER_STATUS.CLOSED]: 'danger',
}

/** 可提交 / 编辑 / 删除的状态。 */
export const isSalDraft = (status: number): boolean => status === SAL_ORDER_STATUS.DRAFT
/** 可关闭的状态：已审 / 部分出库（SAL-08）。 */
export const isSalClosable = (status: number): boolean =>
  status === SAL_ORDER_STATUS.APPROVED || status === SAL_ORDER_STATUS.PARTIAL_OUT

/** 销售订单明细行（对应后端 SalOrderItemOut）。 */
export interface SalOrderItem {
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
  /** 累计已出库数量 */
  outboundQty: DecimalValue
  /** 剩余可出库数量 = qty - outboundQty */
  remainingQty: DecimalValue
}

/** 销售订单（对应后端 SalOrderOut）；列表接口 items 为空数组，但含出库进度汇总。 */
export interface SalOrder {
  id: number
  orderNo: string
  customerId: number
  customerName: string | null
  orderDate: string
  status: number
  /** 折扣后含税合计 */
  totalAmount: DecimalValue
  /** 折扣后税额 */
  taxAmount: DecimalValue
  /** 整单折扣金额 */
  discount: DecimalValue
  auditBy: number | null
  auditByName: string | null
  auditAt: string | null
  /** 审核意见 / 驳回原因 / 关闭原因 */
  auditRemark: string | null
  remark: string | null
  /** 乐观锁版本号，编辑动作时回传 */
  version: number
  itemsCount: number
  totalQty: DecimalValue
  outboundQty: DecimalValue
  /** 已出库进度百分比 0～100 */
  outboundProgress: DecimalValue
  createdAt: string | null
  items: SalOrderItem[]
}

/** 明细行入参（对应后端 SalOrderItemIn），金额由后端重算，禁止前端传 amount。 */
export interface SalOrderItemPayload {
  goodsId: number
  unitId: number
  qty: string
  price: string
  taxRate: string
}

/** 新增订单入参（对应后端 SalOrderCreate）。 */
export interface SalOrderCreatePayload {
  customerId: number
  orderDate: string
  discount: string
  remark?: string | null
  items: SalOrderItemPayload[]
}

/** 修改草稿入参（对应后端 SalOrderUpdate），version 用于乐观锁冲突检测。 */
export interface SalOrderUpdatePayload {
  customerId?: number
  orderDate?: string
  discount?: string
  remark?: string | null
  items?: SalOrderItemPayload[]
  version?: number
}

/** 审核入参：驳回必须填写原因。 */
export interface SalOrderAuditPayload {
  result: 'approved' | 'rejected'
  remark?: string | null
  version?: number
}

/** 关闭入参：记录关闭原因。 */
export interface SalOrderClosePayload {
  reason?: string | null
  version?: number
}

/** 列表查询参数（对应后端 SalOrderQuery）。 */
export interface SalOrderQuery extends PageQuery {
  status?: number | null
  customerId?: number | null
  orderDateStart?: string | null
  orderDateEnd?: string | null
}

/** 客户信用概览（对应后端 CreditSummaryOut，SAL-15 下单页展示）。 */
export interface SalCreditSummary {
  customerId: number
  customerName: string | null
  creditLimit: DecimalValue
  creditDays: number
  /** 已占用 = 未结清应收 + 已审未出库订单未出库金额 */
  occupiedAmount: DecimalValue
  /** 可用余额；额度为 0（不控制）时为 null */
  availableAmount: DecimalValue | null
}

/** 报价转订单预填明细（对应后端 SalOrderFromQuotationItem）。 */
export interface SalOrderFromQuotationItem {
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitId: number | null
  unitName: string | null
  qty: DecimalValue
  price: DecimalValue
  taxRate: DecimalValue
}

/** 报价转订单预填（对应后端 SalOrderFromQuotationOut，只读不落库）。 */
export interface SalOrderFromQuotation {
  sourceQuotationId: number
  quoteNo: string
  customerId: number
  customerName: string | null
  creditLimit: DecimalValue
  occupiedAmount: DecimalValue
  orderDate: string
  remark: string | null
  items: SalOrderFromQuotationItem[]
}

/** 报价转订单提交入参（对应后端 SalOrderConvert）；明细价格以后端复制为准。 */
export interface SalOrderConvertPayload {
  orderDate: string
  discount: string
  remark?: string | null
}
