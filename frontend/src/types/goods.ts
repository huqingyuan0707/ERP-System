/** 基础资料-商品相关类型（字段 camelCase，与后端 Out/Create/Update 对齐）。 */

import type { PageQuery } from './common'

export type DecimalValue = string | number

export interface GoodsCategory {
  id: number
  name: string
  parentId: number | null
  sort: number
  remark: string | null
  createdAt: string | null
  /** 子分类数量：>0 时分类不可删除（后端同步返回） */
  childrenCount: number
  /** 关联商品数量：>0 时分类不可删除 */
  goodsCount: number
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
  /** 数量保留的小数位（0-4） */
  precision: number
  remark: string | null
  createdAt: string | null
  /** 引用该单位的商品数量：>0 时不可删除 */
  goodsCount: number
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
  costPrice: DecimalValue
  salePrice: DecimalValue
  stockLower: DecimalValue
  stockUpper: DecimalValue
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
  costPrice: DecimalValue
  salePrice: DecimalValue
  stockLower: DecimalValue
  stockUpper: DecimalValue
  status: number
  remark?: string | null
}

export type GoodsUpdatePayload = Partial<Omit<GoodsCreatePayload, 'code'>>

export interface GoodsQuery extends PageQuery {
  categoryId?: number | null
  status?: number | null
}

/** 导入错误行：row 为 Excel 行号，errors 为该行全部校验失败原因。 */
export interface GoodsImportError {
  row: number
  errors: string[]
}

/** 导入结果：success=false 表示整批未入库，errors 为逐行错误报告。 */
export interface GoodsImportResult {
  success: boolean
  total: number
  successCount: number
  failCount: number
  errors: GoodsImportError[]
}
