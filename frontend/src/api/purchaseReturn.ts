/** 采购退货接口（P-04）：/purchase/returns 的列表、详情、原单可退查询、草稿 CRUD 与确认。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  PurReturn,
  PurReturnCreatePayload,
  PurReturnQuery,
  PurReturnUpdatePayload,
  SourceInbound,
} from '@/types/purchaseReturn'

/** 过滤空值，避免 axios 把 null 序列化成空字符串导致后端 date/int 参数 422。 */
const toParams = (params: PurReturnQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询采购退货单（后端自动叠加角色数据范围）。 */
export const getPurReturnList = (params: PurReturnQuery): Promise<PageResult<PurReturn>> =>
  request({ url: '/purchase/returns', method: 'get', params: toParams(params) })

/** 采购退货单详情（含明细与原入库/已退/可退数量，供详情与打印取单据快照）。 */
export const getPurReturnDetail = (id: number): Promise<PurReturn> =>
  request({ url: `/purchase/returns/${id}`, method: 'get' })

/** 按原入库单号查询可退数据（原入库/已退/可退数量与应付快照）。
 *  注意：后端该接口为普通 query 参数（snake_case），不要传 sourceNo。 */
export const getReturnSource = (sourceNo: string): Promise<SourceInbound> =>
  request({ url: '/purchase/returns/source', method: 'get', params: { source_no: sourceNo } })

/** 新增采购退货单（保存即草稿），金额由后端按明细重算。 */
export const createPurReturn = (
  payload: PurReturnCreatePayload,
  idempotencyKey?: string,
): Promise<PurReturn> =>
  request({
    url: '/purchase/returns',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改草稿（非草稿或版本冲突返回 409）。 */
export const updatePurReturn = (
  id: number,
  payload: PurReturnUpdatePayload,
  idempotencyKey?: string,
): Promise<PurReturn> =>
  request({
    url: `/purchase/returns/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除草稿（后端软删除，明细保留流水）。 */
export const deletePurReturn = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/purchase/returns/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 确认退货：校验可退量/可用量/退货金额 → 减库存写流水 → 冲减原应付（违反均 409）。 */
export const confirmPurReturn = (id: number, idempotencyKey?: string): Promise<PurReturn> =>
  request({
    url: `/purchase/returns/${id}/confirm`,
    method: 'post',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
