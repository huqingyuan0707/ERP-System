/** 销售订单接口（SAL-02/04~09/15）：/sales/orders 的列表、详情、草稿 CRUD、状态机、转单与信用概览。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  SalCreditSummary,
  SalOrder,
  SalOrderAuditPayload,
  SalOrderClosePayload,
  SalOrderConvertPayload,
  SalOrderCreatePayload,
  SalOrderFromQuotation,
  SalOrderQuery,
  SalOrderUpdatePayload,
} from '@/types/salesOrder'

/** 过滤空值，避免 axios 把 null 序列化成空字符串导致后端 date/int 参数 422。 */
const toParams = (params: SalOrderQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询销售订单（后端自动叠加角色数据范围，含出库进度汇总）。 */
export const getSalOrderList = (params: SalOrderQuery): Promise<PageResult<SalOrder>> =>
  request({ url: '/sales/orders', method: 'get', params: toParams(params) })

/** 销售订单详情（含明细、累计出库数量，供详情与出库参照）。 */
export const getSalOrderDetail = (id: number): Promise<SalOrder> =>
  request({ url: `/sales/orders/${id}`, method: 'get' })

/** 客户信用概览（SAL-15）：额度、已占用（未结清应收 + 已审未出库）与可用余额。 */
export const getSalCreditInfo = (customerId: number): Promise<SalCreditSummary> =>
  request({ url: '/sales/orders/credit-info', method: 'get', params: { customerId } })

/** 报价转订单预填（只读）：带出客户、明细、价格与信用概览，不改报价状态。 */
export const buildOrderFromQuotation = (quotationId: number): Promise<SalOrderFromQuotation> =>
  request({ url: `/sales/orders/from-quotation/${quotationId}`, method: 'get' })

/** 新增销售订单（保存即草稿），金额由后端按明细重算并做信用预检。 */
export const createSalOrder = (
  payload: SalOrderCreatePayload,
  idempotencyKey?: string,
): Promise<SalOrder> =>
  request({
    url: '/sales/orders',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 报价转订单（SAL-02）：明细价格从报价复制，报价置「已转订单」，重复转单 409。 */
export const convertQuotation = (
  quotationId: number,
  payload: SalOrderConvertPayload,
  idempotencyKey?: string,
): Promise<SalOrder> =>
  request({
    url: `/sales/quotations/${quotationId}/convert`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改草稿（非草稿或版本冲突返回 409）。 */
export const updateSalOrder = (
  id: number,
  payload: SalOrderUpdatePayload,
  idempotencyKey?: string,
): Promise<SalOrder> =>
  request({
    url: `/sales/orders/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除草稿（后端软删除，明细保留流水）。 */
export const deleteSalOrder = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/sales/orders/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 提交：草稿 → 待审。 */
export const submitSalOrder = (id: number, idempotencyKey?: string): Promise<SalOrder> =>
  request({
    url: `/sales/orders/${id}/submit`,
    method: 'post',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 审核：通过（做 SAL-15 信用校验，超额 409）→ 已审；驳回 → 草稿并记录原因。 */
export const auditSalOrder = (
  id: number,
  payload: SalOrderAuditPayload,
  idempotencyKey?: string,
): Promise<SalOrder> =>
  request({
    url: `/sales/orders/${id}/audit`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 关闭：剩余未出库数量作废，关闭后不可再出库。 */
export const closeSalOrder = (
  id: number,
  payload: SalOrderClosePayload,
  idempotencyKey?: string,
): Promise<SalOrder> =>
  request({
    url: `/sales/orders/${id}/close`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
