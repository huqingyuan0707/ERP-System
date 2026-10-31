<script setup lang="ts">
/** 经营看板页（RPT-01/02 实现）。 */
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">经营看板</h1>
        <p class="page-subtitle">
          欢迎回来，{{ userStore.user?.realName || userStore.user?.username }}！今日经营概览如下。
        </p>
      </div>
      <el-alert
        title="看板功能将在 RPT-01/RPT-02 实现（今日销售额、库存总额、趋势图等）"
        type="info"
        :closable="false"
      />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  margin-bottom: 20px;
}
</style>
