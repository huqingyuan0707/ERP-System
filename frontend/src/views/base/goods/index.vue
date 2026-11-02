<script setup lang="ts">
/**
 * 商品管理页（BASE-04/05/06）。
 * 结构：左侧商品分类树（分类增删改/筛选） + 右侧查询条件/结果卡片 + 商品表单/分类表单/单位管理弹窗。
 * 分类与单位均带引用计数，存在子分类或商品引用时禁用删除，与后端删除保护（409）保持一致。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import {
  createCategory,
  createGoods,
  createUnit,
  deleteCategory,
  deleteGoods,
  deleteUnit,
  getCategories,
  getGoodsList,
  getUnits,
  updateCategory,
  updateGoods,
  updateUnit,
} from '@/api/goods'
import type { Goods, GoodsCategory, GoodsQuery, Unit } from '@/types/goods'
import BaseCard from '@/components/BaseCard.vue'
import ExportButton from '@/components/ExportButton.vue'
import ImportDialog from '@/components/ImportDialog.vue'

/** 分类树节点：在扁平分类上补充 children，synthetic 表示合成的「全部商品」节点。 */
interface CategoryTreeNode extends GoodsCategory {
  children: CategoryTreeNode[]
  synthetic?: boolean
}

const loading = ref(false)
const rows = ref<Goods[]>([])
const total = ref(0)
const categories = ref<GoodsCategory[]>([])
const units = ref<Unit[]>([])
const categoryLoading = ref(false)
const unitLoading = ref(false)

const query = reactive<GoodsQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  categoryId: null,
  status: null,
})

/** 当前高亮的分类节点 key：0 表示「全部商品」。 */
const activeCategoryKey = ref(0)

// ---------- 分类树 ----------

/** 把扁平分类列表组装成树，并按 sort、id 排序，保证父子顺序稳定。 */
const buildCategoryTree = (list: GoodsCategory[]): CategoryTreeNode[] => {
  const nodes = new Map<number, CategoryTreeNode>()
  list.forEach((item) => nodes.set(item.id, { ...item, children: [] }))
  const roots: CategoryTreeNode[] = []
  nodes.forEach((node) => {
    const parent = node.parentId === null ? undefined : nodes.get(node.parentId)
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })
  const sortNodes = (items: CategoryTreeNode[]): void => {
    items.sort((left, right) => left.sort - right.sort || left.id - right.id)
    items.forEach((item) => sortNodes(item.children))
  }
  sortNodes(roots)
  return roots
}

const categoryRoots = computed<CategoryTreeNode[]>(() => buildCategoryTree(categories.value))

/** 树控件数据：顶层加一个「全部商品」合成节点，点击即可清除分类筛选。 */
const categoryTree = computed<CategoryTreeNode[]>(() => [
  {
    id: 0,
    name: '全部商品',
    parentId: null,
    sort: -1,
    remark: null,
    createdAt: null,
    childrenCount: categoryRoots.value.length,
    goodsCount: 0,
    children: categoryRoots.value,
    synthetic: true,
  },
])

const handleCategorySelect = (node: CategoryTreeNode): void => {
  activeCategoryKey.value = node.id
  query.categoryId = node.synthetic ? null : node.id
  query.page = 1
  void fetchList()
}

const fetchCategories = async (): Promise<void> => {
  categoryLoading.value = true
  try {
    categories.value = await getCategories()
  } finally {
    categoryLoading.value = false
  }
}

// ---------- 商品列表 ----------

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

const fetchUnits = async (): Promise<void> => {
  unitLoading.value = true
  try {
    units.value = await getUnits()
  } finally {
    unitLoading.value = false
  }
}

const handleSearch = (): void => {
  query.page = 1
  // 查询时同步树高亮，保证下拉框选择分类后左侧树状态一致
  activeCategoryKey.value = query.categoryId ?? 0
  void fetchList()
}

const handleReset = (): void => {
  query.keyword = ''
  query.categoryId = null
  query.status = null
  activeCategoryKey.value = 0
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

// ---------- 商品表单 ----------

const dialogVisible = ref(false)
const importDialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)

