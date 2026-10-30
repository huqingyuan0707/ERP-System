<script setup lang="ts">
/** 动态路由占位页：菜单已授权但页面功能尚未实现时展示。 */
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <el-empty :description="`「${String(route.meta.title ?? route.path)}」页面建设中，敬请期待`" />
</template>
