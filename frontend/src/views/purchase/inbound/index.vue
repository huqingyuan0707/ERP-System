<script setup lang="ts">
/**
 * 采购入库页（P-03）。
 * 结构：查询条件 + 入库单列表 + 新增/编辑草稿抽屉（按订单预填剩余数量）+ 详情/打印抽屉。
 * 与后端保持一致：
 *   - 新建即草稿；确认后同一事务「加库存/写流水 + 回写订单 + 生成应付」，不可再改；
 *   - 入库单价取来源订单明细单价（不可修改），本次数量 ≤ 剩余未入库数量；
 *   - 确认前提示「将增加库存并生成应付」；金额以后端重算结果为准。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { getSupplierOptions } from '@/api/supplier'
import {
  confirmPurInbound,
  createPurInbound,
  deletePurInbound,
  getInboundFromOrder,
  getPurInboundDetail,
  getPurInboundList,
  updatePurInbound,
} from '@/api/purchaseInbound'
import { getPurOrderList } from '@/api/purchaseOrder'
import { getWarehouseList } from '@/api/warehouse'
import BaseCard from '@/components/BaseCard.vue'
import type { Supplier } from '@/types/supplier'
import type { Warehouse } from '@/types/warehouse'
import {
  INBOUND_STATUS,
  INBOUND_STATUS_LABEL,
  INBOUND_STATUS_TAG,
  isInboundDraft,
  type PurInbound,
  type PurInboundFromOrderItem,
  type PurInboundItemPayload,
  type PurInboundQuery,
} from '@/types/purchaseInbound'
import { ORDER_STATUS, type PurOrder } from '@/types/purchaseOrder'

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

/** 行金额：round(qty × 订单单价, 2)，仅展示。 */
const lineAmount = (item: InboundItemForm): number => round2(toNum(item.qty) * toNum(item.price))

// ---------- 列表与筛选 ----------

const loading = ref(false)
const rows = ref<PurInbound[]>([])
const total = ref(0)
const query = reactive<PurInboundQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  supplierId: null,
  warehouseId: null,
  inboundDateStart: null,
  inboundDateEnd: null,
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
    query.inboundDateStart = dateRange.value?.[0] ?? null
    query.inboundDateEnd = dateRange.value?.[1] ?? null
    const result = await getPurInboundList(query)
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

const statusLabel = (status: number): string => INBOUND_STATUS_LABEL[status] ?? '未知'
const statusTag = (status: number): 'info' | 'success' => INBOUND_STATUS_TAG[status] ?? 'info'

// ---------- 新增 / 编辑草稿 ----------

interface InboundItemForm {
  /** 表格渲染用的稳定 key，不提交给后端 */
  key: number
  orderItemId: number
  goodsId: number
  goodsCode: string | null
  goodsName: string | null
  spec: string | null
  unitName: string | null
  qty: string
  /** 本次数量上限 = 订单剩余未入库数量 */
  remainingQty: number
  price: string
  batchNo: string
}

interface InboundForm {
  id: number | null
  orderId: number | null
  orderNo: string
  supplierId: number | null
  warehouseId: number | null
  inboundDate: string
  remark: string
  version: number | null
  items: InboundItemForm[]
}

let itemKeySeed = 0
const nextItemKey = (): number => {
  itemKeySeed += 1
  return itemKeySeed
}

function createEmptyForm(): InboundForm {
  return {
    id: null,
    orderId: null,
    orderNo: '',
    supplierId: null,
    warehouseId: null,
    inboundDate: new Date().toISOString().slice(0, 10),
    remark: '',
    version: null,
    items: [],
  }
}

const form = reactive<InboundForm>(createEmptyForm())
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()

const formRules: FormRules = {
  warehouseId: [{ required: true, message: '请选择入库仓库', trigger: 'change' }],
  inboundDate: [{ required: true, message: '请选择入库日期', trigger: 'change' }],
}

/** 可入库订单候选：已审 + 部分入库（G4：一张入库单对应一张订单）。 */
const inboundableOrders = ref<PurOrder[]>([])
const orderLoading = ref(false)

const fetchInboundableOrders = async (): Promise<void> => {
  orderLoading.value = true
  try {
    const [approved, partial] = await Promise.all([
      getPurOrderList({ page: 1, pageSize: 100, status: ORDER_STATUS.APPROVED }),
      getPurOrderList({ page: 1, pageSize: 100, status: ORDER_STATUS.PARTIAL_INBOUND }),
    ])
    inboundableOrders.value = [...approved.items, ...partial.items]
  } finally {
    orderLoading.value = false
  }
}

