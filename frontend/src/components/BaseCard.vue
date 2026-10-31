<script setup lang="ts">
/**
 * 通用卡片组件 BaseCard。
 *
 * 用途：把列表页的「查询条件」和「查询结果」统一成同一种卡片外观。
 * 组件只负责标题区、操作区、内容区、底部的展示结构，不承载任何数据查询或业务逻辑，
 * 具体表单、表格、分页由使用方通过插槽传入。
 *
 * 用法示例：
 *   <BaseCard title="查询条件" subtitle="维护商品基础档案……">查询表单</BaseCard>
 *   <BaseCard title="查询结果" body-padding="0" loading="loading">
 *     <template #extra>新增 / 导出 / 导入按钮</template>
 *     表格
 *     <template #footer>分页</template>
 *   </BaseCard>
 */
import { computed } from 'vue'

interface BaseCardProps {
  /** 左上角标题，如「查询条件」「查询结果」；不传则使用 title 插槽 */
  title?: string
  /** 标题下方的灰色说明文字，可选 */
  subtitle?: string
  /** 内容区加载遮罩，页面无需再单独给表格加 v-loading */
  loading?: boolean
  /** 内容区内边距；表格类内容传 '0' 可让表格贴住卡片边缘 */
  bodyPadding?: string
  /** 标题区与内容区之间是否显示分割线 */
  showDivider?: boolean
  /** 透传 el-card 的阴影模式，默认无阴影，与页面整体的扁平风格一致 */
  shadow?: 'always' | 'never' | 'hover'
}

const props = withDefaults(defineProps<BaseCardProps>(), {
  title: '',
  subtitle: '',
  loading: false,
  bodyPadding: '20px',
  showDivider: false,
  shadow: 'never',
})

// el-card 的 body-style 只接受样式对象，这里把内边距作为样式透传下去
const bodyStyle = computed(() => ({ padding: props.bodyPadding }))
</script>

<template>
  <el-card
    v-loading="loading"
    class="base-card"
    :class="{ 'base-card--divider': showDivider }"
    :shadow="shadow"
    :body-style="bodyStyle"
  >
    <!-- 只有传了标题、副标题或标题/操作插槽时才渲染头部，避免出现空白标题栏 -->
    <template v-if="title || subtitle || $slots.title || $slots.extra" #header>
      <div class="base-card__header">
        <div v-if="title || $slots.title" class="base-card__heading">
          <slot name="title">
            <span class="base-card__title">{{ title }}</span>
          </slot>
          <p v-if="subtitle" class="base-card__subtitle">{{ subtitle }}</p>
        </div>

        <!-- 右上角操作区：工具栏按钮、刷新、折叠等由使用方自行放入 -->
        <div v-if="$slots.extra" class="base-card__extra">
          <slot name="extra" />
        </div>
      </div>
    </template>

    <!-- 内容区：查询表单或结果表格 -->
    <slot />

    <!-- 底部区：分页、合计统计等，由使用方按需传入 -->
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
  </el-card>
</template>

<style scoped>
.base-card {
  border: 1px solid #ebe9f1;
  border-radius: 8px;
}

/* 默认隐藏 el-card 头部的下边框，保持与页面原有 .page-card 一致的扁平观感 */
.base-card :deep(.el-card__header) {
  padding: 20px 20px 0;
  border-bottom: none;
}

/* 需要视觉分隔时才补分割线与下方留白 */
.base-card.base-card--divider :deep(.el-card__header) {
  padding-bottom: 16px;
  border-bottom: 1px solid #ebe9f1;
}

.base-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.base-card__heading {
  /* 允许标题过长时换行，避免把右侧操作区挤出卡片 */
  min-width: 0;
}

.base-card__title {
  font-size: 16px;
  font-weight: 600;
  line-height: 22px;
  color: #303133;
}

.base-card__subtitle {
  margin: 4px 0 0;
  font-size: 14px;
  line-height: 20px;
  color: #909399;
}

.base-card__extra {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: flex-end;
}
</style>
