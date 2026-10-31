<script setup lang="ts">
/** 商品导入弹窗（C-07）：下载模板、上传 Excel、逐行校验、全部成功才入库。 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { importGoods } from '@/api/task'

interface Props {
  modelValue: boolean
  onSuccess?: () => void
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
  onSuccess: undefined,
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

const handleFileChange = (e: Event): void => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  fileRef.value = file
}

const downloadTemplate = async (): Promise<void> => {
  const link = document.createElement('a')
  link.href = '/base/goods/template'
  link.download = '商品导入模板.xlsx'
  link.click()
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
      fileRef.value = null
      const elInput = document.querySelector('input[type=file]') as HTMLInputElement | null
      if (elInput) elInput.value = ''
      emit('update:modelValue', false)
      await props.onSuccess?.()
    } else {
      const msg = result.errors
        .map((e) => `第${e.row}行：${e.errors.join('、')}`)
        .join('\n')
      ElMessage.error(`导入失败：${result.failCount} 行有误\n${msg}`)
    }
  } catch {
    // 错误已由 request 拦截器统一提示
  } finally {
    importing.value = false
  }
}

</script>

<template>
  <el-dialog v-model="modelValueProxy" title="商品导入" width="600px" destroy-on-close>
    <el-card>
      <div class="template-row">
        <el-button text :icon="'Download'" @click="downloadTemplate">下载导入模板</el-button>
        <span class="template-tip">
          模板说明：商品编码/商品名称/规格/分类名称/单位名称/条码/成本价/售价/库存下限/库存上限/备注；
          分类与单位不存在时自动创建；全部校验通过才入库，任一行出错则全部不入库。
        </span>
      </div>
      <el-upload
        class="upload-demo"
        action=""
        :show-file-list="false"
        :on-change="handleFileChange"
        accept=".xlsx,.xls"
      >
        <el-button v-if="!fileRef" type="dashed" :icon="'Upload'">
          点击上传 Excel 文件
        </el-button>
        <el-button v-else type="primary" :loading="importing" @click="handleImport">
          {{ importing ? '导入中...' : '立即导入' }}
        </el-button>
      </el-upload>
    </el-card>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.template-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.template-tip {
  font-size: 12px;
  color: #909399;
}
.upload-demo {
  width: 100%;
}
</style>