const totalQty = computed(() => form.items.reduce((sum, item) => sum + toNum(item.qty), 0))
const totalAmount = computed(() => form.items.reduce((sum, item) => sum + lineAmount(item), 0))

/** 选择订单后按预填接口带出剩余可入库明细（单价取订单明细，不可修改）。 */
const handleOrderChange = async (orderId: number | null): Promise<void> => {
  form.items = []
  form.orderNo = ''
  if (!orderId) return
  const prefill = await getInboundFromOrder(orderId)
  form.orderNo = prefill.orderNo
  form.supplierId = prefill.supplierId
  if (!form.warehouseId) form.warehouseId = 1
  form.items = prefill.items.map((item: PurInboundFromOrderItem) => ({
    key: nextItemKey(),
    orderItemId: item.orderItemId,
    goodsId: item.goodsId,
    goodsCode: item.goodsCode,
    goodsName: item.goodsName,
    spec: item.spec,
    unitName: item.unitName,
    qty: String(item.remainingQty),
    remainingQty: toNum(item.remainingQty),
    price: String(item.price),
    batchNo: '',
  }))
  if (form.items.length === 0) {
    ElMessage.warning('该订单没有剩余可入库数量')
  }
}

const openCreate = async (): Promise<void> => {
  drawerMode.value = 'create'
  Object.assign(form, createEmptyForm())
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (warehouseOptions.value.length === 0) await fetchWarehouseOptions()
  await fetchInboundableOrders()
}

const openEdit = async (row: PurInbound): Promise<void> => {
  const detail = await getPurInboundDetail(row.id)
  drawerMode.value = 'edit'
  Object.assign(form, {
    id: detail.id,
    orderId: detail.orderId,
    orderNo: detail.orderNo ?? '',
    supplierId: detail.supplierId,
    warehouseId: detail.warehouseId,
    inboundDate: detail.inboundDate,
    remark: detail.remark ?? '',
    version: detail.version,
    items: detail.items.map((item) => ({
      key: nextItemKey(),
      orderItemId: item.orderItemId,
      goodsId: item.goodsId,
      goodsCode: item.goodsCode,
      goodsName: item.goodsName,
      spec: item.spec,
      unitName: item.unitName,
      qty: String(item.qty),
      remainingQty: Number.MAX_SAFE_INTEGER, // 编辑时后端按剩余量校验
      price: String(item.price),
      batchNo: item.batchNo ?? '',
    })),
  })
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (warehouseOptions.value.length === 0) await fetchWarehouseOptions()
}

const validateItems = (): string | null => {
  if (form.items.length === 0) return '请先选择采购订单带出明细'
  for (const [index, item] of form.items.entries()) {
    if (!(toNum(item.qty) > 0)) return `第 ${index + 1} 行数量必须大于 0`
    if (drawerMode.value === 'create' && toNum(item.qty) > item.remainingQty) {
      return `第 ${index + 1} 行数量超过剩余可入库数量 ${formatQty(item.remainingQty)}`
    }
  }
  return null
}

