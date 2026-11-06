/** 采购申请类型（P-01，字段 camelCase，与后端 PurApplyOut/Create/Update/Query 对齐）。 */

import type { PageQuery } from './common'

/** 采购申请状态：0 草稿 / 1 待审 / 2 已审（与后端 PurApplyStatus 一致）。 */
export const APPLY_STATUS = {
  DRAFT: 0,
  PENDING: 1,
  APPROVED: 2,
} as const

/** 状态中文标签：列表、详情与打印统一展示口径。 */
export const APPLY_STATUS_LABEL: Record<number, string> = {
  [APPLY_STATUS.DRAFT]: '草稿',
  [APPLY_STATUS.PENDING]: '待审',
  [APPLY_STATUS.APPROVED]: '已审',
}

/** 状态标签颜色：草稿灰、待审橙、已审绿，与列表 el-tag 的 type 对应。 */
export const APPLY_STATUS_TAG: Record<number, 'info' | 'warning' | 'success'> = {
  [APPLY_STATUS.DRAFT]: 'info',
  [APPLY_STATUS.PENDING]: 'warning',
  [APPLY_STATUS.APPROVED]: 'success',
}

/** 申请明细行（对应后端 PurApplyItemOut）。 */
export interface PurApplyItem {
  id: number
  goodsId: number
  /** 商品编码，用于打印与列表展示。 */
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  /** 申请数量，后端以字符串返回小数。 */
  qty: string | number
  needDate: string | null
}

/** 申请单（对应后端 PurApplyOut），列表接口 items 为空数组。 */
export interface PurApply {
  id: number
  applyNo: string
  deptId: number
  deptName: string | null
  applicantId: number
  applicantName: string | null
  needDate: string | null
  status: number
  auditBy: number | null
  auditByName: string | null
  auditAt: string | null
  auditRemark: string | null
  remark: string | null
  version: number
  itemsCount: number
  createdAt: string | null
  items: PurApplyItem[]
}

/** 明细行入参（对应后端 PurApplyItemIn）。 */
export interface PurApplyItemPayload {
  goodsId: number
  qty: string | number
  needDate?: string | null
}

/** 新增申请入参（对应后端 PurApplyCreate）。 */
export interface PurApplyCreatePayload {
  deptId?: number | null
  applicantId?: number | null
  needDate: string
  remark?: string | null
  items: PurApplyItemPayload[]
}

/** 修改草稿入参（对应后端 PurApplyUpdate）。 */
export interface PurApplyUpdatePayload {
  deptId?: number | null
  needDate?: string | null
  remark?: string | null
  items?: PurApplyItemPayload[]
}

/** 审核入参（对应后端 PurApplyAudit）：驳回必须填写原因。 */
export interface PurApplyAuditPayload {
  result: 'approved' | 'rejected'
  remark?: string | null
}

/** 列表查询参数（对应后端 PurApplyQuery）。 */
export interface PurApplyQuery extends PageQuery {
  status?: number | null
  deptId?: number | null
  needDateStart?: string | null
  needDateEnd?: string | null
}
