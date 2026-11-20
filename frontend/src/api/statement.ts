/** 对账单接口（F-06）：列表/详情/预览/生成，导出走 C-07 异步任务。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Statement,
  StatementBuild,
  StatementCreate,
  StatementQuery,
} from '@/types/statement'

interface ExportTaskAccepted {
  taskId: string
  taskType: string
  status: number
}

const toParams = (params: StatementQuery): Record<string, unknown> => {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') result[key] = value
  }
  return result
}

export const getStatementList = (params: StatementQuery): Promise<PageResult<Statement>> =>
  request({ url: '/finance/statements', method: 'get', params: toParams(params) })

export const getStatementDetail = (id: number): Promise<Statement> =>
  request({ url: `/finance/statements/${id}`, method: 'get' })

export const previewStatement = (payload: StatementCreate): Promise<StatementBuild> =>
  request({ url: '/finance/statements/preview', method: 'post', data: payload })

export const createStatement = (payload: StatementCreate): Promise<Statement> =>
  request({ url: '/finance/statements', method: 'post', data: payload })

export const exportStatement = (id: number): Promise<ExportTaskAccepted> =>
  request({ url: `/finance/statements/${id}/export`, method: 'get' })
