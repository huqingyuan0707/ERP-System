/** 采购入库接口（P-03）：/purchase/inbounds 的列表、详情、按订单预填、草稿 CRUD 与确认。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  PurInbound,
  PurInboundCreatePayload,
  PurInboundFromOrder,
  PurInboundQuery,
  PurInboundUpdatePayload,
} from '@/types/purchaseInbound'

/** 过滤空值，避免 axios 把 null 序列化成空字符串导致后端 date/int 参数 422。 */
const toParams = (params: PurInboundQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询采购入库单（后端自动叠加角色数据范围）。 */
export const getPurInboundList = (params: PurInboundQuery): Promise<PageResult<PurInbound>> =>
  request({ url: '/purchase/inbounds', method: 'get', params: toParams(params) })

/** 采购入库单详情（含明细，供详情与打印取单据快照）。 */
export const getPurInboundDetail = (id: number): Promise<PurInbound> =>
  request({ url: `/purchase/inbounds/${id}`, method: 'get' })

/** 按已审/部分入库订单生成入库单预填数据（带出剩余数量与订单单价）。 */
export const getInboundFromOrder = (orderId: number): Promise<PurInboundFromOrder> =>
  request({ url: `/purchase/inbounds/from-order/${orderId}`, method: 'get' })

/** 新增采购入库单（保存即草稿），单价/金额由后端按订单明细重算。 */
export const createPurInbound = (
  payload: PurInboundCreatePayload,
  idempotencyKey?: string,
): Promise<PurInbound> =>
  request({
    url: '/purchase/inbounds',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改草稿（非草稿或版本冲突返回 409）。 */
export const updatePurInbound = (
  id: number,
  payload: PurInboundUpdatePayload,
  idempotencyKey?: string,
): Promise<PurInbound> =>
  request({
    url: `/purchase/inbounds/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除草稿（后端软删除，明细保留流水）。 */
export const deletePurInbound = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/purchase/inbounds/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 确认入库：同一事务增加库存并写流水、回写订单、生成应付（重复确认 409）。 */
export const confirmPurInbound = (id: number, idempotencyKey?: string): Promise<PurInbound> =>
  request({
    url: `/purchase/inbounds/${id}/confirm`,
    method: 'post',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
