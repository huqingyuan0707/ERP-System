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

/** 动态菜单节点（/auth/me 返回，用于生成动态路由与侧边栏）。 */
export interface MenuNode {
  id: number
  name: string
  routePath: string | null
  /** 视图组件路径（相对 src/views，如 base/goods/index）。 */
  component: string | null
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
