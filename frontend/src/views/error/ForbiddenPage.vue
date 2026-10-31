<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="result-page">
    <el-result icon="warning" title="403" sub-title="没有权限访问该页面">
      <template #extra>
        <el-button type="primary" @click="router.push('/')">返回首页</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.result-page {
  display: flex;
  justify-content: center;
  padding: 60px 20px;
  box-sizing: border-box;
  min-height: 100vh;
}
</style>
