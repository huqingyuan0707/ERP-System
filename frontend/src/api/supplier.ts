/** 基础资料-供应商接口。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Supplier,
  SupplierCreatePayload,
  SupplierQuery,
  SupplierUpdatePayload,
} from '@/types/supplier'

/** 分页查询供应商列表。 */
export const getSupplierList = (params: SupplierQuery): Promise<PageResult<Supplier>> =>
  request({ url: '/base/suppliers', method: 'get', params })

/** 获取可用于新单据的供应商（仅启用状态），供采购订单等模块下拉选择。 */
export const getSupplierOptions = (): Promise<Supplier[]> =>
  request({ url: '/base/suppliers/options', method: 'get' })

/** 新增供应商。 */
export const createSupplier = (payload: SupplierCreatePayload): Promise<Supplier> =>
  request({ url: '/base/suppliers', method: 'post', data: payload })

/** 修改供应商（部分更新）。 */
export const updateSupplier = (id: number, payload: SupplierUpdatePayload): Promise<Supplier> =>
  request({ url: `/base/suppliers/${id}`, method: 'put', data: payload })

/** 删除供应商（后端为软删除，有单据引用时返回 409）。 */
export const deleteSupplier = (id: number): Promise<null> =>
  request({ url: `/base/suppliers/${id}`, method: 'delete' })
