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

export const getWarehouseList = (params: WarehouseQuery): Promise<PageResult<Warehouse>> =>
  request({ url: '/base/warehouses', method: 'get', params })

export const createWarehouse = (payload: WarehouseCreatePayload, idempotencyKey?: string): Promise<Warehouse> =>
  request({ url: '/base/warehouses', method: 'post', data: payload, headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined })

export const updateWarehouse = (id: number, payload: WarehouseUpdatePayload, idempotencyKey?: string): Promise<Warehouse> =>
  request({ url: `/base/warehouses/${id}`, method: 'put', data: payload, headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined })

export const deleteWarehouse = (id: number, idempotencyKey?: string): Promise<null> =>
  request({ url: `/base/warehouses/${id}`, method: 'delete', headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined })

/** 只查询本租户最小负责人选项，不需要 sys:user:list。暂用用户，员工待 B06。 */
export const fetchManagerOptions = (keyword?: string): Promise<ManagerOption[]> =>
  request({ url: '/base/warehouses/manager-options', method: 'get', params: { keyword } })
