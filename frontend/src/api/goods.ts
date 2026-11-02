/** 基础资料-商品/分类/单位接口。 */

import { download, request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Goods,
  GoodsCategory,
  GoodsCategoryPayload,
  GoodsCreatePayload,
  GoodsImportResult,
  GoodsQuery,
  GoodsUpdatePayload,
  Unit,
  UnitPayload,
} from '@/types/goods'

export const getGoodsList = (params: GoodsQuery): Promise<PageResult<Goods>> =>
  request({ url: '/base/goods', method: 'get', params })

export const getGoodsDetail = (id: number): Promise<Goods> =>
  request({ url: `/base/goods/${id}`, method: 'get' })

export const createGoods = (payload: GoodsCreatePayload, idempotencyKey?: string): Promise<Goods> =>
  request({
    url: '/base/goods', method: 'post', data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const updateGoods = (id: number, payload: GoodsUpdatePayload): Promise<Goods> =>
  request({ url: `/base/goods/${id}`, method: 'put', data: payload })

export const deleteGoods = (id: number): Promise<null> =>
  request({ url: `/base/goods/${id}`, method: 'delete' })

/** 下载商品导入模板（需要登录态，走统一下载封装以携带 Token）。 */
export const downloadGoodsTemplate = (): Promise<void> =>
  download('/base/goods/template', {}, '商品导入模板.xlsx')

/** 商品导入：后端逐行校验，全部通过才入库；失败返回逐行错误报告。 */
export const importGoods = (file: File): Promise<GoodsImportResult> => {
  const formData = new FormData()
  formData.append('file', file)
  return request({ url: '/base/goods/import', method: 'post', data: formData })
}

export const getCategories = (): Promise<GoodsCategory[]> =>
  request({ url: '/base/categories', method: 'get', params: { pageSize: 100 } })

export const createCategory = (payload: GoodsCategoryPayload, idempotencyKey?: string): Promise<GoodsCategory> =>
  request({
    url: '/base/categories', method: 'post', data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const updateCategory = (id: number, payload: Partial<GoodsCategoryPayload>): Promise<GoodsCategory> =>
  request({ url: `/base/categories/${id}`, method: 'put', data: payload })

export const deleteCategory = (id: number): Promise<null> =>
  request({ url: `/base/categories/${id}`, method: 'delete' })

export const getUnits = (): Promise<Unit[]> =>
  request({ url: '/base/units', method: 'get', params: { pageSize: 100 } })

export const createUnit = (payload: UnitPayload, idempotencyKey?: string): Promise<Unit> =>
  request({
    url: '/base/units', method: 'post', data: payload,
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })

export const updateUnit = (id: number, payload: Partial<UnitPayload>): Promise<Unit> =>
  request({ url: `/base/units/${id}`, method: 'put', data: payload })

export const deleteUnit = (id: number): Promise<null> =>
  request({ url: `/base/units/${id}`, method: 'delete' })
