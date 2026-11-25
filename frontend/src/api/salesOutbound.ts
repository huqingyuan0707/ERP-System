/** 销售出库（SA-03）/退货（SA-04）接口。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Outbound,
  OutboundCreate,
  OutboundFromOrder,
  OutboundQuery,
  ReturnCreate,
  ReturnQuery,
  ReturnSource,
  SalesReturn,
} from '@/types/salesOutbound'

const toParams = (params: OutboundQuery | ReturnQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

// 销售出库
export const getOutbounds = (params: OutboundQuery): Promise<PageResult<Outbound>> =>
  request({ url: '/sales/outbounds', method: 'get', params: toParams(params) })
export const getOutbound = (id: number): Promise<Outbound> =>
  request({ url: `/sales/outbounds/${id}`, method: 'get' })
export const getOutboundFromOrder = (orderId: number): Promise<OutboundFromOrder> =>
  request({ url: `/sales/outbounds/from-order/${orderId}`, method: 'get' })
export const createOutbound = (data: OutboundCreate): Promise<Outbound> =>
  request({ url: '/sales/outbounds', method: 'post', data })
export const updateOutbound = (id: number, data: Partial<OutboundCreate> & { version: number }): Promise<Outbound> =>
  request({ url: `/sales/outbounds/${id}`, method: 'put', data })
export const deleteOutbound = (id: number): Promise<void> =>
  request({ url: `/sales/outbounds/${id}`, method: 'delete' })
export const confirmOutbound = (id: number): Promise<Outbound> =>
  request({ url: `/sales/outbounds/${id}/confirm`, method: 'post' })

// 销售退货
export const getReturns = (params: ReturnQuery): Promise<PageResult<SalesReturn>> =>
  request({ url: '/sales/returns', method: 'get', params: toParams(params) })
export const getReturn = (id: number): Promise<SalesReturn> =>
  request({ url: `/sales/returns/${id}`, method: 'get' })
export const getReturnSource = (sourceNo: string): Promise<ReturnSource> =>
  request({ url: `/sales/returns/source/${encodeURIComponent(sourceNo)}`, method: 'get' })
export const createReturn = (data: ReturnCreate): Promise<SalesReturn> =>
  request({ url: '/sales/returns', method: 'post', data })
export const updateReturn = (id: number, data: Partial<ReturnCreate> & { version: number }): Promise<SalesReturn> =>
  request({ url: `/sales/returns/${id}`, method: 'put', data })
export const deleteReturn = (id: number): Promise<void> =>
  request({ url: `/sales/returns/${id}`, method: 'delete' })
export const confirmReturn = (id: number): Promise<SalesReturn> =>
  request({ url: `/sales/returns/${id}/confirm`, method: 'post' })
