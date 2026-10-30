/** 系统参数类型（与后端 ConfigOut/ConfigBatchUpdate 对齐）。 */

export interface SysConfig {
  key: string
  value: string | null
  name: string
  remark: string | null
}

export interface ConfigUpdateItemPayload {
  key: string
  value: string | null
}

export interface ConfigBatchUpdatePayload {
  items: ConfigUpdateItemPayload[]
}
