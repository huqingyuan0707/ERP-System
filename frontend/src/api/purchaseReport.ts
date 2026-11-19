/** 采购报表接口（R-02）：查询与异步导出。 */

import { request } from '@/utils/request'
import type {
  PurchaseReportOut,
  PurchaseReportQuery,
} from '@/types/purchaseReport'
interface ExportTaskAccepted {
  taskId: string
  taskType: string
  status: number
}

/** 过滤空值，避免日期/数字空串导致后端 422。 */
const toParams = (params: PurchaseReportQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

/** 采购报表查询（明细/汇总/排行共用）。 */
export const getPurchaseReport = (
  params: PurchaseReportQuery,
): Promise<PurchaseReportOut> =>
  request({ url: '/reports/purchase', method: 'get', params: toParams(params) })

/** 提交导出异步任务，返回 taskId 供轮询下载。 */
export const exportPurchaseReport = (
  params: PurchaseReportQuery,
): Promise<ExportTaskAccepted> =>
  request({ url: '/reports/purchase/export', method: 'post', params: toParams(params) })
