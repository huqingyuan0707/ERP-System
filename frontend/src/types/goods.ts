/** 基础资料-商品相关类型（字段 camelCase，与后端 Out/Create/Update 对齐）。 */

import type { PageQuery } from './common'

export interface GoodsCategory {
  id: number
  name: string
  parentId: number | null
  sort: number
  remark: string | null
  createdAt: string | null
}

export interface GoodsCategoryPayload {
  name: string
  parentId?: number | null
  sort?: number
  remark?: string | null
}

export interface Unit {
  id: number
  name: string
  precision: number
  remark: string | null
  createdAt: string | null
}

export interface UnitPayload {
  name: string
  precision?: number
  remark?: string | null
}

export interface Goods {
  id: number
  code: string
  name: string
  categoryId: number | null
  categoryName: string | null
  spec: string | null
  unitId: number | null
  unitName: string | null
  barcode: string | null
  costPrice: number
  salePrice: number
  stockLower: number
  stockUpper: number
  status: number
  remark: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface GoodsCreatePayload {
  code?: string | null
  name: string
  categoryId?: number | null
  spec?: string | null
  unitId?: number | null
  barcode?: string | null
  costPrice: number
  salePrice: number
  stockLower: number
  stockUpper: number
  status: number
  remark?: string | null
}

export type GoodsUpdatePayload = Partial<GoodsCreatePayload>

export interface GoodsQuery extends PageQuery {
  categoryId?: number | null
  status?: number | null
}
