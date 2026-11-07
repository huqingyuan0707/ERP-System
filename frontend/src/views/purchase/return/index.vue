<script setup lang="ts">
/**
 * 采购退货页（P-04）。
 * 结构：查询条件 + 退货单列表 + 新增/编辑草稿抽屉（按原入库单带出可退数量）+ 详情/打印抽屉。
 * 与后端保持一致：
 *   - 新建即草稿；确认后同一事务「扣库存/写流水 + 按 source_no 冲减原应付」，不可再改；
 *   - 供应商必须与原入库单一致（选原单后自动带出并锁定）；退货数量 ≤ 可退数量；
 *   - 显示原入库数量/已退数量/可退数量；确认前提示「将减少库存并冲减应付」；
 *   - 退货金额 ≤ 原应付当前余额；金额以后端重算结果为准。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { getSupplierOptions } from '@/api/supplier'
import {
  confirmPurReturn,
  createPurReturn,
  deletePurReturn,
  getReturnSource,
  getPurReturnDetail,
  getPurReturnList,
  updatePurReturn,
} from '@/api/purchaseReturn'
import { getWarehouseList } from '@/api/warehouse'
import BaseCard from '@/components/BaseCard.vue'
import type { Supplier } from '@/types/supplier'
import type { Warehouse } from '@/types/warehouse'
import {
  RETURN_STATUS,
  RETURN_STATUS_LABEL,
  RETURN_STATUS_TAG,
  isReturnDraft,
  type PurReturn,
  type PurReturnItemPayload,
  type PurReturnQuery,
  type SourceInbound,
  type SourceInboundItem,
} from '@/types/purchaseReturn'

// ---------- 通用格式化（前端只做展示，金额以后端重算结果为准） ----------

const toNum = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}
const round2 = (value: number): number => Math.round((value + Number.EPSILON) * 100) / 100
const money = (value: string | number | null | undefined): string => toNum(value).toFixed(2)
const formatQty = (value: string | number | null | undefined): string => {
  const num = toNum(value)
  return Number.isFinite(num) ? String(Number(num.toFixed(4))) : '0'
}

/** 行金额：round(qty × 退货单价, 2)，仅展示。 */
const lineAmount = (item: ReturnItemForm): number => round2(toNum(item.qty) * toNum(item.price))

// ---------- 列表与筛选 ----------

const loading = ref(false)
const rows = ref<PurReturn[]>([])
const total = ref(0)
const query = reactive<PurReturnQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  supplierId: null,
  warehouseId: null,
  sourceNo: null,
  returnDateStart: null,
  returnDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)

const supplierOptions = ref<Supplier[]>([])
const warehouseOptions = ref<Warehouse[]>([])

const fetchSupplierOptions = async (): Promise<void> => {
  supplierOptions.value = await getSupplierOptions()
}

const fetchWarehouseOptions = async (): Promise<void> => {
  const result = await getWarehouseList({ page: 1, pageSize: 100, status: 1 })
  warehouseOptions.value = result.items
}

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.returnDateStart = dateRange.value?.[0] ?? null
    query.returnDateEnd = dateRange.value?.[1] ?? null
    const result = await getPurReturnList(query)
    rows.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

const handleSearch = (): void => {
  query.page = 1
  void fetchList()
}

