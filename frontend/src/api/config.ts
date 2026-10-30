/** 系统参数接口。 */

import { request } from '@/utils/request'
import type { ConfigBatchUpdatePayload, SysConfig } from '@/types/config'

export const getConfigs = (): Promise<SysConfig[]> =>
  request({ url: '/sys/configs', method: 'get' })

export const updateConfigs = (payload: ConfigBatchUpdatePayload): Promise<SysConfig[]> =>
  request({ url: '/sys/configs', method: 'put', data: payload })
