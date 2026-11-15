/** 采购申请接口（P-01）：/purchase/applies 的列表、详情、草稿 CRUD、提交与审核。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  PurApply,
  PurApplyAuditPayload,
  PurApplyCreatePayload,
  PurApplyQuery,
  PurApplyUpdatePayload,
} from '@/types/purchaseApply'

/** 分页查询采购申请（自动叠加后端数据范围）。 */
export const getPurApplyList = (params: PurApplyQuery): Promise<PageResult<PurApply>> =>
  request({ url: '/purchase/applies', method: 'get', params })

/** 采购申请详情（含明细，供详情与打印使用）。 */
export const getPurApplyDetail = (id: number): Promise<PurApply> =>
  request({ url: `/purchase/applies/${id}`, method: 'get' })

/** 新增采购申请（保存即草稿），支持 Idempotency-Key 防重复提交。 */
export const createPurApply = (
  payload: PurApplyCreatePayload,
  idempotencyKey?: string,
): Promise<PurApply> =>
  request({
    url: '/purchase/applies',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改草稿（非草稿返回 409，需先由审核人驳回）。 */
export const updatePurApply = (
  id: number,
  payload: PurApplyUpdatePayload,
  idempotencyKey?: string,
): Promise<PurApply> =>
  request({
    url: `/purchase/applies/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除草稿（后端软删除，明细保留流水）。 */
export const deletePurApply = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/purchase/applies/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 提交采购申请：草稿 → 待审。 */
export const submitPurApply = (id: number, idempotencyKey?: string): Promise<PurApply> =>
  request({
    url: `/purchase/applies/${id}/submit`,
    method: 'post',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 审核采购申请：通过 → 已审；驳回 → 草稿并记录驳回原因。 */
export const auditPurApply = (
  id: number,
  payload: PurApplyAuditPayload,
  idempotencyKey?: string,
): Promise<PurApply> =>
  request({
    url: `/purchase/applies/${id}/audit`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
