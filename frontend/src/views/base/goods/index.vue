<script setup lang="ts">
/**
 * 商品管理页（BASE-04/05/06）。
 * 结构：查询条件卡片 + 查询结果卡片（工具栏 + 表格 + 分页）+ 新增/编辑弹窗。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import {
  createGoods,
  deleteGoods,
  getCategories,
  getGoodsList,
  getUnits,
  updateGoods,
} from '@/api/goods'
import type { Goods, GoodsCategory, GoodsQuery, Unit } from '@/types/goods'
import BaseCard from '@/components/BaseCard.vue'
import ExportButton from '@/components/ExportButton.vue'
import ImportDialog from '@/components/ImportDialog.vue'

const loading = ref(false)
const rows = ref<Goods[]>([])
const total = ref(0)
const categories = ref<GoodsCategory[]>([])
const units = ref<Unit[]>([])

const query = reactive<GoodsQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  categoryId: null,
  status: null,
})

const dialogVisible = ref(false)
const importDialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)

const createEmptyForm = (): GoodsForm => ({
  code: '',
  name: '',
  categoryId: null,
  spec: '',
  unitId: null,
  barcode: '',
  costPrice: 0,
  salePrice: 0,
  stockLower: 0,
  stockUpper: 0,
  status: 1,
  remark: '',
})

interface GoodsForm {
  code: string
  name: string
  categoryId: number | null
  spec: string
  unitId: number | null
  barcode: string
  costPrice: number
  salePrice: number
  stockLower: number
  stockUpper: number
  status: number
  remark: string
}

const form = reactive<GoodsForm>(createEmptyForm())

const rules: FormRules = {
  name: [{ required: true, message: '请输入商品名称', trigger: 'blur' }],
  salePrice: [{ required: true, message: '请输入销售价', trigger: 'blur' }],
}

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getGoodsList(query)
    rows.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

const fetchOptions = async (): Promise<void> => {
  const [cats, us] = await Promise.all([getCategories(), getUnits()])
  categories.value = cats
  units.value = us
}

const handleSearch = (): void => {
  query.page = 1
  void fetchList()
}

const handleReset = (): void => {
  query.keyword = ''
  query.categoryId = null
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

const openCreate = (): void => {
  dialogMode.value = 'create'
  editingId.value = null
  Object.assign(form, createEmptyForm())
  dialogVisible.value = true
}

const openEdit = (row: Goods): void => {
  dialogMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, {
    code: row.code,
    name: row.name,
    categoryId: row.categoryId,
    spec: row.spec ?? '',
    unitId: row.unitId,
    barcode: row.barcode ?? '',
    costPrice: row.costPrice,
    salePrice: row.salePrice,
    stockLower: row.stockLower,
    stockUpper: row.stockUpper,
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (form.stockUpper > 0 && form.stockLower > form.stockUpper) {
    ElMessage.error('库存下限不能大于上限')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name,
      categoryId: form.categoryId,
      spec: form.spec || null,
      unitId: form.unitId,
      barcode: form.barcode || null,
      costPrice: form.costPrice,
      salePrice: form.salePrice,
      stockLower: form.stockLower,
      stockUpper: form.stockUpper,
      status: form.status,
      remark: form.remark || null,
    }
    if (dialogMode.value === 'create') {
      await createGoods({ ...payload, code: form.code || null })
      ElMessage.success('新增成功')
    } else if (editingId.value !== null) {
      await updateGoods(editingId.value, payload)
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    void fetchList()
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row: Goods): Promise<void> => {
  await ElMessageBox.confirm(`确定删除商品「${row.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteGoods(row.id)
  ElMessage.success('删除成功')
  void fetchList()
}

const openImport = (): void => {
  importDialogVisible.value = true
}

onMounted(() => {
  void fetchList()
  void fetchOptions()
})
</script>

<template>
  <div class="page-container">
    <!-- 卡片 1：查询条件。标题与副标题全部由 BaseCard 渲染，页面只提供表单内容 -->
    <BaseCard
      title="查询条件"
      subtitle="维护商品基础档案，支持分类、单位、价格与库存范围设置。"
    >
      <el-form class="query-form" label-width="72px" @submit.prevent="handleSearch">
        <!-- 筛选项每行最多 4 个（lg 断点下每列 span=6），窄屏自动降为 3/2/1 列 -->
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12" :md="8" :lg="6">
            <el-form-item label="关键字">
              <el-input
                v-model="query.keyword"
                placeholder="编码 / 名称 / 条码"
                clearable
                @keyup.enter="handleSearch"
              />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12" :md="8" :lg="6">
            <el-form-item label="分类">
              <el-select v-model="query.categoryId" placeholder="全部" clearable>
                <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
              </el-select>
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

        <!-- 查询、重置与结果工具栏按钮统一收在查询卡片的最后一行 -->
        <div class="query-actions">
          <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
          <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
          <el-button
            v-perm="'base:goods:add'"
            type="primary"
            :icon="'Plus'"
            @click="openCreate"
          >
            新增商品
          </el-button>
          <ExportButton
            v-perm="'base:goods:export'"
            :params="{
              keyword: query.keyword || undefined,
              categoryId: query.categoryId,
              status: query.status,
            }"
          >
            导出 Excel
          </ExportButton>
          <el-button v-perm="'base:goods:import'" :icon="'Upload'" @click="openImport">
            导入 Excel
          </el-button>
        </div>
      </el-form>
    </BaseCard>

    <!-- 卡片 2：查询结果。工具栏已移到查询卡片末行，这里只保留标题、表格与分页 -->
    <BaseCard
      class="result-card"
      title="查询结果"
      body-padding="0"
      show-divider
      :loading="loading"
    >
      <el-table :data="rows" border stripe>
        <el-table-column prop="code" label="编码" width="130" />
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column prop="spec" label="规格" min-width="160" />
        <el-table-column prop="categoryName" label="分类" width="140" />
        <el-table-column prop="unitName" label="单位" width="100" />
        <el-table-column prop="costPrice" label="成本价" width="130" align="right" />
        <el-table-column prop="salePrice" label="销售价" width="130" align="right" />
        <el-table-column label="库存范围" width="160" align="center">
          <template #default="{ row }">{{ row.stockLower }} ~ {{ row.stockUpper }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right" align="center">
          <template #default="{ row }">
            <el-button v-perm="'base:goods:edit'" link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button v-perm="'base:goods:delete'" link type="danger" @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无商品数据" />
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

    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新增商品' : '编辑商品'"
      width="900px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="商品编码">
              <el-input v-model="form.code" :disabled="dialogMode === 'edit'" placeholder="留空自动生成" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="商品名称" prop="name">
              <el-input v-model="form.name" placeholder="必填" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="form.categoryId" placeholder="请选择" clearable class="full">
                <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="单位">
              <el-select v-model="form.unitId" placeholder="请选择" clearable class="full">
                <el-option v-for="u in units" :key="u.id" :label="u.name" :value="u.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="规格">
              <el-input v-model="form.spec" placeholder="如 14英寸/i5/16G" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="条码">
              <el-input v-model="form.barcode" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="成本价">
              <el-input-number v-model="form.costPrice" :min="0" :precision="4" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="销售价" prop="salePrice">
              <el-input-number v-model="form.salePrice" :min="0" :precision="4" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="库存下限">
              <el-input-number v-model="form.stockLower" :min="0" :precision="4" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="库存上限">
              <el-input-number v-model="form.stockUpper" :min="0" :precision="4" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-radio-group v-model="form.status">
                <el-radio :value="1">启用</el-radio>
                <el-radio :value="0">停用</el-radio>
              </el-radio-group>
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
    <ImportDialog
      v-model="importDialogVisible"
      @success="fetchList"
    />
  </div>
</template>

<style scoped>
/* 卡片已自带内边距，去掉全局 .query-form 的底部外边距，避免卡片底部多出一段空白 */
.query-form {
  margin-bottom: 0;
}

/* 筛选项在列内撑满，避免固定像素宽度在窄屏断点下溢出 */
.query-form :deep(.el-input),
.query-form :deep(.el-select) {
  width: 100%;
}

/* 查询、重置、新增、导出、导入统一排在查询卡片的最后一行 */
.query-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

/* 两张卡片之间的统一间距 */
.result-card {
  margin-top: 16px;
}

/* footer 插槽内的分页靠右（el-card 已提供底部内边距，这里不再叠加 margin） */
.card-footer {
  display: flex;
  justify-content: flex-end;
}

.full {
  width: 100%;
}
</style>
