/** 按钮级权限指令：v-perm="'base:goods:add'"，无权限时移除元素。 */
import type { Directive } from 'vue'

import { useUserStore } from '@/stores/user'

const perm: Directive<HTMLElement, string | string[]> = {
  mounted(el, binding) {
    const required = Array.isArray(binding.value) ? binding.value : [binding.value]
    const userStore = useUserStore()
    if (!required.every((code) => userStore.hasPerm(code))) {
      el.parentNode?.removeChild(el)
    }
  },
}

export default perm
