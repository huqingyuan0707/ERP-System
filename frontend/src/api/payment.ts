/** 付款管理接口（F-04）：/finance/payments 的列表、汇总、详情、CRUD 与核销。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Payment,
  PaymentCreatePayload,
  PaymentDetail,
  PaymentQuery,
  PaymentSummary,
  PaymentUpdatePayload,
  WriteoffPayload,
} from '@/types/payment'

/** 过滤空值，避免 axios 把 null 序列化成空字符串导致后端 date/int 参数 422。 */
const toParams = (params: PaymentQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询付款单（后端自动叠加角色数据范围）。 */
export const getPaymentList = (params: PaymentQuery): Promise<PageResult<Payment>> =>
  request({ url: '/finance/payments', method: 'get', params: toParams(params) })

/** 汇总卡片：与列表同筛选口径。 */
export const getPaymentSummary = (params: PaymentQuery): Promise<PaymentSummary> =>
  request({ url: '/finance/payments/summary', method: 'get', params: toParams(params) })

/** 付款单详情：含核销明细（应付单号/核销金额/核销时间）。 */
export const getPaymentDetail = (id: number): Promise<PaymentDetail> =>
  request({ url: `/finance/payments/${id}`, method: 'get' })

/** 新增付款单（保存即待核销），单号由后端生成，不直接改变应付。 */
export const createPayment = (
  payload: PaymentCreatePayload,
  idempotencyKey?: string,
): Promise<Payment> =>
  request({
    url: '/finance/payments',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改付款单（仅未核销可改；已核销或版本冲突返回 409）。 */
export const updatePayment = (
  id: number,
  payload: PaymentUpdatePayload,
  idempotencyKey?: string,
): Promise<Payment> =>
  request({
    url: `/finance/payments/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除付款单（仅未核销可删，后端软删除）。 */
export const deletePayment = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/finance/payments/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 付款核销：校验供应商一致/单条≤未付/总额≤未分配 → 写核销明细 → 冲减应付。 */
export const writeoffPayment = (
  id: number,
  payload: WriteoffPayload,
  idempotencyKey?: string,
): Promise<Payment> =>
  request({
    url: `/finance/payments/${id}/writeoff`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
