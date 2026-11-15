/** 基础资料-员工接口（B-06）。 */

import { request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Employee,
  EmployeeCreatePayload,
  EmployeeQuery,
  EmployeeUpdatePayload,
  UserOption,
} from '@/types/employee'

/** 分页查询员工列表。 */
export const getEmployeeList = (params: EmployeeQuery): Promise<PageResult<Employee>> =>
  request({ url: '/base/employees', method: 'get', params })

/** 新单据可选员工（仅启用状态）。 */
export const getEmployeeOptions = (): Promise<Employee[]> =>
  request({ url: '/base/employees/options', method: 'get' })

/** 可关联的系统用户，返回已被占用的账号标记。 */
export const getUserOptions = (): Promise<UserOption[]> =>
  request({ url: '/base/employees/user-options', method: 'get' })

/** 新增员工；编码重复 409，部门/用户无效 422。 */
export const createEmployee = (
  payload: EmployeeCreatePayload,
  idempotencyKey?: string,
): Promise<Employee> =>
  request({
    url: '/base/employees',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改员工（部分更新，含启停与关联用户）。 */
export const updateEmployee = (
  id: number,
  payload: EmployeeUpdatePayload,
  idempotencyKey?: string,
): Promise<Employee> =>
  request({
    url: `/base/employees/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除员工（软删，历史单据不受影响）。 */
export const deleteEmployee = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/base/employees/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
