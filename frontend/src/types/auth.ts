/** 认证相关类型。 */

export interface LoginPayload {
  username: string
  password: string
}

export interface TokenResult {
  accessToken: string
  tokenType: string
  expiresIn: number
}

/** 动态菜单节点（/auth/me 返回，本任务仅存储备用）。 */
export interface MenuNode {
  id: number
  name: string
  routePath: string | null
  icon: string | null
  children: MenuNode[]
}

export interface PasswordChangePayload {
  oldPassword: string
  newPassword: string
}

export interface CurrentUser {
  id: number
  username: string
  realName: string | null
  roles: string[]
  permissions: string[]
  menus: MenuNode[]
}
