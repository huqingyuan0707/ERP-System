/** 基础资料-仓库接口。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  ManagerOption,
  Warehouse,
  WarehouseCreatePayload,
  WarehouseQuery,
  WarehouseUpdatePayload,
} from '@/types/warehouse'
import { getUserList } from '@/api/user'

export const getWarehouseList = (params: WarehouseQuery): Promise<PageResult<Warehouse>> =>
  request({ url: '/base/warehouses', method: 'get', params })

export const createWarehouse = (payload: WarehouseCreatePayload): Promise<Warehouse> =>
  request({ url: '/base/warehouses', method: 'post', data: payload })

export const updateWarehouse = (id: number, payload: WarehouseUpdatePayload): Promise<Warehouse> =>
  request({ url: `/base/warehouses/${id}`, method: 'put', data: payload })

export const deleteWarehouse = (id: number): Promise<null> =>
  request({ url: `/base/warehouses/${id}`, method: 'delete' })

/** 获取全部未删除用户，作为仓库负责人下拉框选项。 */
export const fetchManagerOptions = async (): Promise<ManagerOption[]> => {
  const result = await getUserList({ page: 1, pageSize: 1000, status: 1 })
  return result.items.map((user) => ({
    value: user.id,
    label: user.realName ?? user.username,
  }))
}