interface GoodsForm {
  code: string
  name: string
  categoryId: number | null
  spec: string
  unitId: number | null
  barcode: string
  costPrice: string
  salePrice: string
  stockLower: string
  stockUpper: string
  status: number
  remark: string
}

const createEmptyForm = (): GoodsForm => ({
  code: '',
  name: '',
  categoryId: null,
  spec: '',
  unitId: null,
  barcode: '',
  costPrice: '0',
  salePrice: '0',
  stockLower: '0',
  stockUpper: '0',
  status: 1,
  remark: '',
})

const form = reactive<GoodsForm>(createEmptyForm())
const quantityPrecision = computed(() => units.value.find((unit) => unit.id === form.unitId)?.precision ?? 4)

const decimalUnits = (value: string, precision: number, integerDigits: number): bigint | null => {
  const match = /^(\d+)(?:\.(\d*))?$/.exec(value.trim())
  if (!match) return null
  const integer = match[1]!.replace(/^0+(?=\d)/, '')
  const fraction = (match[2] ?? '').replace(/0+$/, '')
  if (integer.length > integerDigits || fraction.length > precision) return null
  return BigInt(integer + fraction.padEnd(4, '0'))
}

const formatPrice = (value: string | number): string => {
  const [integer, fraction = ''] = String(value).split('.')
  return `${integer}.${fraction.padEnd(2, '0')}`
}

const draftKeys = new Map<string, { body: string; key: string }>()
const submissionKey = (scope: string, payload: unknown): string => {
  const body = JSON.stringify(payload)
  const previous = draftKeys.get(scope)
  if (previous?.body === body) return previous.key
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  const key = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  draftKeys.set(scope, { body, key })
  return key
}

const rules: FormRules = {
  name: [{ required: true, whitespace: true, message: '请输入商品名称', trigger: 'blur' }],
  salePrice: [{ required: true, message: '请输入销售价', trigger: 'blur' }],
}

const openCreate = (): void => {
  draftKeys.delete('goods')
  dialogMode.value = 'create'
  editingId.value = null
  Object.assign(form, createEmptyForm())
  // 左侧树已选中某分类时，新增商品默认带入该分类，减少重复选择
  form.categoryId = query.categoryId ?? null
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
    costPrice: String(row.costPrice),
    salePrice: String(row.salePrice),
    stockLower: String(row.stockLower),
    stockUpper: String(row.stockUpper),
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
}

