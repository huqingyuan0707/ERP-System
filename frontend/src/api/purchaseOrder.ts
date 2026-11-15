/** 采购订单接口（P-02）：/purchase/orders 的列表、详情、草稿 CRUD、状态流转与导出。 */

import { download, request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  PurOrder,
  PurOrderAuditPayload,
  PurOrderClosePayload,
  PurOrderCreatePayload,
  PurOrderQuery,
  PurOrderUpdatePayload,
} from '@/types/purchaseOrder'

/** 过滤空值，避免 axios 把 null 序列化成空字符串导致后端 date/int 参数 422。 */
const toParams = (params: PurOrderQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询采购订单（后端自动叠加角色数据范围）。 */
export const getPurOrderList = (params: PurOrderQuery): Promise<PageResult<PurOrder>> =>
  request({ url: '/purchase/orders', method: 'get', params: toParams(params) })

/** 采购订单详情（含明细、累计入库数量，供详情与打印取单据快照）。 */
export const getPurOrderDetail = (id: number): Promise<PurOrder> =>
  request({ url: `/purchase/orders/${id}`, method: 'get' })

/** 新增采购订单（保存即草稿），金额由后端按明细重算。 */
export const createPurOrder = (
  payload: PurOrderCreatePayload,
  idempotencyKey?: string,
): Promise<PurOrder> =>
  request({
    url: '/purchase/orders',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改草稿（非草稿或版本冲突返回 409）。 */
export const updatePurOrder = (
  id: number,
  payload: PurOrderUpdatePayload,
  idempotencyKey?: string,
): Promise<PurOrder> =>
  request({
    url: `/purchase/orders/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除草稿（后端软删除，明细保留流水）。 */
export const deletePurOrder = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/purchase/orders/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 提交：草稿 → 待审；version 用于乐观锁校验。 */
export const submitPurOrder = (
  id: number,
  version?: number,
  idempotencyKey?: string,
): Promise<PurOrder> =>
  request({
    url: `/purchase/orders/${id}/submit`,
    method: 'post',
    params: version === undefined ? undefined : { version },
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 审核：通过 → 已审；驳回 → 草稿并记录原因。 */
export const auditPurOrder = (
  id: number,
  payload: PurOrderAuditPayload,
  idempotencyKey?: string,
): Promise<PurOrder> =>
  request({
    url: `/purchase/orders/${id}/audit`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 关闭：剩余未入库数量作废，关闭后不可再入库。 */
export const closePurOrder = (
  id: number,
  payload: PurOrderClosePayload,
  idempotencyKey?: string,
): Promise<PurOrder> =>
  request({
    url: `/purchase/orders/${id}/close`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 按当前筛选条件导出 Excel（需 report:export），导出全量不带分页参数。 */
export const exportPurOrders = (params: PurOrderQuery): Promise<void> =>
  download(
    '/purchase/orders/export',
    toParams({
      keyword: params.keyword,
      status: params.status,
      supplierId: params.supplierId,
      orderDateStart: params.orderDateStart,
      orderDateEnd: params.orderDateEnd,
      sourceApplyId: params.sourceApplyId,
    }),
    '采购订单.xlsx',
  )
