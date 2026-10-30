/** 数据字典接口。 */

import { request } from '@/utils/request'
import type {
  DictItem,
  DictItemPayload,
  DictOption,
  DictType,
  DictTypePayload,
} from '@/types/dict'

export const getGroupedDicts = (): Promise<Record<string, DictOption[]>> =>
  request({ url: '/sys/dicts', method: 'get' })

export const getDictTypes = (): Promise<DictType[]> =>
  request({ url: '/sys/dicts/types', method: 'get' })

export const createDictType = (payload: DictTypePayload): Promise<DictType> =>
  request({ url: '/sys/dicts/types', method: 'post', data: payload })

export const updateDictType = (id: number, payload: Partial<DictTypePayload>): Promise<DictType> =>
  request({ url: `/sys/dicts/types/${id}`, method: 'put', data: payload })

export const deleteDictType = (id: number): Promise<null> =>
  request({ url: `/sys/dicts/types/${id}`, method: 'delete' })

export const getDictItems = (typeCode: string): Promise<DictItem[]> =>
  request({ url: `/sys/dicts/types/${typeCode}/items`, method: 'get' })

export const createDictItem = (typeCode: string, payload: DictItemPayload): Promise<DictItem> =>
  request({ url: `/sys/dicts/types/${typeCode}/items`, method: 'post', data: payload })

export const updateDictItem = (id: number, payload: Partial<DictItemPayload>): Promise<DictItem> =>
  request({ url: `/sys/dicts/items/${id}`, method: 'put', data: payload })

export const deleteDictItem = (id: number): Promise<null> =>
  request({ url: `/sys/dicts/items/${id}`, method: 'delete' })
