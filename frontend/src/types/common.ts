/** 通用类型：统一响应与分页契约（与后端 G-01/G-02 对齐）。 */

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface PageResult<T> {
  total: number
  page: number
  pageSize: number
  items: T[]
}

export interface PageQuery {
  page?: number
  pageSize?: number
  keyword?: string
}
