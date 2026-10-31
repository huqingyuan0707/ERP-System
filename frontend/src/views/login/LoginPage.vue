<script setup lang="ts">
/** 登录页：账号密码登录，成功后按 redirect 跳转。 */
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: 'admin', password: '' })

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const handleLogin = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await userStore.login({ username: form.username, password: form.password })
    await userStore.fetchUser()
    ElMessage.success('登录成功')
    // 默认进 '/'，由路由守卫按用户首个可导航菜单落地
    const redirect = (route.query.redirect as string) || '/'
    router.push(redirect)
  } catch {
    // 错误提示已由 request 拦截器统一处理
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-wrapper">
      <el-card class="login-card">
        <div class="login-brand">企业ERP系统</div>
        <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="handleLogin">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" :prefix-icon="'User'" />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="密码"
              show-password
              :prefix-icon="'Lock'"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" class="login-btn" :loading="loading" @click="handleLogin">
              登 录
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e3a5f 0%, #0f2438 100%);
  padding: 40px;
  box-sizing: border-box;
}
.login-wrapper {
  width: 100%;
  max-width: 460px;
  display: flex;
  justify-content: center;
}
.login-card {
  width: 420px;
  padding: 32px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}
.login-brand {
  text-align: center;
  font-size: 26px;
  font-weight: 700;
  color: #fff;
  margin: 0 0 8px 0;
}
.login-brand::after {
  content: '· ERP';
  font-size: 14px;
  font-weight: 400;
  opacity: 0.7;
  margin-left: 4px;
}
.login-btn {
  width: 100%;
  font-size: 16px;
  padding: 10px 0;
}
</style>
