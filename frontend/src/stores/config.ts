/** 系统参数全局缓存：key → value，参数页保存后调用 fetchAll(true) 刷新。 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { getConfigs } from '@/api/config'

export const useConfigStore = defineStore('config', () => {
  const map = ref<Record<string, string>>({})
  const loaded = ref(false)

  const fetchAll = async (force = false): Promise<void> => {
    if (loaded.value && !force) {
      return
    }
    const rows = await getConfigs()
    const next: Record<string, string> = {}
    for (const row of rows) {
      next[row.key] = row.value ?? ''
    }
    map.value = next
    loaded.value = true
  }

  const valueOf = (key: string, defaultValue = ''): string => map.value[key] || defaultValue

  const invalidate = (): void => {
    loaded.value = false
  }

  return { map, loaded, fetchAll, valueOf, invalidate }
})
