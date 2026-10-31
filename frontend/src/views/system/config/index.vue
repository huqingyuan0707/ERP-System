<script setup lang="ts">
/**
 * 系统参数页（S-03）。
 * 结构：参数表格（值就地编辑，仅提示修改过的行）+ 批量保存；保存后刷新全局参数缓存。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getConfigs, updateConfigs } from '@/api/config'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import type { SysConfig } from '@/types/config'

const configStore = useConfigStore()
const userStore = useUserStore()
const canEdit = computed(() => userStore.hasPerm('sys:config:edit'))

const loading = ref(false)
const saving = ref(false)
const rows = ref<SysConfig[]>([])
const drafts = reactive<Record<string, string>>({})

const dirtyKeys = computed<string[]>(() =>
  rows.value
    .filter((row) => (drafts[row.key] ?? row.value ?? '') !== (row.value ?? ''))
    .map((row) => row.key),
)

const rowClassName = ({ row }: { row: SysConfig }): string =>
  dirtyKeys.value.includes(row.key) ? 'row-dirty' : ''

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    rows.value = await getConfigs()
    for (const row of rows.value) {
      drafts[row.key] = row.value ?? ''
    }
  } finally {
    loading.value = false
  }
}

const resetDrafts = (): void => {
  for (const row of rows.value) {
    drafts[row.key] = row.value ?? ''
  }
}

const handleSave = async (): Promise<void> => {
  if (dirtyKeys.value.length === 0) {
    ElMessage.info('没有修改需要保存')
    return
  }
  saving.value = true
  try {
    rows.value = await updateConfigs({
      items: dirtyKeys.value.map((key) => ({ key, value: drafts[key] ?? null })),
    })
    for (const row of rows.value) {
      drafts[row.key] = row.value ?? ''
    }
    ElMessage.success('保存成功')
    await configStore.fetchAll(true)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void fetchList()
  void configStore.fetchAll()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">系统参数</h1>
        <p class="page-subtitle">维护公司信息、编号规则等系统级参数；修改立即生效（带缓存）。</p>
      </div>

      <el-alert
        class="tip"
        type="info"
        :closable="false"
        title="单号规则格式为「前缀,序号宽度」，如 PO,4 表示前缀 PO、4 位流水号。"
      />

      <div class="toolbar">
        <el-button
          v-perm="'sys:config:edit'"
          type="primary"
          :icon="'Check'"
          :loading="saving"
          :disabled="dirtyKeys.length === 0"
          @click="handleSave"
        >
          保存修改（{{ dirtyKeys.length }}）
        </el-button>
        <el-button :icon="'Refresh'" :disabled="dirtyKeys.length === 0" @click="resetDrafts">
          撤销修改
        </el-button>
      </div>

      <el-table v-loading="loading" :data="rows" border stripe :row-class-name="rowClassName">
        <el-table-column prop="key" label="参数键" width="240" show-overflow-tooltip />
        <el-table-column prop="name" label="参数名称" width="280" show-overflow-tooltip />
        <el-table-column label="参数值" min-width="400">
          <template #default="{ row }">
            <el-input v-model="drafts[row.key]" :disabled="!canEdit" maxlength="2000" />
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="说明" min-width="300" show-overflow-tooltip />
        <template #empty>
          <el-empty description="暂无系统参数" />
        </template>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.tip {
  margin-bottom: 16px;
}

:deep(.row-dirty) {
  background-color: #ecf5ff;
}
</style>