const handleSubmit = async (): Promise<void> => {
  if (submitting.value) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const lower = decimalUnits(form.stockLower, quantityPrecision.value, 14)
  const upper = decimalUnits(form.stockUpper, quantityPrecision.value, 14)
  if (decimalUnits(form.costPrice, 2, 16) === null || decimalUnits(form.salePrice, 2, 16) === null) {
    ElMessage.error('价格须为非负数，整数最多16位，小数最多2位')
    return
  }
  if (lower === null || upper === null) {
    ElMessage.error(`库存上下限须为非负数，整数最多14位，小数最多${quantityPrecision.value}位`)
    return
  }
  if (lower > upper) {
    ElMessage.error('库存下限不能大于上限')
    return
  }
  submitting.value = true
  try {
    const payload = {
      name: form.name.trim(),
      categoryId: form.categoryId || null,
      spec: form.spec || null,
      unitId: form.unitId || null,
      barcode: form.barcode.trim() || null,
      costPrice: form.costPrice.trim(),
      salePrice: form.salePrice.trim(),
      stockLower: form.stockLower.trim(),
      stockUpper: form.stockUpper.trim(),
      status: form.status,
      remark: form.remark || null,
    }
    if (dialogMode.value === 'create') {
      const createPayload = { ...payload, code: form.code.trim() || null }
      await createGoods(createPayload, submissionKey('goods', createPayload))
      ElMessage.success('新增成功')
    } else if (editingId.value !== null) {
      await updateGoods(editingId.value, payload)
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    await Promise.all([fetchList(), fetchCategories()])
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
  await Promise.all([fetchList(), fetchCategories()])
}

/** 商品列表内直接启用/停用：失败时重新拉取，让开关回到后端真实状态。 */
const handleStatusChange = async (row: Goods, value: unknown): Promise<void> => {
  const status = Number(value)
  try {
    await updateGoods(row.id, { status })
    row.status = status
    ElMessage.success(status === 1 ? '已启用' : '已停用')
  } catch {
    void fetchList()
  }
}

const openImport = (): void => {
  importDialogVisible.value = true
}

/** 导入成功后同时刷新商品列表与分类计数（导入可能给分类新增商品）。 */
const handleImportSuccess = (): void => {
  void Promise.all([fetchList(), fetchCategories()])
}

// ---------- 分类表单 ----------

const categoryDialogVisible = ref(false)
const categoryDialogMode = ref<'create' | 'edit'>('create')
const categorySubmitting = ref(false)
const categoryFormRef = ref<FormInstance>()
const editingCategoryId = ref<number | null>(null)

interface CategoryForm {
  name: string
  parentId: number | null
  sort: number
  remark: string
}

const createEmptyCategoryForm = (): CategoryForm => ({
  name: '',
  parentId: null,
  sort: 0,
  remark: '',
})

const categoryForm = reactive<CategoryForm>(createEmptyCategoryForm())

const categoryRules: FormRules = {
  name: [{ required: true, whitespace: true, message: '请输入分类名称', trigger: 'blur' }],
}

/** 上级分类选项：编辑时剔除自身及其子树，避免形成循环引用。 */
const categoryParentOptions = computed<CategoryTreeNode[]>(() => {
  const excludeId = categoryDialogMode.value === 'edit' ? editingCategoryId.value : null
  const prune = (nodes: CategoryTreeNode[]): CategoryTreeNode[] =>
    nodes
      .filter((node) => node.id !== excludeId)
      .map((node) => ({ ...node, children: prune(node.children) }))
  return prune(categoryRoots.value)
})

const openCategoryCreate = (parent: CategoryTreeNode | null): void => {
  draftKeys.delete('category')
  categoryDialogMode.value = 'create'
  editingCategoryId.value = null
  Object.assign(categoryForm, createEmptyCategoryForm())
  categoryForm.parentId = parent && !parent.synthetic ? parent.id : null
  categoryDialogVisible.value = true
  nextTick(() => categoryFormRef.value?.clearValidate())
}

const openCategoryEdit = (node: CategoryTreeNode): void => {
  categoryDialogMode.value = 'edit'
  editingCategoryId.value = node.id
  Object.assign(categoryForm, {
    name: node.name,
    parentId: node.parentId,
    sort: node.sort,
    remark: node.remark ?? '',
  })
  categoryDialogVisible.value = true
  nextTick(() => categoryFormRef.value?.clearValidate())
}

const handleCategorySubmit = async (): Promise<void> => {
  if (categorySubmitting.value) return
  const valid = await categoryFormRef.value?.validate().catch(() => false)
  if (!valid) return
  categorySubmitting.value = true
  try {
    const payload = {
      name: categoryForm.name.trim(),
      parentId: categoryForm.parentId || null,
      sort: categoryForm.sort,
      remark: categoryForm.remark.trim() || null,
    }
    if (categoryDialogMode.value === 'create') {
      await createCategory(payload, submissionKey('category', payload))
      ElMessage.success('分类新增成功')
    } else if (editingCategoryId.value !== null) {
      await updateCategory(editingCategoryId.value, payload)
      ElMessage.success('分类修改成功')
    }
    categoryDialogVisible.value = false
    await fetchCategories()
  } finally {
    categorySubmitting.value = false
  }
}

const handleCategoryDelete = async (node: CategoryTreeNode): Promise<void> => {
  // 前端先行拦截被引用的分类，后端仍会返回 409 做最终保护
  if (node.childrenCount > 0 || node.goodsCount > 0) {
    ElMessage.warning('该分类下存在子分类或商品，无法删除')
    return
  }
  await ElMessageBox.confirm(`确定删除分类「${node.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteCategory(node.id)
  ElMessage.success('删除成功')
  if (activeCategoryKey.value === node.id) {
    activeCategoryKey.value = 0
    query.categoryId = null
  }
  await Promise.all([fetchCategories(), fetchList()])
}

// ---------- 单位管理 ----------

const unitDialogVisible = ref(false)
const unitSubmitting = ref(false)
const unitFormRef = ref<FormInstance>()
const editingUnitId = ref<number | null>(null)

interface UnitForm {
  name: string
  precision: number
  remark: string
}

const createEmptyUnitForm = (): UnitForm => ({ name: '', precision: 0, remark: '' })
const unitForm = reactive<UnitForm>(createEmptyUnitForm())

const unitRules: FormRules = {
  name: [{ required: true, whitespace: true, message: '请输入单位名称', trigger: 'blur' }],
}

const openUnitDialog = (): void => {
  resetUnitForm()
  unitDialogVisible.value = true
  void fetchUnits()
}

const resetUnitForm = (): void => {
  draftKeys.delete('unit')
  editingUnitId.value = null
  Object.assign(unitForm, createEmptyUnitForm())
  nextTick(() => unitFormRef.value?.clearValidate())
}

const handleUnitEdit = (row: Unit): void => {
  editingUnitId.value = row.id
  Object.assign(unitForm, {
    name: row.name,
    precision: row.precision,
    remark: row.remark ?? '',
  })
}

const handleUnitSubmit = async (): Promise<void> => {
  if (unitSubmitting.value) return
  const valid = await unitFormRef.value?.validate().catch(() => false)
  if (!valid) return
  unitSubmitting.value = true
  try {
    const payload = {
      name: unitForm.name.trim(),
      precision: unitForm.precision,
      remark: unitForm.remark.trim() || null,
    }
    if (editingUnitId.value === null) {
      await createUnit(payload, submissionKey('unit', payload))
      ElMessage.success('单位新增成功')
    } else {
      await updateUnit(editingUnitId.value, payload)
      ElMessage.success('单位修改成功')
    }
    resetUnitForm()
    await Promise.all([fetchUnits(), fetchList()])
  } finally {
    unitSubmitting.value = false
  }
}

const handleUnitDelete = async (row: Unit): Promise<void> => {
  if (row.goodsCount > 0) {
    ElMessage.warning('该单位已被商品引用，无法删除')
    return
  }
  await ElMessageBox.confirm(`确定删除单位「${row.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteUnit(row.id)
  ElMessage.success('删除成功')
  await Promise.all([fetchUnits(), fetchList()])
}

onMounted(() => {
  void fetchList()
  void Promise.all([fetchCategories(), fetchUnits()])
})
</script>

<template>
  <div class="page-container goods-page">
    <el-row :gutter="16">
      <!-- 左侧：商品分类树，支持新增子类、编辑、删除与筛选 -->
      <el-col :xs="24" :sm="24" :md="8" :lg="6">
        <BaseCard
          title="商品分类"
          subtitle="点击分类筛选商品，可维护层级与排序。"
          :loading="categoryLoading"
          class="category-card"
        >
          <template #extra>
            <el-button
              v-perm="'base:category:add'"
              size="small"
              type="primary"
              :icon="'Plus'"
              @click="openCategoryCreate(null)"
            >
              新增
            </el-button>
            <el-button
              v-perm="'base:unit:list'"
              size="small"
              :icon="'Setting'"
              @click="openUnitDialog"
            >
              单位
            </el-button>
          </template>

          <el-tree
            class="category-tree"
            :data="categoryTree"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            default-expand-all
            highlight-current
            :expand-on-click-node="false"
            :current-node-key="activeCategoryKey"
            @node-click="handleCategorySelect"
          >
            <template #default="{ data }">
              <div class="category-node" :class="{ 'is-synthetic': data.synthetic }">
                <span class="category-node__label">
                  {{ data.name }}
                  <el-tag v-if="!data.synthetic && data.goodsCount > 0" size="small" type="info">
                    {{ data.goodsCount }}
                  </el-tag>
                </span>
                <span v-if="!data.synthetic" class="category-node__actions" @click.stop>
                  <el-button
                    v-perm="'base:category:add'"
                    link
                    size="small"
                    type="primary"
                    @click="openCategoryCreate(data)"
                  >
                    子类
                  </el-button>
                  <el-button
                    v-perm="'base:category:edit'"
                    link
                    size="small"
                    @click="openCategoryEdit(data)"
                  >
                    编辑
                  </el-button>
                  <el-tooltip
                    :disabled="data.childrenCount === 0 && data.goodsCount === 0"
                    content="存在子分类或商品，无法删除"
                  >
                    <el-button
                      v-perm="'base:category:delete'"
                      link
                      size="small"
                      type="danger"
                      :disabled="data.childrenCount > 0 || data.goodsCount > 0"
                      @click="handleCategoryDelete(data)"
                    >
                      删除
                    </el-button>
                  </el-tooltip>
                </span>
              </div>
            </template>
          </el-tree>
        </BaseCard>
      </el-col>

      <!-- 右侧：查询条件与查询结果 -->
      <el-col :xs="24" :sm="24" :md="16" :lg="18">
        <BaseCard
          title="查询条件"
          subtitle="维护商品基础档案，支持分类、单位、价格与库存范围设置。"
        >
          <el-form class="query-form" label-width="72px" @submit.prevent="handleSearch">
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
                  <el-tree-select
                    v-model="query.categoryId"
                    :data="categoryRoots"
                    :props="{ value: 'id', label: 'name', children: 'children' }"
                    check-strictly
                    clearable
                    placeholder="全部"
                    class="full"
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
            <el-table-column prop="spec" label="规格" min-width="140" />
            <el-table-column prop="barcode" label="条码" width="150" />
            <el-table-column prop="categoryName" label="分类" width="120" />
            <el-table-column prop="unitName" label="单位" width="90" />
            <el-table-column label="成本价" width="110" align="right">
              <template #default="{ row }">{{ formatPrice(row.costPrice) }}</template>
            </el-table-column>
            <el-table-column label="销售价" width="110" align="right">
              <template #default="{ row }">{{ formatPrice(row.salePrice) }}</template>
            </el-table-column>
            <el-table-column label="库存范围" width="150" align="center">
              <template #default="{ row }">
                {{ row.stockLower }} ~ {{ row.stockUpper }}
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-switch
                  v-perm="'base:goods:edit'"
                  :model-value="row.status"
                  :active-value="1"
                  :inactive-value="0"
                  @change="handleStatusChange(row, $event)"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  v-perm="'base:goods:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-perm="'base:goods:delete'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
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
      </el-col>
    </el-row>

    <!-- 商品新增/编辑 -->
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
              <el-input
                v-model="form.code"
                maxlength="32"
                :disabled="dialogMode === 'edit'"
                placeholder="留空自动生成"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="商品名称" prop="name">
              <el-input v-model="form.name" maxlength="128" placeholder="必填" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类">
              <el-tree-select
                v-model="form.categoryId"
                :data="categoryRoots"
                :props="{ value: 'id', label: 'name', children: 'children' }"
                check-strictly
                clearable
                placeholder="请选择"
                class="full"
              />
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
              <el-input v-model="form.barcode" maxlength="64" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="成本价">
              <el-input v-model="form.costPrice" inputmode="decimal" placeholder="价格最多2位小数" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="销售价" prop="salePrice">
              <el-input v-model="form.salePrice" inputmode="decimal" placeholder="价格最多2位小数" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="库存下限">
              <el-input v-model="form.stockLower" inputmode="decimal" placeholder="按所选单位精度填写" class="full" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="库存上限">
              <el-input v-model="form.stockUpper" inputmode="decimal" placeholder="按所选单位精度填写" class="full" />
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

    <!-- 分类新增/编辑 -->
    <el-dialog
      v-model="categoryDialogVisible"
      :title="categoryDialogMode === 'create' ? '新增分类' : '编辑分类'"
      width="520px"
      destroy-on-close
    >
      <el-form ref="categoryFormRef" :model="categoryForm" :rules="categoryRules" label-width="90px">
        <el-form-item label="分类名称" prop="name">
          <el-input v-model="categoryForm.name" maxlength="64" placeholder="必填" />
        </el-form-item>
        <el-form-item label="上级分类">
          <el-tree-select
            v-model="categoryForm.parentId"
            :data="categoryParentOptions"
            :props="{ value: 'id', label: 'name', children: 'children' }"
            check-strictly
            clearable
            placeholder="不选则为一级分类"
            class="full"
          />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="categoryForm.sort" :min="0" :precision="0" class="full" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="categoryForm.remark" type="textarea" :rows="3" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="categoryDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="categorySubmitting" @click="handleCategorySubmit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 单位管理 -->
    <el-dialog v-model="unitDialogVisible" title="计量单位管理" width="760px" destroy-on-close>
      <el-form
        ref="unitFormRef"
        :model="unitForm"
        :rules="unitRules"
        label-width="80px"
        class="unit-form"
        @submit.prevent="handleUnitSubmit"
      >
        <el-row :gutter="12">
          <el-col :span="9">
            <el-form-item label="名称" prop="name">
              <el-input v-model="unitForm.name" maxlength="32" placeholder="如 个 / 箱" />
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="精度">
              <el-input-number
                v-model="unitForm.precision"
                :min="0"
                :max="4"
                :precision="0"
                class="full"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label-width="0">
              <el-button v-perm="editingUnitId === null ? 'base:unit:add' : 'base:unit:edit'" type="primary" :loading="unitSubmitting" @click="handleUnitSubmit">
                {{ editingUnitId === null ? '新增' : '保存' }}
              </el-button>
              <el-button v-if="editingUnitId !== null" @click="resetUnitForm">取消</el-button>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="unitForm.remark" maxlength="255" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <el-table v-loading="unitLoading" :data="units" border stripe size="small" max-height="320">
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="precision" label="精度" width="80" align="center" />
        <el-table-column prop="goodsCount" label="引用商品" width="90" align="center" />
        <el-table-column prop="remark" label="备注" min-width="140" />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button v-perm="'base:unit:edit'" link type="primary" @click="handleUnitEdit(row)">
              编辑
            </el-button>
            <el-tooltip
              :disabled="row.goodsCount === 0"
              content="该单位已被商品引用，无法删除"
            >
              <el-button
                v-perm="'base:unit:delete'"
                link
                type="danger"
                :disabled="row.goodsCount > 0"
                @click="handleUnitDelete(row)"
              >
                删除
              </el-button>
            </el-tooltip>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无计量单位" />
        </template>
      </el-table>

      <template #footer>
        <el-button @click="unitDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <ImportDialog v-model="importDialogVisible" @success="handleImportSuccess" />
  </div>
</template>

<style scoped>
/* 卡片已自带内边距，去掉全局 .query-form 的底部外边距，避免卡片底部多出一段空白 */
.query-form {
  margin-bottom: 0;
}

/* 筛选项在列内撑满，避免固定像素宽度在窄屏断点下溢出 */
.query-form :deep(.el-input),
.query-form :deep(.el-select),
.query-form :deep(.el-tree-select) {
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

/* 分类树节点：名称与操作按钮两端对齐，操作按钮在悬停时展示，避免常态拥挤 */
.category-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
  padding-right: 4px;
}

.category-node__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-node__actions {
  display: none;
  flex-shrink: 0;
}

.category-tree :deep(.el-tree-node__content):hover .category-node__actions {
  display: inline-flex;
}

.category-node.is-synthetic .category-node__label {
  font-weight: 600;
}

.unit-form {
  margin-bottom: 12px;
}

.full {
  width: 100%;
}
</style>
