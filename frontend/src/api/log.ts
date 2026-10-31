/** 系统日志接口（C-06）。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  LoginLog,
  LoginLogQuery,
  OperLog,
  OperLogQuery,
} from '@/types/log'

export const getOperLogList = (params: OperLogQuery): Promise<PageResult<OperLog>> =>
  request({ url: '/sys/logs/oper', method: 'get', params })

export const getLoginLogList = (params: LoginLogQuery): Promise<PageResult<LoginLog>> =>
  request({ url: '/sys/logs/login', method: 'get', params })