const handleReset = (): void => {
  query.keyword = ''
  query.status = null
  query.supplierId = null
  query.warehouseId = null
  query.sourceNo = null
  dateRange.value = null
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

const statusLabel = (status: number): string => RETURN_STATUS_LABEL[status] ?? '未知'
const statusTag = (status: number): 'info' | 'success' => RETURN_STATUS_TAG[status] ?? 'info'

// ---------- 新增 / 编辑草稿 ----------

interface ReturnItemForm {
  /** 表格渲染用的稳定 key，不提交给后端 */
  key: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: string
  price: string
  /** 原入库数量（展示） */
  originalQty: number
  /** 已退数量（展示） */
  returnedQty: number
  /** 可退数量（本次数量上限） */
  returnableQty: number
}

interface ReturnForm {
  id: number | null
  supplierId: number | null
  supplierName: string
  warehouseId: number | null
  sourceNo: string
  returnDate: string
  remark: string
  version: number | null
  /** 原应付当前余额（确认前提示与前端粗校验用） */
  payableAmount: number
  items: ReturnItemForm[]
}

let itemKeySeed = 0
const nextItemKey = (): number => {
  itemKeySeed += 1
  return itemKeySeed
}

function createEmptyForm(): ReturnForm {
  return {
    id: null,
    supplierId: null,
    supplierName: '',
    warehouseId: null,
    sourceNo: '',
    returnDate: new Date().toISOString().slice(0, 10),
    remark: '',
    version: null,
    payableAmount: 0,
    items: [],
  }
}

const form = reactive<ReturnForm>(createEmptyForm())
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const sourceLoading = ref(false)
const sourceNotFound = ref(false)
const formRef = ref<FormInstance>()

const formRules: FormRules = {
  sourceNo: [{ required: true, message: '请输入原入库单号并查询', trigger: 'blur' }],
  warehouseId: [{ required: true, message: '请选择退货出库仓库', trigger: 'change' }],
  returnDate: [{ required: true, message: '请选择退货日期', trigger: 'change' }],
}

const totalQty = computed(() => form.items.reduce((sum, item) => sum + toNum(item.qty), 0))
const totalAmount = computed(() => form.items.reduce((sum, item) => sum + lineAmount(item), 0))

/** 把原单聚合明细转换成可编辑行（可退数量为 0 的行不带出）。 */
const toFormItems = (items: SourceInboundItem[], qtyOf: (item: SourceInboundItem) => string) =>
  items
    .filter((item) => toNum(item.returnableQty) > 0)
    .map((item) => ({
      key: nextItemKey(),
      goodsId: item.goodsId,
      goodsCode: item.goodsCode,
      goodsName: item.goodsName,
      spec: item.spec,
      unitName: item.unitName,
      qty: qtyOf(item),
      price: String(item.price),
      originalQty: toNum(item.originalQty),
      returnedQty: toNum(item.returnedQty),
      returnableQty: toNum(item.returnableQty),
    }))

/** 按原入库单号查询可退数据：带出供应商/仓库/原入库数量/已退数量/可退数量。 */
const handleSourceLookup = async (): Promise<void> => {
  const sourceNo = form.sourceNo.trim()
  if (!sourceNo) {
    ElMessage.warning('请输入原入库单号')
    return
  }
  sourceLoading.value = true
  sourceNotFound.value = false
  try {
    const source: SourceInbound = await getReturnSource(sourceNo)
    form.supplierId = source.supplierId
    form.supplierName = source.supplierName ?? ''
    if (!form.warehouseId) form.warehouseId = source.warehouseId
    form.payableAmount = toNum(source.payable?.amount ?? 0)
    form.items = toFormItems(source.items, (item) => String(item.returnableQty))
    if (form.items.length === 0) {
      ElMessage.warning('该原入库单没有可退数量（可能已全部退完）')
    }
  } catch {
    sourceNotFound.value = true
    form.supplierId = null
    form.supplierName = ''
    form.items = []
    form.payableAmount = 0
  } finally {
    sourceLoading.value = false
  }
}

const openCreate = async (): Promise<void> => {
  drawerMode.value = 'create'
  Object.assign(form, createEmptyForm())
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (warehouseOptions.value.length === 0) await fetchWarehouseOptions()
}

const openEdit = async (row: PurReturn): Promise<void> => {
  const detail = await getPurReturnDetail(row.id)
  drawerMode.value = 'edit'
  Object.assign(form, {
    id: detail.id,
    supplierId: detail.supplierId,
    supplierName: detail.supplierName ?? '',
    warehouseId: detail.warehouseId,
    sourceNo: detail.sourceNo,
    returnDate: detail.returnDate,
    remark: detail.remark ?? '',
    version: detail.version,
    payableAmount: 0,
    items: detail.items.map((item) => ({
      key: nextItemKey(),
      goodsId: item.goodsId,
      goodsCode: item.goodsCode,
      goodsName: item.goodsName,
      spec: item.spec,
      unitName: item.unitName,
      qty: String(item.qty),
      price: String(item.price),
      originalQty: toNum(item.originalQty),
      returnedQty: toNum(item.returnedQty),
      returnableQty: toNum(item.returnableQty),
    })),
  })
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (warehouseOptions.value.length === 0) await fetchWarehouseOptions()
}

const validateItems = (): string | null => {
  const active = form.items.filter((item) => toNum(item.qty) > 0)
  if (active.length === 0) return '至少填写一行退货数量'
  for (const item of active) {
    if (toNum(item.qty) > item.returnableQty) {
      return `商品「${item.goodsName ?? item.goodsId}」退货数量超过可退数量 ${formatQty(item.returnableQty)}`
    }
    if (!(toNum(item.price) >= 0)) {
      return `商品「${item.goodsName ?? item.goodsId}」退货单价必须 ≥ 0`
    }
  }
  return null
}

const handleSave = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (form.supplierId === null) {
    ElMessage.warning('请先查询原入库单（供应商由原单带出）')
    return
  }
  const itemError = validateItems()
  if (itemError) {
    ElMessage.warning(itemError)
    return
  }
  submitting.value = true
  try {
    const items: PurReturnItemPayload[] = form.items
      .filter((item) => toNum(item.qty) > 0)
      .map((item) => ({
        goodsId: item.goodsId,
        qty: item.qty,
        price: item.price,
      }))
    const payload = {
      supplierId: Number(form.supplierId),
      warehouseId: Number(form.warehouseId),
      sourceNo: form.sourceNo.trim(),
      returnDate: form.returnDate,
      remark: form.remark.trim() || null,
      items,
    }
    if (drawerMode.value === 'create') {
      await createPurReturn(payload, `pur-return-create-${Date.now()}`)
      ElMessage.success('已保存为草稿')
    } else if (form.id !== null) {
      await updatePurReturn(
        form.id,
        { ...payload, version: form.version ?? undefined },
        `pur-return-update-${form.id}-${Date.now()}`,
      )
      ElMessage.success('草稿已更新')
    }
    drawerVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

// ---------- 确认 / 删除 ----------

const handleConfirm = async (row: PurReturn): Promise<void> => {
  await ElMessageBox.confirm(
    `确定确认退货单「${row.returnNo}」吗？确认后将减少库存并冲减应付，确认后不可修改。`,
    '确认退货',
    { type: 'warning' },
  )
  await confirmPurReturn(row.id, `pur-return-confirm-${row.id}-${Date.now()}`)
  ElMessage.success('退货成功：库存已减少、原应付已冲减')
  await fetchList()
}

const handleDelete = async (row: PurReturn): Promise<void> => {
  await ElMessageBox.confirm(`确定删除草稿「${row.returnNo}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deletePurReturn(row.id, `pur-return-delete-${row.id}-${Date.now()}`)
  ElMessage.success('删除成功')
  await fetchList()
}

// ---------- 详情 / 打印 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<PurReturn | null>(null)

const openDetail = async (row: PurReturn): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getPurReturnDetail(row.id)
  } finally {
    detailLoading.value = false
  }
}

/** 详情退货数量合计（打印用）。 */
const detailQty = computed(() =>
  (detail.value?.items ?? []).reduce((sum, item) => sum + toNum(item.qty), 0),
)

/** 打印：只打印抽屉内的打印区（抬头 + 明细 + 合计 + 签字栏）。 */
const handlePrint = (): void => {
  window.print()
}

onMounted(() => {
  void Promise.all([fetchList(), fetchSupplierOptions(), fetchWarehouseOptions()])
})
</script>

<template>
  <div class="page-container return-page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="按状态、供应商、仓库、原入库单号与退货日期筛选；确认退货将减少库存并冲减应付。"
        >
          <el-form class="query-form" label-width="90px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="退货单号 / 原单号 / 备注"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable style="width: 100%">
                    <el-option label="草稿" :value="RETURN_STATUS.DRAFT" />
                    <el-option label="已确认" :value="RETURN_STATUS.CONFIRMED" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="供应商">
                  <el-select
                    v-model="query.supplierId"
                    placeholder="全部"
                    clearable
                    filterable
                    style="width: 100%"
                  >
                    <el-option
                      v-for="supplier in supplierOptions"
                      :key="supplier.id"
                      :label="`${supplier.code} ${supplier.name}`"
                      :value="supplier.id"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="原入库单号">
                  <el-input
                    v-model="query.sourceNo"
                    placeholder="精确匹配"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="退货日期">
                  <el-date-picker
                    v-model="dateRange"
                    type="daterange"
                    value-format="YYYY-MM-DD"
                    start-placeholder="开始日期"
                    end-placeholder="结束日期"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <div class="query-actions">
              <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
              <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
              <el-button v-perm="'pur:return:add'" type="primary" :icon="'Plus'" @click="openCreate">
                新增退货单
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="采购退货单列表"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="returnNo" label="退货单号" width="180" />
            <el-table-column prop="sourceNo" label="原入库单号" width="180" />
            <el-table-column label="供应商" min-width="140">
              <template #default="{ row }">{{ row.supplierName || '-' }}</template>
            </el-table-column>
            <el-table-column label="退货仓库" min-width="110">
              <template #default="{ row }">{{ row.warehouseName || '-' }}</template>
            </el-table-column>
            <el-table-column label="退货日期" width="110" align="center">
              <template #default="{ row }">{{ row.returnDate || '-' }}</template>
            </el-table-column>
            <el-table-column label="退货金额" width="130" align="right">
              <template #default="{ row }">¥ {{ money(row.totalAmount) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="明细行数" width="90" align="center">
              <template #default="{ row }">{{ row.itemsCount }}</template>
            </el-table-column>
            <el-table-column label="操作" width="230" fixed="right" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDetail(row)">详情</el-button>
                <el-button
                  v-if="isReturnDraft(row.status)"
                  v-perm="'pur:return:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="isReturnDraft(row.status)"
                  v-perm="'pur:return:confirm'"
                  link
                  type="success"
                  @click="handleConfirm(row)"
                >
                  确认
                </el-button>
                <el-button
                  v-if="isReturnDraft(row.status)"
                  v-perm="'pur:return:edit'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无采购退货单" />
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

    <!-- 新增 / 编辑草稿：原入库单号查询带出可退数量，供应商由原单锁定 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新增采购退货单' : '编辑采购退货单草稿'"
      size="1080px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="20">
          <el-col :span="10">
            <el-form-item label="原入库单号" prop="sourceNo">
              <div class="source-row">
                <el-input
                  v-model="form.sourceNo"
                  placeholder="如 PI202611070001"
                  :disabled="drawerMode === 'edit'"
                  @keyup.enter="handleSourceLookup"
                />
                <el-button
                  :loading="sourceLoading"
                  :disabled="drawerMode === 'edit'"
                  @click="handleSourceLookup"
                >
                  查询
                </el-button>
              </div>
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="供应商">
              <el-input :model-value="form.supplierName" disabled placeholder="由原入库单带出" />
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="退货仓库" prop="warehouseId">
              <el-select
                v-model="form.warehouseId"
                placeholder="请选择仓库"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="warehouse in warehouseOptions"
                  :key="warehouse.id"
                  :label="warehouse.name"
                  :value="warehouse.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="20">
          <el-col :span="10">
            <el-form-item label="退货日期" prop="returnDate">
              <el-date-picker
                v-model="form.returnDate"
                type="date"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="14">
            <el-form-item label="备注">
              <el-input v-model="form.remark" maxlength="255" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>

        <div class="item-toolbar">
          <span class="item-title">退货明细（本次数量 ≤ 可退数量；单价默认取原入库单价）</span>
          <span v-if="form.payableAmount > 0" class="payable-hint">
            原应付当前余额：¥ {{ money(form.payableAmount) }}（退货金额不得超过该余额）
          </span>
        </div>

        <el-alert
          v-if="sourceNotFound"
          type="error"
          :closable="false"
          show-icon
          title="未找到该入库单，或其尚未确认、不可退货"
          class="source-alert"
        />

        <el-table :data="form.items" border size="small" row-key="key">
          <el-table-column label="商品编码" width="120">
            <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
          </el-table-column>
          <el-table-column label="商品名称" min-width="150">
            <template #default="{ row }">{{ row.goodsName || '-' }}</template>
          </el-table-column>
          <el-table-column label="单位" width="70" align="center">
            <template #default="{ row }">{{ row.unitName || '-' }}</template>
          </el-table-column>
          <el-table-column label="原入库数量" width="105" align="right">
            <template #default="{ row }">{{ formatQty(row.originalQty) }}</template>
          </el-table-column>
          <el-table-column label="已退数量" width="95" align="right">
            <template #default="{ row }">{{ formatQty(row.returnedQty) }}</template>
          </el-table-column>
          <el-table-column label="可退数量" width="95" align="right">
            <template #default="{ row }">
              <span class="returnable">{{ formatQty(row.returnableQty) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="退货数量" width="120">
            <template #default="{ row }">
              <el-input v-model="row.qty" placeholder="0 表示不退" />
            </template>
          </el-table-column>
          <el-table-column label="退货单价" width="110">
            <template #default="{ row }">
              <el-input v-model="row.price" placeholder="≥0" />
            </template>
          </el-table-column>
          <el-table-column label="行金额" width="110" align="right">
            <template #default="{ row }">{{ money(lineAmount(row)) }}</template>
          </el-table-column>
        </el-table>

        <div class="totals">
          <span>数量合计：<b>{{ formatQty(totalQty) }}</b></span>
          <span>退货金额合计：<b>¥ {{ money(totalAmount) }}</b></span>
        </div>
        <p class="form-tip">
          提示：库存按当前移动平均成本出库（成本不变），财务按退货单价冲减原应付的金额与未付金额，
          差额体现在毛利。确认前请核对退货金额不超过原应付当前余额。
        </p>
      </el-form>

      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存草稿</el-button>
      </template>
    </el-drawer>

    <!-- 详情 + 打印：打印区单独包裹，@media print 只显示该区域 -->
    <el-drawer v-model="detailVisible" title="采购退货单详情" size="1000px" destroy-on-close>
      <div v-loading="detailLoading" class="print-area">
        <template v-if="detail">
          <h2 class="print-title">采购退货单</h2>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="退货单号">{{ detail.returnNo }}</el-descriptions-item>
            <el-descriptions-item label="原入库单号">{{ detail.sourceNo }}</el-descriptions-item>
            <el-descriptions-item label="供应商">{{ detail.supplierName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="退货仓库">{{ detail.warehouseName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="退货日期">{{ detail.returnDate || '-' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTag(detail.status)" size="small">
                {{ statusLabel(detail.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="退货金额">¥ {{ money(detail.totalAmount) }}</el-descriptions-item>
            <el-descriptions-item label="单据时间">
              {{ detail.createdAt?.replace('T', ' ').slice(0, 19) || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="备注">{{ detail.remark || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-table :data="detail.items" border size="small" class="print-table">
            <el-table-column type="index" label="序号" width="60" align="center" />
            <el-table-column label="商品编码" width="120">
              <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="商品名称" min-width="150">
              <template #default="{ row }">{{ row.goodsName || '-' }}</template>
            </el-table-column>
            <el-table-column label="单位" width="70" align="center">
              <template #default="{ row }">{{ row.unitName || '-' }}</template>
            </el-table-column>
            <el-table-column label="退货数量" width="95" align="right">
              <template #default="{ row }">{{ formatQty(row.qty) }}</template>
            </el-table-column>
            <el-table-column label="退货单价" width="100" align="right">
              <template #default="{ row }">{{ money(row.price) }}</template>
            </el-table-column>
            <el-table-column label="行金额" width="110" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="原入库数量" width="105" align="right">
              <template #default="{ row }">{{ formatQty(row.originalQty) }}</template>
            </el-table-column>
            <el-table-column label="已退数量" width="95" align="right">
              <template #default="{ row }">{{ formatQty(row.returnedQty) }}</template>
            </el-table-column>
            <el-table-column label="可退数量" width="95" align="right">
              <template #default="{ row }">{{ formatQty(row.returnableQty) }}</template>
            </el-table-column>
          </el-table>

          <div class="print-totals">
            <span>数量合计：{{ formatQty(detailQty) }}</span>
            <span>退货金额合计：¥ {{ money(detail.totalAmount) }}</span>
          </div>

          <div class="print-footer">
            <span>仓管员签字：______________</span>
            <span>采购员签字：______________</span>
            <span>日期：______________</span>
          </div>
        </template>
      </div>

      <template #footer>
        <el-button :icon="'Printer'" @click="handlePrint">打印</el-button>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.return-page {
  padding-bottom: 8px;
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

.source-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.source-alert {
  margin: 8px 0;
}

.item-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
}

.item-title {
  font-weight: 600;
  color: #303133;
}

.payable-hint {
  font-size: 12px;
  color: #e6a23c;
}

.returnable {
  color: #67c23a;
  font-weight: 600;
}

.totals {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  justify-content: flex-end;
  margin-top: 12px;
  font-size: 14px;
  color: #303133;
}

.totals b {
  color: #409eff;
}

.form-tip {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #909399;
}

.print-title {
  margin: 4px 0 16px;
  font-size: 20px;
  text-align: center;
  letter-spacing: 4px;
}

.print-table {
  margin-top: 16px;
}

.print-totals {
  display: flex;
  justify-content: flex-end;
  gap: 24px;
  margin-top: 12px;
  font-size: 14px;
  color: #303133;
}

.print-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  font-size: 13px;
  color: #303133;
}

@media print {
  :global(body *) {
    visibility: hidden;
  }

  .print-area,
  .print-area * {
    visibility: visible;
  }

  .print-area {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    padding: 0 8mm;
  }
}
</style>
