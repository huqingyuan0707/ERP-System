<script setup lang="ts">
/**
 * 仓库管理页（BASE-07）。
 * 结构：查询条件 + 仓库表格/分页 + 新增/编辑弹窗。
 * 与后端 B-03 保持一致：
 *   - 编码重复 409；负责人不存在 422；
 *   - 设为默认时，后端同一事务内把其它仓 is_default 置 false；
 *   - 停用/删除前先查 inv_stock，有库存返回 409；默认仓禁止停用/删除 409；
 *   - 删除为软删。
 */
import { nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import {
  createWarehouse,
  deleteWarehouse,
  fetchManagerOptions,
  getWarehouseList,
  updateWarehouse,
} from '@/api/warehouse'
import type { ManagerOption, Warehouse, WarehouseQuery } from '@/types/warehouse'
import BaseCard from '@/components/BaseCard.vue'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()

const loading = ref(false)
const rows = ref<Warehouse[]>([])
const total = ref(0)
const managers = ref<ManagerOption[]>([])
const managerLoading = ref(false)

const query = reactive<WarehouseQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
})

// ---------- 仓库列表 ----------

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getWarehouseList(query)
    rows.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

const fetchManagers = async (keyword = ''): Promise<void> => {
  managerLoading.value = true
  try {
    managers.value = await fetchManagerOptions(keyword)
  } finally {
    managerLoading.value = false
  }
}

const handleSearch = (): void => {
  query.page = 1
  void fetchList()
}

const handleReset = (): void => {
  query.keyword = ''
  query.status = null
  handleSearch()
}

const handlePageChange = (page: number): void => {
  query.page = page
  void fetchList()
}

const handleSizeChange = (size: number): void => {
  query.pageSize = size
  query.page = 1
  void fetchList()
}

// ---------- 仓库表单 ----------

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)

interface WarehouseForm {
  code: string
  name: string
  managerId: number | null
  isDefault: boolean
  address: string
  status: number
  remark: string
}

const createEmptyForm = (): WarehouseForm => ({
  code: '',
  name: '',
  managerId: null,
  isDefault: false,
  address: '',
  status: 1,
  remark: '',
})

const form = reactive<WarehouseForm>(createEmptyForm())

const rules: FormRules = {
  name: [{ required: true, message: '请输入仓库名称', trigger: 'blur' }],
}

const openCreate = (): void => {
  dialogMode.value = 'create'
  editingId.value = null
  Object.assign(form, createEmptyForm())
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

const openEdit = (row: Warehouse): void => {
  if (row.managerId && !managers.value.some((m) => m.value === row.managerId)) {
    managers.value.push({ value: row.managerId, label: row.managerName || '原负责人（暂用用户）' })
  }
  dialogMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, {
    code: row.code,
    name: row.name,
    managerId: row.managerId,
    isDefault: row.isDefault,
    address: row.address ?? '',
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const payload = {
      code: form.code || null,
      name: form.name.trim(),
      managerId: form.managerId,
      isDefault: form.isDefault,
      address: form.address.trim() || null,
      status: form.status,
      remark: form.remark.trim() || null,
    }
    if (dialogMode.value === 'create') {
      await createWarehouse(payload)
      ElMessage.success('新增成功')
    } else if (editingId.value !== null) {
      await updateWarehouse(editingId.value, { ...payload, code: form.code })
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row: Warehouse): Promise<void> => {
  await ElMessageBox.confirm(`确定删除仓库「${row.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteWarehouse(row.id)
  ElMessage.success('删除成功')
  await fetchList()
}

/** 启用/停用切换：默认仓禁止停用，后端返回 409 时回滚开关。 */
const handleStatusChange = async (row: Warehouse, value: unknown): Promise<void> => {
  const status = Number(value)
  try {
    await updateWarehouse(row.id, { status })
    row.status = status
    ElMessage.success(status === 1 ? '已启用' : '已停用')
  } catch {
    void fetchList()
  }
}

onMounted(() => {
  void fetchList()
  void fetchManagers()
})
</script>

<template>
  <div class="page-container warehouse-page">
    <el-row :gutter="16">
      <!-- 查询条件与结果 -->
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="维护仓库基础档案，支持编码/名称关键字与状态筛选。"
        >
          <el-form class="query-form" label-width="72px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="编码 / 名称"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable>
                    <el-option label="启用" :value="1" />
                    <el-option label="停用" :value="0" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>

            <div class="query-actions">
              <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
              <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
              <el-button
                v-perm="'base:warehouse:add'"
                type="primary"
                :icon="'Plus'"
                @click="openCreate"
              >
                新增仓库
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="查询结果"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="code" label="编码" width="120" />
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column prop="managerName" label="负责人（用户）" width="150">
              <template #default="{ row }">{{ row.managerName || '-' }}</template>
            </el-table-column>
            <el-table-column prop="address" label="地址" min-width="160" />
            <el-table-column label="是否默认" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="row.isDefault ? 'danger' : 'primary'" size="small">
                  {{ row.isDefault ? '默认仓' : '普通仓' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-switch
                  v-if="userStore.hasPerm('base:warehouse:edit')"
                  :model-value="row.status"
                 :active-value="1"
                 :inactive-value="0"
                  @change="handleStatusChange(row, $event)"
                />
                <el-tag v-else :type="row.status === 1 ? 'success' : 'info'" size="small">
                  {{ row.status === 1 ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  v-perm="'base:warehouse:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-perm="'base:warehouse:delete'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无仓库数据" />
            </template>
          </el-table>

          <template #footer>
            <div class="card-footer">
              <el-pagination
                :current-page="query.page"
                :page-size="query.pageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="total"
                layout="total, sizes, prev, pager, next, jumper"
                @current-change="handlePageChange"
                @size-change="handleSizeChange"
              />
            </div>
          </template>
        </BaseCard>
      </el-col>
    </el-row>

    <!-- 仓库新增/编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新增仓库' : '编辑仓库'"
      width="620px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-row :gutter="20">
          <el-col :span="24">
            <el-form-item label="仓库编码">
              <el-input
                v-model="form.code"
                :disabled="dialogMode === 'edit'"
                placeholder="留空由后端自动生成"
              />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="仓库名称" prop="name">
              <el-input v-model="form.name" placeholder="必填" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="负责人">
              <el-select
                v-model="form.managerId"
                :loading="managerLoading"
                placeholder="搜索用户姓名/账号（可选）"
                filterable
                remote
                :remote-method="fetchManagers"
                clearable
                class="full"
              >
                <el-option
                  v-for="m in managers"
                  :key="m.value"
                  :label="m.label"
                  :value="m.value"
                />
              </el-select>
              <div class="form-tip">负责人暂用系统用户；员工档案闭环待 B06 接入。</div>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="是否默认仓">
              <el-switch
                v-model="form.isDefault"
                :active-value="true"
                :inactive-value="false"
                active-text="是"
                inactive-text="否"
              />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="状态">
              <el-radio-group v-model="form.status">
                <el-radio :value="1">启用</el-radio>
                <el-radio :value="0">停用</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="地址">
              <el-input v-model="form.address" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remark" type="textarea" :rows="3" maxlength="255" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.form-tip {
  font-size: 12px;
  color: #909399;
}

.query-form {
  margin-bottom: 0;
}

.query-form :deep(.el-input),
.query-form :deep(.el-select) {
  width: 100%;
}

.query-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.result-card {
  margin-top: 16px;
}

.card-footer {
  display: flex;
  justify-content: flex-end;
}

.full {
  width: 100%;
}
</style>
