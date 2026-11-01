/** 用户状态：token、当前用户、权限码、角色与动态菜单。 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { getCurrentUser, login as loginApi, logout as logoutApi } from '@/api/auth'
import { clearToken, getToken, setToken } from '@/utils/request'
import type { CurrentUser, LoginPayload, MenuNode } from '@/types/auth'

const USER_KEY = 'erp_user'
const TENANT_KEY = 'erp_tenant_id'

/** 从 sessionStorage 恢复用户信息，避免刷新页面白屏重取；损坏数据静默忽略。 */
const loadStoredUser = (): CurrentUser | null => {
  try {
    const raw = sessionStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as CurrentUser) : null
  } catch {
    return null
  }
}

/** 从 sessionStorage 恢复租户 ID。 */
const loadStoredTenantId = (): number | null => {
  try {
    const raw = sessionStorage.getItem(TENANT_KEY)
    return raw ? parseInt(raw, 10) : null
  } catch {
    return null
  }
}

export const useUserStore = defineStore('user', () => {
  const storedUser = loadStoredUser()
  const storedTenantId = loadStoredTenantId()
  const token = ref<string>(getToken())
  const user = ref<CurrentUser | null>(storedUser)
  const tenantId = ref<number | null>(storedTenantId ?? storedUser?.tenantId ?? null)
  const permissions = ref<string[]>(storedUser?.permissions ?? [])
  const roles = ref<string[]>(storedUser?.roles ?? [])
  const menus = ref<MenuNode[]>(storedUser?.menus ?? [])

  const persistUser = (): void => {
    if (user.value) {
      sessionStorage.setItem(USER_KEY, JSON.stringify(user.value))
    } else {
      sessionStorage.removeItem(USER_KEY)
    }
  }

  const persistTenantId = (value: number | null): void => {
    if (value) {
      sessionStorage.setItem(TENANT_KEY, String(value))
    } else {
      sessionStorage.removeItem(TENANT_KEY)
    }
  }

  const hasPerm = (code: string): boolean =>
    permissions.value.includes('*') || permissions.value.includes(code)

  const login = async (payload: LoginPayload): Promise<void> => {
    const result = await loginApi(payload)
    token.value = result.accessToken
    setToken(result.accessToken)
    if (result.tenantId) {
      tenantId.value = result.tenantId
      persistTenantId(result.tenantId)
    }
  }

  const fetchUser = async (): Promise<CurrentUser> => {
    const me = await getCurrentUser()
    user.value = me
    tenantId.value = me.tenantId ?? null
    persistTenantId(tenantId.value)
    permissions.value = me.permissions
    roles.value = me.roles
    menus.value = me.menus
    persistUser()
    return me
  }

  const logout = async (): Promise<void> => {
    try {
      // 接口失败（含 token 已失效的 401）也照样清理本地状态
      await logoutApi()
    } catch {
      // 忽略登出接口异常，继续本地清理
    }
    token.value = ''
    user.value = null
    tenantId.value = null
    permissions.value = []
    roles.value = []
    menus.value = []
    clearToken()
    persistUser()
    persistTenantId(null)
  }

  return { token, user, tenantId, permissions, roles, menus, hasPerm, login, fetchUser, logout }
})
