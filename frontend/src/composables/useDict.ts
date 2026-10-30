/** 字典组合式函数：useDict('yes_no') → { options, labelOf }，自动懒加载全局缓存。 */

import { computed } from 'vue'

import { useDictStore } from '@/stores/dict'
import type { DictOption } from '@/types/dict'

export const useDict = (typeCode: string) => {
  const store = useDictStore()
  if (!store.loaded) {
    void store.fetchAll()
  }
  const options = computed<DictOption[]>(() => store.grouped[typeCode] ?? [])
  const labelOf = (value: string | number | null | undefined): string =>
    store.labelOf(typeCode, value)
  return { options, labelOf }
}
