<script setup lang="ts">
/** 异步任务进度抽屉（C-07）：顶栏入口，显示所有导入导出任务进度与下载入口。 */
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElButton, ElMessage, ElProgress, ElTag, ElScrollbar } from 'element-plus'

import { downloadTaskFile, getTaskList, isTerminalTaskError } from '@/api/task'
import type { Task } from '@/types/log'

const visible = ref(false)
const loading = ref(false)
const tasks = ref<Task[]>([])

const pendingCount = computed(() => tasks.value.filter((t) => t.status !== 3 && t.status !== 4).length)

let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let disposed = false
let failures = 0
let attempts = 0
let stopped = false

const clearTimer = (): void => {
  if (timer) clearTimeout(timer)
  timer = null
}

const fetchTasks = async (): Promise<void> => {
  if (loading.value || disposed || stopped) return
  clearTimer()
  loading.value = true
  controller = new AbortController()
  attempts += 1
  try {
    const result = await getTaskList({ page: 1, pageSize: 20 }, controller.signal)
    if (disposed) return
    tasks.value = result.items
    failures = 0
  } catch (error) {
    if (disposed) return
    failures += 1
    if (isTerminalTaskError(error) || failures >= 3) stopped = true
  } finally {
    if (!disposed) {
      loading.value = false
      if (visible.value && !stopped && attempts < 150 && (pendingCount.value > 0 || failures > 0)) {
        timer = setTimeout(() => void fetchTasks(), 3000)
      }
    }
  }
}

watch(visible, (opened) => {
  clearTimer()
  if (opened && !stopped) {
    attempts = 0
    void fetchTasks()
  }
})

onBeforeUnmount(() => {
  disposed = true
  clearTimer()
  controller?.abort()
})

const statusLabel = (v: number): string => {
  const map: Record<number, string> = { 1: '待执行', 2: '执行中', 3: '成功', 4: '失败' }
  return map[v] ?? '未知'
}

const statusType = (v: number): 'primary' | 'success' | 'warning' | 'info' | 'danger' => {
  const map: Record<number, 'primary' | 'success' | 'warning' | 'info' | 'danger'> = {
    1: 'info',
    2: 'warning',
    3: 'success',
    4: 'danger',
  }
  return map[v] ?? 'info'
}

const taskTypeLabel = (t: string): string => {
  const map: Record<string, string> = {
    goods_export: '商品导出',
    stock_export: '库存导出',
    report_export: '报表导出',
    statement_export: '对账单导出',
  }
  return map[t] ?? t
}

const downloadFile = (row: Task): void => {
  if (!row.canDownload) return
  void downloadTaskFile(row).catch(() => {
    ElMessage.error('下载失败')
  })
}

onMounted(() => {
  void fetchTasks()
})

const handleRefresh = (): void => {
  stopped = false
  failures = 0
  attempts = 0
  void fetchTasks()
}

</script>

<template>
  <el-dropdown>
    <span class="task-trigger" @click="visible = true">
      <el-icon><Clock /></el-icon>
      <el-badge :value="pendingCount" :hidden="pendingCount === 0" />
    </span>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item divided @click="visible = true">查看任务进度</el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>

  <el-drawer v-model="visible" title="异步任务进度" width="520px" destroy-on-close>
    <el-divider>最近任务</el-divider>
    <el-scrollbar style="height: 340px">
      <el-table :data="tasks" border stripe size="small">
        <el-table-column prop="taskType" label="任务类型" width="140">
          <template #default="{ row }">{{ taskTypeLabel(row.taskType) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="160" align="center">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :show-text="true" :width="120" />
          </template>
        </el-table-column>
        <el-table-column prop="createdAt" label="创建时间" width="160" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 3 && row.canDownload"
              type="primary"
              size="small"
              :icon="'Download'"
              @click="downloadFile(row)"
            >
              下载
            </el-button>
            <el-button
              v-else-if="row.status === 4"
              type="danger"
              size="small"
              :icon="'Close'"
            >
              失败
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无任务" />
        </template>
      </el-table>
    </el-scrollbar>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button type="primary" :loading="loading" @click="handleRefresh">刷新</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.task-trigger {
  cursor: pointer;
  padding: 6px;
  border-radius: 4px;
  transition: background 0.2s;
}
.task-trigger:hover {
  background: #ecf5ff;
}
</style>
