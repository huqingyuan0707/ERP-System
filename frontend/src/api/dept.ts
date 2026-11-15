/** 基础资料-部门接口（B-06）。 */

import { request } from '@/utils/request'
import type {
  Dept,
  DeptCreatePayload,
  DeptSortItem,
  DeptUpdatePayload,
} from '@/types/dept'

/** 部门树扁平列表（按 sort 排序，附引用计数）。 */
export const getDeptTree = (): Promise<Dept[]> =>
  request({ url: '/base/depts', method: 'get' })

/** 新增部门；名称重复 409，上级无效/成环 422。 */
export const createDept = (payload: DeptCreatePayload, idempotencyKey?: string): Promise<Dept> =>
  request({
    url: '/base/depts',
    method: 'post',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 修改部门（部分更新）。 */
export const updateDept = (
  id: number,
  payload: DeptUpdatePayload,
  idempotencyKey?: string,
): Promise<Dept> =>
  request({
    url: `/base/depts/${id}`,
    method: 'put',
    data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 删除部门（软删；存在子部门/员工/用户/角色引用时 409）。 */
export const deleteDept = (id: number, idempotencyKey?: string): Promise<null> =>
  request({
    url: `/base/depts/${id}`,
    method: 'delete',
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

/** 批量调整同级部门排序，整体在一个事务内生效。 */
export const reorderDepts = (items: DeptSortItem[], idempotencyKey?: string): Promise<null> =>
  request({
    url: '/base/depts/sort',
    method: 'put',
    data: { items },
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
