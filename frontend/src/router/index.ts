/** 路由：登录页 + 布局内业务页；守卫校验登录态。 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useUserStore } from '@/stores/user'
import { getToken } from '@/utils/request'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/LoginPage.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/DashboardPage.vue'),
        meta: { title: '经营看板' },
      },
      {
        path: 'base/goods',
        name: 'GoodsList',
        component: () => import('@/views/base/goods/index.vue'),
        meta: { title: '商品管理', perm: 'base:goods:list' },
      },
    ],
  },
  {
    path: '/403',
    name: 'Forbidden',
    component: () => import('@/views/error/ForbiddenPage.vue'),
    meta: { public: true, title: '无权限' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/error/NotFoundPage.vue'),
    meta: { public: true, title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  document.title = to.meta.title ? `${to.meta.title} · 企业ERP系统` : '企业ERP系统'
  if (to.meta.public) return true

  if (!getToken()) {
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

  const perm = to.meta.perm as string | undefined
  if (perm && !userStore.hasPerm(perm)) {
    return { path: '/403' }
  }
  return true
})

export default router
