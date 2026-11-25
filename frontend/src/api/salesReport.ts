/** 销售报表接口（R-03）：查询与异步导出。 */

import { request } from '@/utils/request'
import type { SalesReportOut, SalesReportQuery } from '@/types/salesReport'

interface ExportTaskAccepted {
  taskId: string
  taskType: string
  status: number
}

const toParams = (params: SalesReportQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

export const getSalesReport = (params: SalesReportQuery): Promise<SalesReportOut> =>
  request({ url: '/reports/sales', method: 'get', params: toParams(params) })

export const exportSalesReport = (params: SalesReportQuery): Promise<ExportTaskAccepted> =>
  request({ url: '/reports/sales/export', method: 'post', params: toParams(params) })