const handleSave = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const itemError = validateItems()
  if (itemError) {
    ElMessage.warning(itemError)
    return
  }
  submitting.value = true
  try {
    const items: PurInboundItemPayload[] = form.items.map((item) => ({
      orderItemId: item.orderItemId,
      qty: item.qty,
      ...(item.batchNo.trim() ? { batchNo: item.batchNo.trim() } : {}),
    }))
    if (drawerMode.value === 'create') {
      await createPurInbound(
        {
          orderId: Number(form.orderId),
          warehouseId: Number(form.warehouseId),
          inboundDate: form.inboundDate,
          remark: form.remark.trim() || null,
          items,
        },
        `pur-inbound-create-${Date.now()}`,
      )
      ElMessage.success('已保存为草稿')
    } else if (form.id !== null) {
      await updatePurInbound(
        form.id,
        {
          warehouseId: Number(form.warehouseId),
          inboundDate: form.inboundDate,
          remark: form.remark.trim() || null,
          items,
          version: form.version ?? undefined,
        },
        `pur-inbound-update-${form.id}-${Date.now()}`,
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

const handleConfirm = async (row: PurInbound): Promise<void> => {
  await ElMessageBox.confirm(
    `确定确认入库单「${row.inboundNo}」吗？确认后将增加库存并生成应付，确认后不可修改。`,
    '确认入库',
    { type: 'warning' },
  )
  await confirmPurInbound(row.id, `pur-inbound-confirm-${row.id}-${Date.now()}`)
  ElMessage.success('入库成功：库存已增加、订单已回写、应付已生成')
  await fetchList()
}

const handleDelete = async (row: PurInbound): Promise<void> => {
  await ElMessageBox.confirm(`确定删除草稿「${row.inboundNo}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deletePurInbound(row.id, `pur-inbound-delete-${row.id}-${Date.now()}`)
  ElMessage.success('删除成功')
  await fetchList()
}

// ---------- 详情 / 打印 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<PurInbound | null>(null)

const openDetail = async (row: PurInbound): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getPurInboundDetail(row.id)
  } finally {
    detailLoading.value = false
  }
}

/** 打印：只打印抽屉内的打印区（抬头 + 明细 + 合计 + 签字栏）。 */
const handlePrint = (): void => {
  window.print()
}

onMounted(() => {
  void Promise.all([fetchList(), fetchSupplierOptions(), fetchWarehouseOptions()])
})
</script>

<template>
  <div class="page-container inbound-page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="按状态、供应商、仓库与入库日期筛选；确认入库将增加库存并生成应付。"
        >
          <el-form class="query-form" label-width="90px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="单号 / 备注"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable style="width: 100%">
                    <el-option label="草稿" :value="INBOUND_STATUS.DRAFT" />
                    <el-option label="已确认" :value="INBOUND_STATUS.CONFIRMED" />
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
                <el-form-item label="仓库">
                  <el-select
                    v-model="query.warehouseId"
                    placeholder="全部"
                    clearable
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
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="入库日期">
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
              <el-button
                v-perm="'pur:inbound:add'"
                type="primary"
                :icon="'Plus'"
                @click="openCreate"
              >
                新增入库单
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="采购入库单列表"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="inboundNo" label="入库单号" width="180" />
            <el-table-column prop="orderNo" label="来源订单" width="180">
              <template #default="{ row }">{{ row.orderNo || '-' }}</template>
            </el-table-column>
            <el-table-column label="供应商" min-width="140">
              <template #default="{ row }">{{ row.supplierName || '-' }}</template>
            </el-table-column>
            <el-table-column label="仓库" min-width="110">
              <template #default="{ row }">{{ row.warehouseName || '-' }}</template>
            </el-table-column>
            <el-table-column label="入库日期" width="110" align="center">
              <template #default="{ row }">{{ row.inboundDate || '-' }}</template>
            </el-table-column>
            <el-table-column label="含税金额" width="130" align="right">
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
                  v-if="isInboundDraft(row.status)"
                  v-perm="'pur:inbound:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="isInboundDraft(row.status)"
                  v-perm="'pur:inbound:confirm'"
                  link
                  type="success"
                  @click="handleConfirm(row)"
                >
                  确认
                </el-button>
                <el-button
                  v-if="isInboundDraft(row.status)"
                  v-perm="'pur:inbound:edit'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无采购入库单" />
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

    <!-- 新增 / 编辑草稿：从已审/部分入库订单预填，可修改本次数量（≤ 剩余） -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新增采购入库单' : '编辑采购入库单草稿'"
      size="1000px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="采购订单">
              <el-select
                v-model="form.orderId"
                placeholder="请选择已审/部分入库订单"
                filterable
                :disabled="drawerMode === 'edit'"
                :loading="orderLoading"
                style="width: 100%"
                @change="handleOrderChange"
              >
                <el-option
                  v-for="order in inboundableOrders"
                  :key="order.id"
                  :label="`${order.orderNo}（${order.supplierName ?? '-'}）`"
                  :value="order.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="入库仓库" prop="warehouseId">
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
          <el-col :span="8">
            <el-form-item label="入库日期" prop="inboundDate">
              <el-date-picker
                v-model="form.inboundDate"
                type="date"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="供应商">
              <el-input :model-value="form.supplierId ? String(form.supplierId) : ''" disabled>
                <template v-if="form.orderNo" #append>来源订单 {{ form.orderNo }}</template>
              </el-input>
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="备注">
              <el-input v-model="form.remark" maxlength="255" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>

        <div class="item-toolbar">
          <span class="item-title">入库明细（本次数量 ≤ 剩余未入库数量）</span>
        </div>

        <el-table :data="form.items" border size="small" row-key="key">
          <el-table-column label="商品编码" width="120">
            <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
          </el-table-column>
          <el-table-column label="商品名称" min-width="160">
            <template #default="{ row }">{{ row.goodsName || '-' }}</template>
          </el-table-column>
          <el-table-column label="单位" width="70" align="center">
            <template #default="{ row }">{{ row.unitName || '-' }}</template>
          </el-table-column>
          <el-table-column label="本次数量" width="120">
            <template #default="{ row }">
              <el-input v-model="row.qty" placeholder=">0" />
            </template>
          </el-table-column>
          <el-table-column label="剩余可入库" width="110" align="right">
            <template #default="{ row }">{{ formatQty(row.remainingQty) }}</template>
          </el-table-column>
          <el-table-column label="入库单价" width="100" align="right">
            <template #default="{ row }">{{ money(row.price) }}</template>
          </el-table-column>
          <el-table-column label="行金额" width="110" align="right">
            <template #default="{ row }">{{ money(lineAmount(row)) }}</template>
          </el-table-column>
          <el-table-column label="批次号" width="130">
            <template #default="{ row }">
              <el-input v-model="row.batchNo" placeholder="可选" />
            </template>
          </el-table-column>
        </el-table>

        <div class="totals">
          <span>数量合计：<b>{{ formatQty(totalQty) }}</b></span>
          <span>金额合计（含税）：<b>¥ {{ money(totalAmount) }}</b></span>
        </div>
        <p class="form-tip">
          提示：入库单价取来源采购订单明细的含税单价，不可修改；确认后将增加库存并生成应付
          （应付到期日 = 入库日 + 供应商账期），同时回写订单累计入库数量与状态。
        </p>
      </el-form>

      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存草稿</el-button>
      </template>
    </el-drawer>

    <!-- 详情 + 打印：打印区单独包裹，@media print 只显示该区域 -->
    <el-drawer v-model="detailVisible" title="采购入库单详情" size="1000px" destroy-on-close>
      <div v-loading="detailLoading" class="print-area">
        <template v-if="detail">
          <h2 class="print-title">采购入库单</h2>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="入库单号">{{ detail.inboundNo }}</el-descriptions-item>
            <el-descriptions-item label="来源订单">{{ detail.orderNo || '-' }}</el-descriptions-item>
            <el-descriptions-item label="供应商">{{ detail.supplierName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="仓库">{{ detail.warehouseName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="入库日期">{{ detail.inboundDate || '-' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTag(detail.status)" size="small">
                {{ statusLabel(detail.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="含税金额">¥ {{ money(detail.totalAmount) }}</el-descriptions-item>
            <el-descriptions-item label="其中税额">¥ {{ money(detail.taxAmount) }}</el-descriptions-item>
            <el-descriptions-item label="单据时间">
              {{ detail.createdAt?.replace('T', ' ').slice(0, 19) || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="备注" :span="3">{{ detail.remark || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-table :data="detail.items" border size="small" class="print-table">
            <el-table-column type="index" label="序号" width="60" align="center" />
            <el-table-column label="商品编码" width="120">
              <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="商品名称" min-width="150">
              <template #default="{ row }">{{ row.goodsName || '-' }}</template>
            </el-table-column>
            <el-table-column label="规格" min-width="100">
              <template #default="{ row }">{{ row.spec || '-' }}</template>
            </el-table-column>
            <el-table-column label="单位" width="70" align="center">
              <template #default="{ row }">{{ row.unitName || '-' }}</template>
            </el-table-column>
            <el-table-column label="数量" width="90" align="right">
              <template #default="{ row }">{{ formatQty(row.qty) }}</template>
            </el-table-column>
            <el-table-column label="含税单价" width="100" align="right">
              <template #default="{ row }">{{ money(row.price) }}</template>
            </el-table-column>
            <el-table-column label="含税金额" width="110" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="批次号" width="110">
              <template #default="{ row }">{{ row.batchNo || '-' }}</template>
            </el-table-column>
          </el-table>

          <div class="print-totals">
            <span>金额合计（含税）：¥ {{ money(detail.totalAmount) }}</span>
            <span>其中税额：¥ {{ money(detail.taxAmount) }}</span>
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
.inbound-page {
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
