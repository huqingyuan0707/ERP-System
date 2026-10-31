<script setup lang="ts">
/** 导出按钮（C-07）：提交异步导出任务 → 轮询进度 → 成功后下载文件。 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  exportGoods,
  exportStocks,
  exportReport,
  exportStatement,
  getTaskDetail,
  downloadExportFile,
} from '@/api/task'

interface Props {
  /** 导出类型：'goods' | 'stocks' | 'report' | 'statement'（默认 goods，故为可选） */
  type?: 'goods' | 'stocks' | 'report' | 'statement'
  /** 导出参数（提交任务时传给后端） */
  params?: Record<string, unknown>
  /** 报表类型（type='report' 时必填） */
  reportType?: string
  /** 对账单ID（type='statement' 时必填） */
  statementId?: number
  /** 按钮标签 */
  label?: string
  /** 是否禁用 */
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  type: 'goods',
  // 对象类型默认值必须用工厂函数，避免多个组件实例共享同一引用
  params: () => ({}),
  reportType: '',
  statementId: 0,
  label: '导出 Excel',
  disabled: false,
})

const loading = ref(false)
const polling = ref(false)
const progress = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

const clearTimer = (): void => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

const submitExport = async (): Promise<void> => {
  if (props.disabled || loading.value || polling.value) return
  let taskId = ''
  try {
    loading.value = true
    progress.value = 0
    switch (props.type) {
      case 'goods':
        taskId = (await exportGoods(props.params as Record<string, unknown>)).taskId
        break
      case 'stocks':
        taskId = (await exportStocks(props.params as Record<string, unknown>)).taskId
        break
      case 'report':
        taskId = (await exportReport(props.reportType ?? '')).taskId
        break
      case 'statement':
        taskId = (await exportStatement(props.statementId ?? 0)).taskId
        break
    }
    if (!taskId) throw new Error('未获取到任务 ID')
    polling.value = true
    clearTimer()
    timer = setInterval(() => {
      void pollTask(taskId)
    }, 2000)
    void pollTask(taskId)
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

const pollTask = async (taskId: string): Promise<void> => {
  try {
    const task = await getTaskDetail(taskId)
    progress.value = task.progress
    if (task.status === 3) {
      // 成功：下载文件
      clearTimer()
      polling.value = false
      progress.value = 100
      const fileName = `${props.type}_export_${taskId}.xlsx`
      await downloadExportFile(fileName)
      ElMessage.success('导出完成，文件已下载')
    } else if (task.status === 4) {
      // 失败
      clearTimer()
      polling.value = false
      ElMessage.error(`导出失败：${task.errorMsg ?? '未知错误'}`)
    }
    // status 1/2 继续轮询
  } catch {
    // 轮询失败不中断，继续重试
  }
}

const handleClick = (): void => {
  void submitExport()
}

</script>

<template>
  <el-button
    type="primary"
    :icon="'Download'"
    :loading="loading || polling"
    :disabled="props.disabled"
    @click="handleClick"
  >
    <template v-if="polling">{{ progress }}%...</template>
    <template v-else>
      <!-- 默认插槽用于自定义按钮文案，未传时回退到 label 属性 -->
      <slot>{{ props.label }}</slot>
    </template>
  </el-button>
</template>
