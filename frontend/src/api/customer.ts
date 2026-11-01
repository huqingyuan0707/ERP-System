/** 基础资料-客户接口。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Customer,
  CustomerCreatePayload,
  CustomerCredit,
  CustomerQuery,
  CustomerUpdatePayload,
} from '@/types/customer'

/** 分页查询客户列表。 */
export const getCustomerList = (params: CustomerQuery): Promise<PageResult<Customer>> =>
  request({ url: '/base/customers', method: 'get', params })

/** 获取可用于新单据的客户（仅启用状态），供销售订单等模块下拉选择。 */
export const getCustomerOptions = (): Promise<Customer[]> =>
  request({ url: '/base/customers/options', method: 'get' })

/** 新增客户。 */
export const createCustomer = (payload: CustomerCreatePayload): Promise<Customer> =>
  request({ url: '/base/customers', method: 'post', data: payload })

/** 修改客户（部分更新）。 */
export const updateCustomer = (id: number, payload: CustomerUpdatePayload): Promise<Customer> =>
  request({ url: `/base/customers/${id}`, method: 'put', data: payload })

/** 删除客户（后端为软删除，有单据引用时返回 409）。 */
export const deleteCustomer = (id: number): Promise<null> =>
  request({ url: `/base/customers/${id}`, method: 'delete' })

/** 客户信用概况（信用额度/应收余额/可用额度/是否超限）。 */
export const getCustomerCredit = (id: number): Promise<CustomerCredit> =>
  request({ url: `/base/customers/${id}/credit`, method: 'get' })
