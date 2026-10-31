/** 路由：静态壳（登录/布局/403）+ 按 /auth/me 菜单动态注册业务路由。 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import type { Component } from 'vue'

import { useUserStore } from '@/stores/user'
import { getToken } from '@/utils/request'
import type { MenuNode } from '@/types/auth'

/** 未开发功能页的占位视图。 */
const PlaceholderPage = (): Promise<Component> => import('@/views/common/PlaceholderPage.vue')

/** 视图模块表：key 形如 ../views/base/goods/index.vue。 */
const viewModules = import.meta.glob('../views/**/*.vue')

const resolveView = (component: string | null): (() => Promise<Component>) => {
  if (!component) return PlaceholderPage
  const loader = viewModules[`../views/${component}.vue`]
  return loader
    ? (loader as () => Promise<Component>)
    : PlaceholderPage
}

/** 将菜单叶子节点展开为布局子路由（目录本身不可导航）。 */
const buildMenuRoutes = (menus: MenuNode[]): RouteRecordRaw[] => {
  const routes: RouteRecordRaw[] = []
  for (const menu of menus) {
    if (menu.children.length > 0) {
      routes.push(...buildMenuRoutes(menu.children))
      continue
    }
    if (menu.routePath) {
      routes.push({
        path: menu.routePath,
        name: `menu-${menu.id}`,
        component: resolveView(menu.component),
        meta: { title: menu.name },
      })
    }
  }
  return routes
}

/** 取第一个可导航菜单路径，作为登录后落地页。 */
const firstMenuPath = (menus: MenuNode[]): string | null => {
  for (const menu of menus) {
    if (menu.children.length > 0) {
      const childPath = firstMenuPath(menu.children)
      if (childPath) return childPath
    } else if (menu.routePath) {
      return menu.routePath
    }
  }
  return null
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/LoginPage.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    name: 'Layout',
    component: () => import('@/layouts/BasicLayout.vue'),
    children: [],
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: () => import('@/views/error/ForbiddenPage.vue'),
    meta: { public: true, title: '无权限' },
  },
  {
    // 兜底路由：动态菜单路由注册前先命中此处，避免 matcher 报 No match 警告；
    // 守卫注册路由后会按原路径重新解析，最终仍未匹配则跳转 /403。
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/ForbiddenPage.vue'),
    meta: { title: '无权限' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

/** 动态路由是否已按当前用户菜单注册（登出后重置）。 */
let menuRoutesAdded = false

/** 移除已注册的动态路由，防止切换账号后残留上一用户的路由。 */
const removeDynamicRoutes = (): void => {
  for (const record of router.getRoutes()) {
    const name = record.name
    if (name === 'Home' || (typeof name === 'string' && name.startsWith('menu-'))) {
      router.removeRoute(name)
    }
  }
  menuRoutesAdded = false
}

router.beforeEach(async (to) => {
  document.title = to.meta.title ? `${to.meta.title} · 企业ERP系统` : '企业ERP系统'
  if (to.meta.public) {
    if (to.path === '/login') {
      removeDynamicRoutes()
    }
    return true
  }

  if (!getToken()) {
    removeDynamicRoutes()
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  const userStore = useUserStore()
  if (!userStore.user) {
    try {
      await userStore.fetchUser()
    } catch {
      return { path: '/login' }
    }
  }

  if (!menuRoutesAdded) {
    for (const route of buildMenuRoutes(userStore.menus)) {
      router.addRoute('Layout', route)
    }
    router.addRoute('Layout', {
      path: '',
      name: 'Home',
      redirect: firstMenuPath(userStore.menus) ?? '/403',
    })
    menuRoutesAdded = true
    // 以新注册的路由重新解析当前目标
    return { path: to.path, query: to.query, hash: to.hash, replace: true }
  }

  // 合法路由均来自用户菜单：已登录仍落在兜底路由，视为越权
  if (to.name === 'NotFound') {
    return { path: '/403' }
  }

  const perm = to.meta.perm as string | undefined
  if (perm && !userStore.hasPerm(perm)) {
    return { path: '/403' }
  }
  return true
})

export default router
