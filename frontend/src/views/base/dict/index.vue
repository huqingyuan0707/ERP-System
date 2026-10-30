<script setup lang="ts">
/**
 * 数据字典维护页（B-07）。
 * 结构：左侧字典类型列表 + 右侧所选类型的字典项列表；改动后同步刷新全局字典缓存。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import {
  createDictItem,
  createDictType,
  deleteDictItem,
  deleteDictType,
  getDictItems,
  getDictTypes,
  updateDictItem,
  updateDictType,
} from '@/api/dict'
import { useDictStore } from '@/stores/dict'
import type { DictItem, DictType } from '@/types/dict'

const dictStore = useDictStore()

const typesLoading = ref(false)
const types = ref<DictType[]>([])
const selectedType = ref<DictType | null>(null)

const itemsLoading = ref(false)
const items = ref<DictItem[]>([])

const typeName = computed(() => selectedType.value?.name ?? '')

// ---------- 字典类型弹窗 ----------
const typeDialogVisible = ref(false)
const typeDialogMode = ref<'create' | 'edit'>('create')
const typeSubmitting = ref(false)
const typeFormRef = ref<FormInstance>()
const editingTypeId = ref<number | null>(null)

interface TypeForm {
  code: string
  name: string
  status: number
  remark: string
}

const createEmptyTypeForm = (): TypeForm => ({ code: '', name: '', status: 1, remark: '' })
const typeForm = reactive<TypeForm>(createEmptyTypeForm())

const typeRules: FormRules = {
  code: [
    { required: true, message: '请输入类型编码', trigger: 'blur' },
    {
      pattern: /^[a-z][a-z0-9_]*$/,
      message: '小写字母开头，仅含小写字母、数字、下划线',
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: '请输入类型名称', trigger: 'blur' }],
}

// ---------- 字典项弹窗 ----------
const itemDialogVisible = ref(false)
const itemDialogMode = ref<'create' | 'edit'>('create')
const itemSubmitting = ref(false)
const itemFormRef = ref<FormInstance>()
const editingItemId = ref<number | null>(null)

interface ItemForm {
  label: string
  value: string
  sort: number
  status: number
  remark: string
}

const createEmptyItemForm = (): ItemForm => ({ label: '', value: '', sort: 0, status: 1, remark: '' })
const itemForm = reactive<ItemForm>(createEmptyItemForm())

const itemRules: FormRules = {
  label: [{ required: true, message: '请输入标签', trigger: 'blur' }],
  value: [{ required: true, message: '请输入键值', trigger: 'blur' }],
}

const fetchTypes = async (): Promise<void> => {
  typesLoading.value = true
  try {
    types.value = await getDictTypes()
  } finally {
    typesLoading.value = false
  }
}

const fetchItems = async (): Promise<void> => {
  if (!selectedType.value) {
    items.value = []
    return
  }
  itemsLoading.value = true
  try {
    items.value = await getDictItems(selectedType.value.code)
  } finally {
    itemsLoading.value = false
  }
}

const refreshDictCache = async (): Promise<void> => {
  await dictStore.fetchAll(true)
}

const handleSelectType = (row: DictType | null): void => {
  selectedType.value = row
  void fetchItems()
}

// ---------- 类型 CRUD ----------
const openCreateType = (): void => {
  typeDialogMode.value = 'create'
  editingTypeId.value = null
  Object.assign(typeForm, createEmptyTypeForm())
  typeDialogVisible.value = true
}

const openEditType = (row: DictType): void => {
  typeDialogMode.value = 'edit'
  editingTypeId.value = row.id
  Object.assign(typeForm, { code: row.code, name: row.name, status: row.status, remark: row.remark ?? '' })
  typeDialogVisible.value = true
}

const handleTypeSubmit = async (): Promise<void> => {
  const valid = await typeFormRef.value?.validate().catch(() => false)
  if (!valid) return
  typeSubmitting.value = true
  try {
    const payload = {
      name: typeForm.name,
      status: typeForm.status,
      remark: typeForm.remark || null,
    }
    if (typeDialogMode.value === 'create') {
      const created = await createDictType({ ...payload, code: typeForm.code })
      ElMessage.success('新增成功')
      await refreshDictCache()
      await fetchTypes()
      selectedType.value = created
      void fetchItems()
    } else if (editingTypeId.value !== null) {
      const updated = await updateDictType(editingTypeId.value, payload)
      ElMessage.success('修改成功')
      await refreshDictCache()
      await fetchTypes()
      if (selectedType.value?.id === updated.id) {
        selectedType.value = updated
      }
    }
    typeDialogVisible.value = false
  } finally {
    typeSubmitting.value = false
  }
}

const handleDeleteType = async (row: DictType): Promise<void> => {
  await ElMessageBox.confirm(`确定删除字典类型「${row.name}」吗？其下字典项将一并失效。`, '删除确认', {
    type: 'warning',
  })
  await deleteDictType(row.id)
  ElMessage.success('删除成功')
  if (selectedType.value?.id === row.id) {
    selectedType.value = null
  }
  await refreshDictCache()
  await fetchTypes()
  void fetchItems()
}

// ---------- 字典项 CRUD ----------
const openCreateItem = (): void => {
  itemDialogMode.value = 'create'
  editingItemId.value = null
  Object.assign(itemForm, createEmptyItemForm())
  itemDialogVisible.value = true
}

const openEditItem = (row: DictItem): void => {
  itemDialogMode.value = 'edit'
  editingItemId.value = row.id
  Object.assign(itemForm, {
    label: row.label,
    value: row.value,
    sort: row.sort,
    status: row.status,
    remark: row.remark ?? '',
  })
  itemDialogVisible.value = true
}

const handleItemSubmit = async (): Promise<void> => {
  const valid = await itemFormRef.value?.validate().catch(() => false)
  if (!valid) return
  if (!selectedType.value) {
    ElMessage.warning('请先选择左侧字典类型')
    return
  }
  itemSubmitting.value = true
  try {
    const payload = {
      label: itemForm.label,
      value: itemForm.value,
      sort: itemForm.sort,
      status: itemForm.status,
      remark: itemForm.remark || null,
    }
    if (itemDialogMode.value === 'create') {
      await createDictItem(selectedType.value.code, payload)
      ElMessage.success('新增成功')
    } else if (editingItemId.value !== null) {
      await updateDictItem(editingItemId.value, payload)
      ElMessage.success('修改成功')
    }
    itemDialogVisible.value = false
    await refreshDictCache()
    await fetchItems()
  } finally {
    itemSubmitting.value = false
  }
}

const handleDeleteItem = async (row: DictItem): Promise<void> => {
  await ElMessageBox.confirm(`确定删除字典项「${row.label}」吗？`, '删除确认', { type: 'warning' })
  await deleteDictItem(row.id)
  ElMessage.success('删除成功')
  await refreshDictCache()
  await fetchItems()
}

onMounted(() => {
  void fetchTypes()
})
</script>

<template>
  <div class="dict-page">
    <div class="page-card type-pane">
      <div class="pane-header">
        <span class="pane-title">字典类型</span>
        <el-button v-perm="'base:dict:add'" type="primary" size="small" :icon="'Plus'" @click="openCreateType">
          新增类型
        </el-button>
      </div>
      <el-table
        v-loading="typesLoading"
        :data="types"
        border
        stripe
        highlight-current-row
        height="100%"
        @current-change="handleSelectType"
      >
        <el-table-column prop="name" label="名称" min-width="100" show-overflow-tooltip />
        <el-table-column prop="code" label="编码" min-width="110" show-overflow-tooltip />
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button v-perm="'base:dict:edit'" link type="primary" @click.stop="openEditType(row)">
              编辑
            </el-button>
            <el-button v-perm="'base:dict:delete'" link type="danger" @click.stop="handleDeleteType(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无字典类型" />
        </template>
      </el-table>
    </div>

    <div class="page-card item-pane">
      <div class="pane-header">
        <span class="pane-title">字典项{{ typeName ? ` — ${typeName}` : '' }}</span>
        <el-button
          v-perm="'base:dict:add'"
          type="primary"
          size="small"
          :icon="'Plus'"
          :disabled="!selectedType"
          @click="openCreateItem"
        >
          新增字典项
        </el-button>
      </div>
      <el-table v-loading="itemsLoading" :data="items" border stripe height="100%">
        <el-table-column prop="label" label="标签" min-width="120" />
        <el-table-column prop="value" label="键值" min-width="100" />
        <el-table-column prop="sort" label="排序" width="70" align="center" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="110" fixed="right" align="center">
          <template #default="{ row }">
            <el-button v-perm="'base:dict:edit'" link type="primary" @click="openEditItem(row)">
              编辑
            </el-button>
            <el-button v-perm="'base:dict:delete'" link type="danger" @click="handleDeleteItem(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="selectedType ? '该类型暂无字典项' : '请先选择左侧字典类型'" />
        </template>
      </el-table>
    </div>

    <el-dialog
      v-model="typeDialogVisible"
      :title="typeDialogMode === 'create' ? '新增字典类型' : '编辑字典类型'"
      width="480px"
      destroy-on-close
    >
      <el-form ref="typeFormRef" :model="typeForm" :rules="typeRules" label-width="90px">
        <el-form-item label="类型编码" prop="code">
          <el-input
            v-model="typeForm.code"
            :disabled="typeDialogMode === 'edit'"
            placeholder="如 invoice_type"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="类型名称" prop="name">
          <el-input v-model="typeForm.name" placeholder="如 发票类型" maxlength="64" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="typeForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="typeForm.remark" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="typeDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="typeSubmitting" @click="handleTypeSubmit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="itemDialogVisible"
      :title="itemDialogMode === 'create' ? '新增字典项' : '编辑字典项'"
      width="480px"
      destroy-on-close
    >
      <el-form ref="itemFormRef" :model="itemForm" :rules="itemRules" label-width="90px">
        <el-form-item label="标签" prop="label">
          <el-input v-model="itemForm.label" placeholder="显示名称" maxlength="64" />
        </el-form-item>
        <el-form-item label="键值" prop="value">
          <el-input v-model="itemForm.value" placeholder="存储值" maxlength="64" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="itemForm.sort" :min="0" :precision="0" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="itemForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="itemForm.remark" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="itemDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="itemSubmitting" @click="handleItemSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.dict-page {
  display: flex;
  gap: 12px;
  height: 100%;
  min-height: 0;
}

.type-pane {
  width: 460px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.item-pane {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.pane-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.type-pane :deep(.el-table),
.item-pane :deep(.el-table) {
  flex: 1;
  min-height: 0;
}
</style>
