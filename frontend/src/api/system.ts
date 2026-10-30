/** 系统管理公共接口（部门等）。 */

import { request } from '@/utils/request'
import type { Dept } from '@/types/system'

export const getDeptList = (): Promise<Dept[]> =>
  request({ url: '/sys/depts', method: 'get' })
