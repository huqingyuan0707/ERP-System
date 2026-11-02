<script setup lang="ts">
/** 导出按钮（C-07）：提交异步导出任务 → 轮询进度 → 成功后下载文件。 */
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  exportGoods,
  exportStocks,
  exportReport,
  exportStatement,
  getTaskDetail,
  downloadTaskFile,
  isTerminalTaskError,
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
let timer: ReturnType<typeof setTimeout> | null = null
let controller: AbortController | null = null
let disposed = false
let attempts = 0
let failures = 0
const MAX_POLLS = 150
const MAX_FAILURES = 3

const stopPolling = (): void => {
  if (timer) clearTimeout(timer)
  timer = null
  polling.value = false
}

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
  controller?.abort()
})

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
    if (disposed) return
    if (!taskId) throw new Error('未获取到任务 ID')
    attempts = 0
    failures = 0
    polling.value = true
    void pollTask(taskId)
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

const pollTask = async (taskId: string): Promise<void> => {
  if (disposed || !polling.value) return
  attempts += 1
  controller = new AbortController()
  try {
    const task = await getTaskDetail(taskId, controller.signal)
    if (disposed) return
    failures = 0
    progress.value = task.progress
    if (task.status === 3) {
      stopPolling()
      progress.value = 100
      if (!task.canDownload) {
        ElMessage.error('当前任务没有可下载文件或无下载权限')
        return
      }
      await downloadTaskFile(task)
      if (!disposed) ElMessage.success('导出完成，文件已下载')
      return
    }
    if (task.status === 4) {
      stopPolling()
      ElMessage.error(`导出失败：${task.errorMsg ?? '未知错误'}`)
      return
    }
  } catch (error) {
    if (disposed) return
    failures += 1
    if (!polling.value) return // 下载失败不重新下载或继续轮询
    if (isTerminalTaskError(error) || failures >= MAX_FAILURES) {
      stopPolling()
      ElMessage.error('无法继续查询任务，请检查登录或稍后在任务列表查看')
      return
    }
  }
  if (disposed || !polling.value) return
  if (attempts >= MAX_POLLS) {
    stopPolling()
    ElMessage.warning('已停止等待，请稍后在任务列表查看结果')
    return
  }
  // 上一次请求结束后才安排下一次，防止慢请求重叠和重复下载。
  timer = setTimeout(() => void pollTask(taskId), 2000 * Math.max(1, failures))
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
