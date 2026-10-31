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
  // 后端约定 pageSize<=100，这里按 100 一页循环拉取全部启用用户
  const pageSize = 100
  const items: ManagerOption[] = []
  for (let page = 1; ; page += 1) {
    const result = await getUserList({ page, pageSize, status: 1 })
    items.push(
      ...result.items.map((user) => ({
        value: user.id,
        label: user.realName ?? user.username,
      })),
    )
    if (items.length >= result.total || result.items.length === 0) break
  }
  return items
}
