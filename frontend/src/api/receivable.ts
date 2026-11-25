/** 应收管理接口（F-01）：只读查询，无编辑/删除调用。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  AgingBucket,
  Receivable,
  ReceivableDetail,
  ReceivableQuery,
  ReceivableSummary,
} from '@/types/receivable'

const toParams = (params: ReceivableQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 分页查询应收（后端自动叠加角色数据范围）。 */
export const getReceivableList = (params: ReceivableQuery): Promise<PageResult<Receivable>> =>
  request({ url: '/finance/receivables', method: 'get', params: toParams(params) })

/** 汇总卡片：与列表同筛选口径。 */
export const getReceivableSummary = (
  params: ReceivableQuery,
): Promise<ReceivableSummary> =>
  request({ url: '/finance/receivables/summary', method: 'get', params: toParams(params) })

/** 账龄分桶：仅正数未结项分桶，负数单列。 */
export const getReceivableAging = (params: ReceivableQuery): Promise<AgingBucket[]> =>
  request({ url: '/finance/receivables/aging', method: 'get', params: toParams(params) })

/** 应收详情：含收款核销记录与冲减该应收的销售退货记录。 */
export const getReceivableDetail = (id: number): Promise<ReceivableDetail> =>
  request({ url: `/finance/receivables/${id}`, method: 'get' })
