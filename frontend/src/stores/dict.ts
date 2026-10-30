/** 字典全局缓存：登录后一次拉取全量分组字典；维护页改动后调用 fetchAll(true)。 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { getGroupedDicts } from '@/api/dict'
import type { DictOption } from '@/types/dict'

export const useDictStore = defineStore('dict', () => {
  const grouped = ref<Record<string, DictOption[]>>({})
  const loaded = ref(false)

  const fetchAll = async (force = false): Promise<void> => {
    if (loaded.value && !force) {
      return
    }
    grouped.value = await getGroupedDicts()
    loaded.value = true
  }

  const optionsOf = (typeCode: string): DictOption[] => grouped.value[typeCode] ?? []

  const labelOf = (typeCode: string, value: string | number | null | undefined): string => {
    if (value === null || value === undefined || value === '') {
      return ''
    }
    const hit = optionsOf(typeCode).find((item) => item.value === String(value))
    return hit?.label ?? String(value)
  }

  return { grouped, loaded, fetchAll, optionsOf, labelOf }
})
