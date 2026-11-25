/** 收款管理接口（F-03）：/finance/receipts 的列表、汇总、详情、CRUD 与核销。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Receipt,
  ReceiptCreatePayload,
  ReceiptQuery,
  ReceiptSummary,
  ReceiptUpdatePayload,
  WriteoffPayload,
} from '@/types/receipt'

const toParams = (params: ReceiptQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

export const getReceiptList = (params: ReceiptQuery): Promise<PageResult<Receipt>> =>
  request({ url: '/finance/receipts', method: 'get', params: toParams(params) })

export const getReceiptSummary = (params: ReceiptQuery): Promise<ReceiptSummary> =>
  request({ url: '/finance/receipts/summary', method: 'get', params: toParams(params) })

export const getReceiptDetail = (id: number): Promise<Receipt> =>
  request({ url: `/finance/receipts/${id}`, method: 'get' })

export const createReceipt = (
  payload: ReceiptCreatePayload,
  idempotencyKey?: string,
): Promise<Receipt> =>
  request({
    url: '/finance/receipts',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const updateReceipt = (
  id: number,
  payload: ReceiptUpdatePayload,
  idempotencyKey?: string,
): Promise<Receipt> =>
  request({
    url: `/finance/receipts/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const deleteReceipt = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/finance/receipts/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const writeoffReceipt = (
  id: number,
  payload: WriteoffPayload,
  idempotencyKey?: string,
): Promise<Receipt> =>
  request({
    url: `/finance/receipts/${id}/writeoff`,
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
