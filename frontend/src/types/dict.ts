/** 数据字典类型（与后端 DictTypeOut/DictItemOut 对齐）。 */

export interface DictType {
  id: number
  code: string
  name: string
  status: number
  remark: string | null
}

export interface DictTypePayload {
  code?: string
  name: string
  status?: number
  remark?: string | null
}

export interface DictItem {
  id: number
  typeCode: string
  label: string
  value: string
  sort: number
  status: number
  remark: string | null
}

export interface DictItemPayload {
  label: string
  value: string
  sort?: number
  status?: number
  remark?: string | null
}

/** 字典下拉选项（/sys/dicts 分组返回 {typeCode: [{label, value}]}）。 */
export interface DictOption {
  label: string
  value: string
}
