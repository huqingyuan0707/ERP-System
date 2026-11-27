/** 库存接口（I-01/I-02）：实时库存、库存汇总、出入库流水。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  FlowItem,
  FlowQuery,
  StockItem,
  StockQuery,
  StockSummary,
} from '@/types/inventory'

const toParams = (params: object): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

export const getStocks = (params: StockQuery): Promise<PageResult<StockItem>> =>
  request({ url: '/inventory/stocks', method: 'get', params: toParams(params) })

export const getStockSummary = (params: StockQuery): Promise<StockSummary> =>
  request({ url: '/inventory/stocks/summary', method: 'get', params: toParams(params) })

export const getFlows = (params: FlowQuery): Promise<PageResult<FlowItem>> =>
  request({ url: '/inventory/flows', method: 'get', params: toParams(params) })
