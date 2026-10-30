<script setup lang="ts">
/** 经营看板占位页（RPT-01/02 实现）。 */
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
</script>

<template>
  <div class="page-card">
    <h3>经营看板</h3>
    <p>欢迎回来，{{ userStore.user?.realName || userStore.user?.username }}！</p>
    <el-alert
      title="看板功能将在 RPT-01/RPT-02 实现（今日销售额、库存总额、趋势图等）"
      type="info"
      :closable="false"
    />
  </div>
</template>
