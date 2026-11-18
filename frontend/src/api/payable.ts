/** 应付管理接口（F-02）：只读查询，无编辑/删除调用。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  AgingBucket,
  Payable,
  PayableDetail,
  PayableQuery,
  PayableSummary,
} from '@/types/payable'

/** 过滤空值，避免 axios 把 null 序列化成空串导致后端 date/int 参数 422。 */
const toParams = (params: PayableQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询应付（后端自动叠加角色数据范围）。 */
export const getPayableList = (params: PayableQuery): Promise<PageResult<Payable>> =>
  request({ url: '/finance/payables', method: 'get', params: toParams(params) })

/** 汇总卡片：与列表同筛选口径。 */
export const getPayableSummary = (params: PayableQuery): Promise<PayableSummary> =>
  request({ url: '/finance/payables/summary', method: 'get', params: toParams(params) })

/** 账龄分桶：仅正数未结项分桶，负数单列。 */
export const getPayableAging = (params: PayableQuery): Promise<AgingBucket[]> =>
  request({ url: '/finance/payables/aging', method: 'get', params: toParams(params) })

/** 应付详情：含付款核销记录与冲减该应付的退货记录。 */
export const getPayableDetail = (id: number): Promise<PayableDetail> =>
  request({ url: `/finance/payables/${id}`, method: 'get' })
