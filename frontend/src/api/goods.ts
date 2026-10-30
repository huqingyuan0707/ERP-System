/** 基础资料-商品/分类/单位接口。 */

import { download, request } from '@/utils/request'
import type { PageResult } from '@/types/common'
import type {
  Goods,
  GoodsCategory,
  GoodsCategoryPayload,
  GoodsCreatePayload,
  GoodsQuery,
  GoodsUpdatePayload,
  Unit,
  UnitPayload,
} from '@/types/goods'

export const getGoodsList = (params: GoodsQuery): Promise<PageResult<Goods>> =>
  request({ url: '/base/goods', method: 'get', params })

export const getGoodsDetail = (id: number): Promise<Goods> =>
  request({ url: `/base/goods/${id}`, method: 'get' })

export const createGoods = (payload: GoodsCreatePayload): Promise<Goods> =>
  request({ url: '/base/goods', method: 'post', data: payload })

export const updateGoods = (id: number, payload: GoodsUpdatePayload): Promise<Goods> =>
  request({ url: `/base/goods/${id}`, method: 'put', data: payload })

export const deleteGoods = (id: number): Promise<null> =>
  request({ url: `/base/goods/${id}`, method: 'delete' })

export const exportGoods = (params: GoodsQuery): Promise<void> =>
  download('/base/goods/export', { ...params }, '商品列表.xlsx')

export const getCategories = (): Promise<GoodsCategory[]> =>
  request({ url: '/base/categories', method: 'get', params: { pageSize: 100 } })

export const createCategory = (payload: GoodsCategoryPayload): Promise<GoodsCategory> =>
  request({ url: '/base/categories', method: 'post', data: payload })

export const updateCategory = (id: number, payload: Partial<GoodsCategoryPayload>): Promise<GoodsCategory> =>
  request({ url: `/base/categories/${id}`, method: 'put', data: payload })

export const deleteCategory = (id: number): Promise<null> =>
  request({ url: `/base/categories/${id}`, method: 'delete' })

export const getUnits = (): Promise<Unit[]> =>
  request({ url: '/base/units', method: 'get', params: { pageSize: 100 } })

export const createUnit = (payload: UnitPayload): Promise<Unit> =>
  request({ url: '/base/units', method: 'post', data: payload })

export const updateUnit = (id: number, payload: Partial<UnitPayload>): Promise<Unit> =>
  request({ url: `/base/units/${id}`, method: 'put', data: payload })

export const deleteUnit = (id: number): Promise<null> =>
  request({ url: `/base/units/${id}`, method: 'delete' })
