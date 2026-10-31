<script setup lang="ts">
/** 商品导入弹窗（C-07）：下载模板、上传 Excel、逐行校验，全部通过才入库，失败展示错误行报告。 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { downloadGoodsTemplate, importGoods } from '@/api/goods'
import type { GoodsImportError } from '@/types/goods'

interface Props {
  modelValue: boolean
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'success'): void
}>()

/** 双向绑定代理：props 只读，关闭/打开时通过 emit 回写父组件的 v-model。 */
const modelValueProxy = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const importing = ref(false)
const fileRef = ref<File | null>(null)
const errorRows = ref<GoodsImportError[]>([])
const failCount = ref(0)

/** 每次打开弹窗重置上一次的选择与错误报告，避免残留结果误导用户。 */
watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      resetState()
    }
  },
)

const resetState = (): void => {
  fileRef.value = null
  errorRows.value = []
  failCount.value = 0
}

const handleFileChange = (uploadFile: { raw?: File }): void => {
  fileRef.value = uploadFile.raw ?? null
  errorRows.value = []
  failCount.value = 0
}

const downloadTemplate = async (): Promise<void> => {
  try {
    await downloadGoodsTemplate()
  } catch {
    // 错误已由 request 拦截器统一提示
  }
}

const handleImport = async (): Promise<void> => {
  if (!fileRef.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  importing.value = true
  try {
    const result = await importGoods(fileRef.value)
    if (result.success) {
      ElMessage.success(`导入成功，${result.successCount} 行已入库`)
      emit('update:modelValue', false)
      // 通知父页面刷新列表与分类计数
      emit('success')
      return
    }
    // 全有或全无：任一行出错整批不入库，弹窗内保留错误报告供逐行修正
    failCount.value = result.failCount
    errorRows.value = result.errors
    ElMessage.error(`导入失败：${result.failCount} 行有误，整批未入库`)
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <el-dialog v-model="modelValueProxy" title="商品导入" width="640px" destroy-on-close>
    <div class="template-row">
      <el-button text :icon="'Download'" @click="downloadTemplate">下载导入模板</el-button>
      <span class="template-tip">
        模板列：商品编码/商品名称/规格/分类名称/单位名称/条码/成本价/售价/库存下限/库存上限/备注；
        分类与单位需先在系统中维护；全部校验通过才入库，任一行出错则整批不入库。
      </span>
    </div>
    <el-upload
      v-if="!fileRef"
      class="upload-demo"
      action=""
      :show-file-list="false"
      :on-change="handleFileChange"
      accept=".xlsx,.xls"
    >
      <el-button type="dashed" :icon="'Upload'">点击上传 Excel 文件</el-button>
    </el-upload>
    <!-- 已选文件后把「导入/重选」按钮移出 el-upload，避免再次点击时又弹出文件选择框 -->
    <div v-else class="file-actions">
      <span class="file-name">{{ fileRef.name }}</span>
      <el-button type="primary" :loading="importing" @click="handleImport">
        {{ importing ? '导入中...' : '立即导入' }}
      </el-button>
      <el-button @click="resetState">重新选择</el-button>
    </div>

    <!-- 错误报告：按 Excel 行号定位，便于用户回到文件逐行修正 -->
    <div v-if="errorRows.length > 0" class="error-report">
      <div class="error-report__title">错误报告（共 {{ failCount }} 行）</div>
      <el-table :data="errorRows" border size="small" max-height="260">
        <el-table-column prop="row" label="Excel 行号" width="100" />
        <el-table-column label="错误原因">
          <template #default="{ row }">{{ row.errors.join('；') }}</template>
        </el-table-column>
      </el-table>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.template-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 16px;
}
.template-tip {
  font-size: 12px;
  line-height: 18px;
  color: #909399;
}
.upload-demo {
  width: 100%;
}
.file-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #303133;
}
.error-report {
  margin-top: 16px;
}
.error-report__title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #f56c6c;
}
</style>
