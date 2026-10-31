/**
 * 系统管理-用户相关类型（与后端 UserOut / UserCreate / UserUpdate / UserQuery 对齐）。
 *
 * 字段命名约定：
 *  - 后端 Python 字段为 snake_case，对外 JSON 统一 camelCase，故此处字段名为 camelCase；
 *  - 前端不做字段名转换，直接消费后端返回的 camelCase 字段。
 */

import type { PageQuery } from '@/types/common'

/** 用户列表项（对应后端 UserOut）。 */
export interface User {
  /** 用户 id，主键。 */
  id: number
  /** 登录账号，全局唯一。 */
  username: string
  /** 真实姓名，未填写时为 null。 */
  realName: string | null
  /** 手机号，未填写时为 null。 */
  mobile: string | null
  /** 邮箱，未填写时为 null。 */
  email: string | null
  /** 所属部门 id，对应 sys_dept.id；未分配部门时为 null。 */
  deptId: number | null
  /** 岗位名称，自由文本。 */
  position: string | null
  /** 账号状态：1 启用、0 停用（停用后无法登录且已有 Token 失效）。 */
  status: number
  /** 已分配的角色 id 列表，对应 sys_user_role.role_id。 */
  roleIds: number[]
  /** 最后登录时间（ISO 8601 字符串），从未登录时为 null。 */
  lastLoginAt: string | null
  /** 创建时间（ISO 8601 字符串）。 */
  createdAt: string | null
  /** 更新时间（ISO 8601 字符串）。 */
  updatedAt: string | null
}

/** 用户列表查询参数（对应后端 UserQuery）。 */
export interface UserQuery extends PageQuery {
  /** 账号状态筛选：1 启用、0 停用；不筛选时传 null。 */
  status?: number | null
  /** 按部门 id 精确筛选；不筛选时传 null。 */
  deptId?: number | null
}

/** 新增用户请求体（对应后端 UserCreate）。 */
export interface UserCreatePayload {
  /** 登录账号，需全局唯一。 */
  username: string
  /** 初始密码，后端以 bcrypt 落库存储。 */
  password: string
  /** 真实姓名。 */
  realName?: string | null
  /** 手机号。 */
  mobile?: string | null
  /** 邮箱。 */
  email?: string | null
  /** 所属部门 id，必须为有效部门，否则后端返回 422。 */
  deptId?: number | null
  /** 岗位名称。 */
  position?: string | null
  /** 账号状态：1 启用、0 停用。 */
  status?: number
  /** 分配的角色 id 列表，必须全部存在，否则后端返回 422。 */
  roleIds?: number[]
}

/** 修改用户请求体（对应后端 UserUpdate，语义为部分更新，仅传需要变更的字段）。 */
export interface UserUpdatePayload {
  /** 真实姓名。 */
  realName?: string | null
  /** 手机号。 */
  mobile?: string | null
  /** 邮箱。 */
  email?: string | null
  /** 所属部门 id，必须为有效部门，否则后端返回 422。 */
  deptId?: number | null
  /** 岗位名称。 */
  position?: string | null
  /** 账号状态；变更状态会使该用户已签发的 Token 立即失效。 */
  status?: number
  /** 分配的角色 id 列表，传空数组表示清空角色。 */
  roleIds?: number[]
}
