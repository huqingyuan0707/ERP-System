/** 异步任务与导入导出接口（C-07）。 */

import { download, request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Task,
  TaskQuery,
} from '@/types/log'

/** 任务列表 */
export const getTaskList = (params: TaskQuery, signal?: AbortSignal): Promise<PageResult<Task>> =>
  request({ url: '/sys/tasks', method: 'get', params, signal, _silentAuth: true })

/** 任务详情（轮询401直接停止，不触发后台无限刷新） */
export const getTaskDetail = (taskId: string, signal?: AbortSignal): Promise<Task> =>
  request({ url: `/sys/tasks/${encodeURIComponent(taskId)}`, method: 'get', signal, _silentAuth: true })

export const isTerminalTaskError = (error: unknown): boolean => {
  const status = (error as { response?: { status?: number } })?.response?.status
  return status === 401 || status === 403 || status === 404
}

/** 首选按taskId下载，避免依赖服务器路径分隔符和文件名猜测。 */
export const downloadTaskFile = (task: Task): Promise<void> =>
  download(`/sys/tasks/${encodeURIComponent(task.taskId)}/download`, {},
    task.filePath?.split(/[\\/]/).pop() ?? '导出.xlsx')

/** 商品导出：提交异步任务，返回 taskId */
export const exportGoods = (params: {
  keyword?: string | null
  categoryId?: number | null
  status?: number | null
}): Promise<{ taskId: string; taskType: string; status: number }> =>
  request({
    url: '/base/goods/export',
    method: 'get',
    params,
  })

/** 库存导出：提交异步任务 */
export const exportStocks = (params: {
  warehouseId?: number | null
  keyword?: string | null
}): Promise<{ taskId: string; taskType: string; status: number }> =>
  request({
    url: '/inventory/stocks/export',
    method: 'get',
    params,
  })

/** 报表导出：提交异步任务 */
export const exportReport = (reportType: string): Promise<{
  taskId: string
  taskType: string
  status: number
}> =>
  request({
    url: '/reports/export',
    method: 'post',
    params: { reportType },
  })

/** 对账单导出：提交异步任务 */
export const exportStatement = (statementId: number): Promise<{
  taskId: string
  taskType: string
  status: number
}> =>
  request({
    url: `/finance/statements/${statementId}/export`,
    method: 'get',
  })

/** 下载导出文件（任务完成后调用） */
export const downloadExportFile = (fileName: string): Promise<void> =>
  download(`/exports/download`, { fileName }, fileName)
