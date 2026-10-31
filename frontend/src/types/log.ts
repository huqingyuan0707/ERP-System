/** 系统日志与异步任务类型（C-06、C-07）。 */

export interface OperLog {
  id: number
  userId: number | null
  userName: string | null
  module: string | null
  action: string | null
  method: string | null
  url: string | null
  ip: string | null
  params: string | null
  result: string | null
  durationMs: number | null
  status: number // 1=成功，0=失败
  createdAt: string | null
}

export interface OperLogQuery {
  page?: number
  pageSize?: number
  keyword?: string
  module?: string | null
  action?: string | null
  userId?: number | null
  status?: number | null
  startDate?: string | null // YYYY-MM-DD
  endDate?: string | null // YYYY-MM-DD
}

export interface LoginLog {
  id: number
  username: string
  ip: string | null
  userAgent: string | null
  status: number // 1=成功，0=失败
  message: string | null
  createdAt: string | null
}

export interface LoginLogQuery {
  page?: number
  pageSize?: number
  keyword?: string
  username?: string | null
  status?: number | null
  startDate?: string | null
  endDate?: string | null
}

/** 异步任务（C-07）。 */
export interface Task {
  id: number
  taskId: string
  taskType: string
  status: number // 1=待执行，2=执行中，3=成功，4=失败
  params: string | null
  filePath: string | null
  errorMsg: string | null
  progress: number
  createdBy: number | null
  startedAt: string | null
  finishedAt: string | null
  createdAt: string | null
}

export interface TaskQuery {
  page?: number
  pageSize?: number
  taskType?: string | null
  status?: number | null
}

export interface TaskCreatePayload {
  taskType: string
  params?: string | null
  createdBy?: number | null
}
