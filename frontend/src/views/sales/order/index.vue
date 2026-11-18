<script setup lang="ts">
/**
 * 销售订单页（SAL-02/04~09/15）。
 * 结构：查询条件 + 订单列表（含已出库进度）+ 新增/编辑草稿抽屉（含信用额度展示）+
 * 审核/关闭弹窗 + 详情/打印抽屉。
 * 与后端保持一致：
 *   - 新建即草稿；提交后「待审」；审核通过「已审」（做信用校验，超额 409），驳回回草稿；
 *   - 已审及之后只读（编辑返回 409）；关闭后剩余数量作废、不可再出库；
 *   - 明细行金额/合计/税额前端仅作展示，最终金额由服务端按含税口径重算（折扣按比例缩放税额）；
 *   - 订单本身不写库存与应收，出库进度来自已确认出库单明细汇总（SAL-09）；
 *   - 信用概览（SAL-15）：已占用 = 未结清应收 + 已审未出库订单未出库金额，额度 0 不控制；
 *   - 报价转单（SAL-02）：?quotationId=N 进入时预填客户/明细/价格，保存调用 convert 接口。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { getCustomerOptions } from '@/api/customer'
import { getGoodsList, getUnits } from '@/api/goods'
import {
  auditSalOrder,
  buildOrderFromQuotation,
  closeSalOrder,
  convertQuotation,
  createSalOrder,
  deleteSalOrder,
  getSalCreditInfo,
  getSalOrderDetail,
  getSalOrderList,
  submitSalOrder,
  updateSalOrder,
} from '@/api/salesOrder'
import BaseCard from '@/components/BaseCard.vue'
import type { Customer } from '@/types/customer'
import type { Goods, Unit } from '@/types/goods'
import type { SalCreditSummary, SalOrder } from '@/types/salesOrder'
import {
  SAL_ORDER_STATUS,
  SAL_ORDER_STATUS_LABEL,
  SAL_ORDER_STATUS_TAG,
  isSalClosable,
  isSalDraft,
  type SalOrderItemPayload,
  type SalOrderQuery,
} from '@/types/salesOrder'

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
const formatRate = (value: string | number | null | undefined): string =>
  `${(toNum(value) * 100).toFixed(2).replace(/\.00$/, '')}%`

/** 行金额：round(qty × 含税单价, 2)。 */
const lineAmount = (item: OrderItemForm): number => round2(toNum(item.qty) * toNum(item.price))
/** 行税额：含税价税分离 amount - amount/(1+rate)，与后端口径一致（展示用）。 */
const lineTax = (item: OrderItemForm): number => {
  const amount = lineAmount(item)
  const rate = toNum(item.taxRate)
  if (rate <= 0 || amount <= 0) return 0
  return round2(amount - round2(amount / (1 + rate)))
}

// ---------- 列表与筛选 ----------

const loading = ref(false)
const rows = ref<SalOrder[]>([])
const total = ref(0)
const query = reactive<SalOrderQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  customerId: null,
  orderDateStart: null,
  orderDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)

/** 客户下拉：只取启用客户（后端 options 接口已过滤）。 */
const customerOptions = ref<Customer[]>([])
/** 商品候选：明细行只允许选启用商品。 */
const goodsOptions = ref<Goods[]>([])
/** 计量单位候选：选商品后自动回填默认单位，仍可手工改。 */
const unitOptions = ref<Unit[]>([])

const fetchCustomerOptions = async (): Promise<void> => {
  customerOptions.value = await getCustomerOptions()
}

const fetchGoodsOptions = async (): Promise<void> => {
  const pageSize = 100
  const options: Goods[] = []
  for (let page = 1; page <= 100; page += 1) {
    const result = await getGoodsList({ page, pageSize, status: 1 })
    options.push(...result.items)
    if (result.items.length < pageSize || options.length >= result.total) break
  }
  goodsOptions.value = options
}

const fetchUnitOptions = async (): Promise<void> => {
  unitOptions.value = await getUnits()
}

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.orderDateStart = dateRange.value?.[0] ?? null
    query.orderDateEnd = dateRange.value?.[1] ?? null
    const result = await getSalOrderList(query)
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
  query.customerId = null
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

const statusLabel = (status: number): string => SAL_ORDER_STATUS_LABEL[status] ?? '未知'
const statusTag = (status: number): 'info' | 'warning' | 'success' | 'danger' | 'primary' =>
  SAL_ORDER_STATUS_TAG[status] ?? 'info'

// ---------- 新增 / 编辑草稿 ----------

interface OrderItemForm {
  /** 表格渲染用的稳定 key，不提交给后端 */
  key: number
  goodsId: number | null
  unitId: number | null
  qty: string
  price: string
  taxRate: string
}

interface OrderForm {
  id: number | null
  customerId: number | null
  orderDate: string
  /** 整单折扣金额（非折扣率），保存时提交给后端重算 */
  discount: string
  remark: string
  /** 乐观锁版本号：编辑保存时回传，后端据此判定并发冲突 */
  version: number | null
  items: OrderItemForm[]
}

let itemKeySeed = 0
const nextItemKey = (): number => {
  itemKeySeed += 1
  return itemKeySeed
}

function createEmptyItem(): OrderItemForm {
  return {
    key: nextItemKey(),
    goodsId: null,
    unitId: null,
    qty: '1',
    price: '0',
    taxRate: '0',
  }
}

function createEmptyForm(): OrderForm {
  return {
    id: null,
    customerId: null,
    orderDate: new Date().toISOString().slice(0, 10),
    discount: '0',
    remark: '',
    version: null,
    items: [createEmptyItem()],
  }
}

// 必须放在 nextItemKey / createEmptyItem 之后：createEmptyForm() 会同步调用它们，
// 提前定义会命中 let/const 的暂时性死区（TDZ）导致页面白屏。
const form = reactive<OrderForm>(createEmptyForm())
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()

const formRules: FormRules = {
  customerId: [{ required: true, message: '请选择客户', trigger: 'change' }],
  orderDate: [{ required: true, message: '请选择下单日期', trigger: 'change' }],
}

const totalQty = computed(() => form.items.reduce((sum, item) => sum + toNum(item.qty), 0))
const totalAmount = computed(() =>
  form.items.reduce((sum, item) => sum + lineAmount(item), 0),
)
const totalTax = computed(() => form.items.reduce((sum, item) => sum + lineTax(item), 0))
/** 折扣后合计（展示用；最终以后端重算为准） */
const payableAmount = computed(() => Math.max(0, totalAmount.value - toNum(form.discount)))

// ---------- SAL-15 信用额度展示 ----------

const credit = ref<SalCreditSummary | null>(null)
const creditLoading = ref(false)

const fetchCredit = async (customerId: number | null): Promise<void> => {
  credit.value = null
  if (customerId === null) return
  creditLoading.value = true
  try {
    credit.value = await getSalCreditInfo(customerId)
  } finally {
    creditLoading.value = false
  }
}

watch(
  () => form.customerId,
  (value) => {
    void fetchCredit(value)
  },
)

const creditUnlimited = computed(
  () => credit.value !== null && toNum(credit.value.creditLimit) <= 0,
)
/** 超额预警：占用 + 当前表单金额 > 额度（仅提示，硬校验在服务端审核动作） */
const creditOver = computed(() => {
  if (credit.value === null || creditUnlimited.value) return false
  return toNum(credit.value.occupiedAmount) + payableAmount.value > toNum(credit.value.creditLimit)
})

// ---------- SAL-02 报价转单 ----------

const route = useRoute()
/** 来源报价单：转单模式下保存走 convert 接口（明细价格以后端复制为准） */
const sourceQuotation = ref<{ id: number; quoteNo: string } | null>(null)

const loadFromQuotation = async (quotationId: number): Promise<void> => {
  const prefill = await buildOrderFromQuotation(quotationId)
  drawerMode.value = 'create'
  Object.assign(form, {
    id: null,
    customerId: prefill.customerId,
    orderDate: prefill.orderDate,
    discount: '0',
    remark: prefill.remark ?? '',
    version: null,
    items: prefill.items.map((item) => ({
      key: nextItemKey(),
      goodsId: item.goodsId,
      unitId: item.unitId,
      qty: String(item.qty),
      price: String(item.price),
      taxRate: String(item.taxRate),
    })),
  })
  if (form.items.length === 0) form.items.push(createEmptyItem())
  sourceQuotation.value = { id: prefill.sourceQuotationId, quoteNo: prefill.quoteNo }
  drawerVisible.value = true
  formRef.value?.clearValidate()
}

const openCreate = async (): Promise<void> => {
  drawerMode.value = 'create'
  sourceQuotation.value = null
  Object.assign(form, createEmptyForm())
  if (customerOptions.value.length > 0) form.customerId = customerOptions.value[0].id
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (goodsOptions.value.length === 0) await fetchGoodsOptions()
  if (unitOptions.value.length === 0) await fetchUnitOptions()
}

const openEdit = async (row: SalOrder): Promise<void> => {
  const detail = await getSalOrderDetail(row.id)
  drawerMode.value = 'edit'
  sourceQuotation.value = null
  Object.assign(form, {
    id: detail.id,
    customerId: detail.customerId,
    orderDate: detail.orderDate,
    discount: String(detail.discount),
    remark: detail.remark ?? '',
    version: detail.version,
    items: detail.items.map((item) => ({
      key: nextItemKey(),
      goodsId: item.goodsId,
      unitId: item.unitId,
      qty: String(item.qty),
      price: String(item.price),
      taxRate: String(item.taxRate),
    })),
  })
  if (form.items.length === 0) form.items.push(createEmptyItem())
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (goodsOptions.value.length === 0) await fetchGoodsOptions()
  if (unitOptions.value.length === 0) await fetchUnitOptions()
}

const handleAddItem = (): void => {
  form.items.push(createEmptyItem())
}
const handleRemoveItem = (index: number): void => {
  if (form.items.length <= 1) {
    ElMessage.warning('至少保留一行明细')
    return
  }
  form.items.splice(index, 1)
}

/** 选择商品后回填默认单位与售价快照，销售员可再修改。 */
const handleGoodsChange = (item: OrderItemForm): void => {
  const goods = goodsOptions.value.find((option) => option.id === item.goodsId)
  if (!goods) return
  if (goods.unitId) item.unitId = goods.unitId
  item.price = String(goods.salePrice ?? '0')
}

const validateItems = (): string | null => {
  if (form.items.length === 0) return '至少需要一行明细'
  const used = new Set<number>()
  for (const [index, item] of form.items.entries()) {
    if (item.goodsId === null) return `第 ${index + 1} 行未选择商品`
    if (used.has(item.goodsId)) return '同一商品不能重复录入多行，请合并数量'
    used.add(item.goodsId)
    if (item.unitId === null) return `第 ${index + 1} 行未选择计量单位`
    if (!(toNum(item.qty) > 0)) return `第 ${index + 1} 行数量必须大于 0`
    if (item.price.trim() === '' || !(toNum(item.price) >= 0)) {
      return `第 ${index + 1} 行含税单价必须 ≥ 0`
    }
    const rate = toNum(item.taxRate)
    if (!(rate >= 0 && rate <= 1)) return `第 ${index + 1} 行税率必须在 0～1 之间（0.13 表示 13%）`
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
    const items: SalOrderItemPayload[] = form.items.map((item) => ({
      goodsId: Number(item.goodsId),
      unitId: Number(item.unitId),
      qty: item.qty,
      price: item.price,
      taxRate: item.taxRate || '0',
    }))
    if (sourceQuotation.value !== null) {
      // SAL-02 报价转单：明细价格以后端从报价复制为准，前端只提交日期/折扣/备注
      await convertQuotation(
        sourceQuotation.value.id,
        { orderDate: form.orderDate, discount: form.discount || '0', remark: form.remark.trim() || null },
        `sal-order-convert-${sourceQuotation.value.id}-${Date.now()}`,
      )
      ElMessage.success('已由报价生成订单草稿，报价单已标记为「已转订单」')
    } else if (drawerMode.value === 'create') {
      await createSalOrder(
        {
          customerId: Number(form.customerId),
          orderDate: form.orderDate,
          discount: form.discount || '0',
          remark: form.remark.trim() || null,
          items,
        },
        `sal-order-create-${Date.now()}`,
      )
      ElMessage.success('已保存为草稿')
    } else if (form.id !== null) {
      await updateSalOrder(
        form.id,
        {
          customerId: Number(form.customerId),
          orderDate: form.orderDate,
          discount: form.discount || '0',
          remark: form.remark.trim() || null,
          items,
          version: form.version ?? undefined,
        },
        `sal-order-update-${form.id}-${Date.now()}`,
      )
      ElMessage.success('草稿已更新')
    }
    drawerVisible.value = false
    sourceQuotation.value = null
    await fetchList()
  } finally {
    submitting.value = false
  }
}

// ---------- 提交 / 删除 / 审核 / 关闭 ----------

const handleSubmitOrder = async (row: SalOrder): Promise<void> => {
  await ElMessageBox.confirm(
    `确定提交订单「${row.orderNo}」吗？提交后不可再编辑。`,
    '提交确认',
    { type: 'warning' },
  )
  await submitSalOrder(row.id, `sal-order-submit-${row.id}-${Date.now()}`)
  ElMessage.success('已提交，等待审核')
  await fetchList()
}

const handleDelete = async (row: SalOrder): Promise<void> => {
  await ElMessageBox.confirm(`确定删除草稿「${row.orderNo}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteSalOrder(row.id, `sal-order-delete-${row.id}-${Date.now()}`)
  ElMessage.success('删除成功')
  await fetchList()
}

const auditVisible = ref(false)
const auditSubmitting = ref(false)
const auditTarget = ref<SalOrder | null>(null)
const auditForm = reactive<{ result: 'approved' | 'rejected'; remark: string }>({
  result: 'approved',
  remark: '',
})

const openAudit = (row: SalOrder): void => {
  auditTarget.value = row
  auditForm.result = 'approved'
  auditForm.remark = ''
  auditVisible.value = true
}

const handleAudit = async (): Promise<void> => {
  if (!auditTarget.value) return
  if (auditForm.result === 'rejected' && !auditForm.remark.trim()) {
    ElMessage.warning('驳回必须填写驳回原因')
    return
  }
  auditSubmitting.value = true
  try {
    await auditSalOrder(
      auditTarget.value.id,
      {
        result: auditForm.result,
        remark: auditForm.remark.trim() || null,
        version: auditTarget.value.version,
      },
      `sal-order-audit-${auditTarget.value.id}-${Date.now()}`,
    )
    ElMessage.success(auditForm.result === 'approved' ? '审核通过' : '已驳回，单据回到草稿')
    auditVisible.value = false
    await fetchList()
  } finally {
    auditSubmitting.value = false
  }
}

const closeVisible = ref(false)
const closeSubmitting = ref(false)
const closeTarget = ref<SalOrder | null>(null)
const closeReason = ref('')

const openClose = (row: SalOrder): void => {
  closeTarget.value = row
  closeReason.value = ''
  closeVisible.value = true
}

const handleClose = async (): Promise<void> => {
  if (!closeTarget.value) return
  if (!closeReason.value.trim()) {
    ElMessage.warning('请填写关闭原因')
    return
  }
  closeSubmitting.value = true
  try {
    await closeSalOrder(
      closeTarget.value.id,
      { reason: closeReason.value.trim(), version: closeTarget.value.version },
      `sal-order-close-${closeTarget.value.id}-${Date.now()}`,
    )
    ElMessage.success('订单已关闭，剩余未出库数量作废')
    closeVisible.value = false
    await fetchList()
  } finally {
    closeSubmitting.value = false
  }
}

// ---------- 详情 / 打印 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<SalOrder | null>(null)

const openDetail = async (row: SalOrder): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getSalOrderDetail(row.id)
  } finally {
    detailLoading.value = false
  }
}

/** 打印：只打印抽屉内的打印区（抬头 + 明细 + 合计 + 签字栏）。 */
const handlePrint = (): void => {
  window.print()
}

onMounted(async () => {
  await Promise.all([fetchList(), fetchCustomerOptions(), fetchGoodsOptions(), fetchUnitOptions()])
  // SAL-02：从报价页跳转过来（?quotationId=N）时自动打开预填抽屉
  const quotationId = Number(route.query.quotationId)
  if (Number.isFinite(quotationId) && quotationId > 0) {
    try {
      await loadFromQuotation(quotationId)
    } catch {
      ElMessage.warning('报价转单预填失败，请手工新建订单')
    }
  }
})
</script>

<template>
  <div class="page-container order-page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="按状态、客户与下单日期筛选；列表显示累计出库进度，数据范围由角色决定（本人/本部门/全部）。"
        >
          <el-form class="query-form" label-width="90px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="单号 / 客户名称"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable style="width: 100%">
                    <el-option label="草稿" :value="SAL_ORDER_STATUS.DRAFT" />
                    <el-option label="待审" :value="SAL_ORDER_STATUS.PENDING" />
                    <el-option label="已审" :value="SAL_ORDER_STATUS.APPROVED" />
                    <el-option label="部分出库" :value="SAL_ORDER_STATUS.PARTIAL_OUT" />
                    <el-option label="完成" :value="SAL_ORDER_STATUS.COMPLETED" />
                    <el-option label="关闭" :value="SAL_ORDER_STATUS.CLOSED" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="客户">
                  <el-select
                    v-model="query.customerId"
                    placeholder="全部"
                    clearable
                    filterable
                    style="width: 100%"
                  >
                    <el-option
                      v-for="customer in customerOptions"
                      :key="customer.id"
                      :label="`${customer.code} ${customer.name}`"
                      :value="customer.id"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="下单日期">
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
              <el-button v-perm="'sal:order:add'" type="primary" :icon="'Plus'" @click="openCreate">
                新增订单
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="销售订单列表"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="orderNo" label="订单号" width="180" />
            <el-table-column label="客户" min-width="150">
              <template #default="{ row }">{{ row.customerName || '-' }}</template>
            </el-table-column>
            <el-table-column label="下单日期" width="110" align="center">
              <template #default="{ row }">{{ row.orderDate || '-' }}</template>
            </el-table-column>
            <el-table-column label="折扣" width="100" align="right">
              <template #default="{ row }">{{ money(row.discount) }}</template>
            </el-table-column>
            <el-table-column label="含税金额" width="120" align="right">
              <template #default="{ row }">¥ {{ money(row.totalAmount) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="已出库进度" width="190">
              <template #default="{ row }">
                <div class="progress-cell">
                  <el-progress
                    :percentage="Math.min(100, Math.max(0, Number(row.outboundProgress)))"
                    :stroke-width="8"
                    :show-text="false"
                  />
                  <span class="progress-text">
                    {{ formatQty(row.outboundQty) }} / {{ formatQty(row.totalQty) }}
                    （{{ Number(row.outboundProgress).toFixed(2) }}%）
                  </span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDetail(row)">详情</el-button>
                <el-button
                  v-if="isSalDraft(row.status)"
                  v-perm="'sal:order:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="isSalDraft(row.status)"
                  v-perm="'sal:order:submit'"
                  link
                  type="success"
                  @click="handleSubmitOrder(row)"
                >
                  提交
                </el-button>
                <el-button
                  v-if="row.status === SAL_ORDER_STATUS.PENDING"
                  v-perm="'sal:order:audit'"
                  link
                  type="warning"
                  @click="openAudit(row)"
                >
                  审核
                </el-button>
                <el-button
                  v-if="isSalClosable(row.status)"
                  v-perm="'sal:order:close'"
                  link
                  type="danger"
                  @click="openClose(row)"
                >
                  关闭
                </el-button>
                <el-button
                  v-if="isSalDraft(row.status)"
                  v-perm="'sal:order:edit'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无销售订单" />
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

    <!-- 新增 / 编辑草稿：客户、日期、折扣、明细行（商品/单位/数量/含税单价/税率）+ 信用额度展示 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新增销售订单' : '编辑销售订单草稿'"
      size="1080px"
      destroy-on-close
    >
      <el-alert
        v-if="sourceQuotation"
        class="convert-alert"
        type="info"
        :closable="false"
        show-icon
        :title="`来源报价单：${sourceQuotation.quoteNo}`"
        description="保存后将调用「报价转订单」，明细与价格以后端从报价单复制为准；报价单会标记为「已转订单」，不可重复转单。"
      />

      <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="客户" prop="customerId">
              <el-select
                v-model="form.customerId"
                placeholder="请选择客户"
                filterable
                style="width: 100%"
              >
                <el-option
                  v-for="customer in customerOptions"
                  :key="customer.id"
                  :label="`${customer.code} ${customer.name}`"
                  :value="customer.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="下单日期" prop="orderDate">
              <el-date-picker
                v-model="form.orderDate"
                type="date"
                value-format="YYYY-MM-DD"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="整单折扣">
              <el-input v-model="form.discount" placeholder="折扣金额，默认 0" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remark" maxlength="255" placeholder="可选" />
            </el-form-item>
          </el-col>
        </el-row>

        <!-- SAL-15：信用额度概览（占用 = 未结清应收 + 已审未出库订单未出库金额） -->
        <el-alert
          v-if="credit"
          class="credit-alert"
          :type="creditOver ? 'error' : 'info'"
          :closable="false"
          show-icon
          :title="
            creditUnlimited
              ? '该客户未启用信用控制（额度 0）'
              : `信用额度 ¥ ${money(credit.creditLimit)} ｜ 已占用 ¥ ${money(credit.occupiedAmount)} ｜ 可用 ¥ ${money(credit.availableAmount ?? 0)}`
          "
          :description="
            creditOver
              ? '已占用 + 本单金额超过信用额度，保存可以草稿保留，但审核通过会被服务端拒绝（409）。'
              : undefined
          "
        />

        <div class="item-toolbar">
          <span class="item-title">订单明细</span>
          <el-button type="primary" link :icon="'Plus'" @click="handleAddItem">添加一行</el-button>
        </div>

        <el-table :data="form.items" border size="small" row-key="key">
          <el-table-column label="商品" min-width="220">
            <template #default="{ row }">
              <el-select
                v-model="row.goodsId"
                placeholder="请选择商品"
                filterable
                style="width: 100%"
                @change="handleGoodsChange(row)"
              >
                <el-option
                  v-for="goods in goodsOptions"
                  :key="goods.id"
                  :label="`${goods.code} ${goods.name}`"
                  :value="goods.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="单位" width="120">
            <template #default="{ row }">
              <el-select v-model="row.unitId" placeholder="单位" filterable style="width: 100%">
                <el-option
                  v-for="unit in unitOptions"
                  :key="unit.id"
                  :label="unit.name"
                  :value="unit.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="110">
            <template #default="{ row }">
              <el-input v-model="row.qty" placeholder=">0" />
            </template>
          </el-table-column>
          <el-table-column label="含税单价" width="120">
            <template #default="{ row }">
              <el-input v-model="row.price" placeholder="≥0" />
            </template>
          </el-table-column>
          <el-table-column label="税率" width="100">
            <template #default="{ row }">
              <el-input v-model="row.taxRate" placeholder="0~1" />
            </template>
          </el-table-column>
          <el-table-column label="行金额" width="120" align="right">
            <template #default="{ row }">{{ money(lineAmount(row)) }}</template>
          </el-table-column>
          <el-table-column label="其中税额" width="110" align="right">
            <template #default="{ row }">{{ money(lineTax(row)) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80" align="center">
            <template #default="{ $index }">
              <el-button link type="danger" @click="handleRemoveItem($index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="totals">
          <span>数量合计：<b>{{ formatQty(totalQty) }}</b></span>
          <span>金额合计（含税）：<b>¥ {{ money(totalAmount) }}</b></span>
          <span>整单折扣：<b>¥ {{ money(form.discount) }}</b></span>
          <span>折后合计：<b>¥ {{ money(payableAmount) }}</b></span>
          <span>其中税额：<b>¥ {{ money(totalTax) }}</b></span>
        </div>
        <p class="form-tip">
          提示：单价为含税单价，行金额 = 数量 × 含税单价；整单折扣为金额（非折扣率），
          折后合计 = 行金额合计 - 折扣，税额按折扣比例同口径缩放。最终金额由服务端重算并以此为准。
        </p>
      </el-form>

      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">
          {{ sourceQuotation ? '转单并保存草稿' : '保存草稿' }}
        </el-button>
      </template>
    </el-drawer>

    <!-- 审核：通过（服务端做信用校验）/ 驳回（必须填写原因） -->
    <el-dialog v-model="auditVisible" title="审核销售订单" width="520px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="订单号">
          <span>{{ auditTarget?.orderNo }}</span>
        </el-form-item>
        <el-form-item label="审核结论">
          <el-radio-group v-model="auditForm.result">
            <el-radio label="approved">通过</el-radio>
            <el-radio label="rejected">驳回</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="auditForm.result === 'rejected' ? '驳回原因' : '审核意见'">
          <el-input
            v-model="auditForm.remark"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            :placeholder="auditForm.result === 'rejected' ? '必填：说明驳回原因' : '可选'"
          />
        </el-form-item>
        <p v-if="auditForm.result === 'approved'" class="form-tip">
          审核通过将校验客户信用额度（占用 = 未结清应收 + 已审未出库订单），超额会被拒绝。
        </p>
      </el-form>
      <template #footer>
        <el-button @click="auditVisible = false">取消</el-button>
        <el-button type="primary" :loading="auditSubmitting" @click="handleAudit">确定</el-button>
      </template>
    </el-dialog>

    <!-- 关闭：剩余未出库数量作废，必须填写原因 -->
    <el-dialog v-model="closeVisible" title="关闭销售订单" width="520px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="订单号">
          <span>{{ closeTarget?.orderNo }}</span>
        </el-form-item>
        <el-form-item label="出库进度">
          <span>
            已出库 {{ formatQty(closeTarget?.outboundQty) }} / 订购
            {{ formatQty(closeTarget?.totalQty) }}，剩余数量将作废且不可再出库
          </span>
        </el-form-item>
        <el-form-item label="关闭原因">
          <el-input
            v-model="closeReason"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            placeholder="必填：说明关闭原因"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="closeVisible = false">取消</el-button>
        <el-button type="danger" :loading="closeSubmitting" @click="handleClose">确认关闭</el-button>
      </template>
    </el-dialog>

    <!-- 详情 + 打印：打印区单独包裹，@media print 只显示该区域 -->
    <el-drawer v-model="detailVisible" title="销售订单详情" size="1000px" destroy-on-close>
      <div v-loading="detailLoading" class="print-area">
        <template v-if="detail">
          <h2 class="print-title">销售订单</h2>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="订单号">{{ detail.orderNo }}</el-descriptions-item>
            <el-descriptions-item label="客户">{{ detail.customerName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="下单日期">{{ detail.orderDate || '-' }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTag(detail.status)" size="small">
                {{ statusLabel(detail.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="整单折扣">¥ {{ money(detail.discount) }}</el-descriptions-item>
            <el-descriptions-item label="折后含税金额">¥ {{ money(detail.totalAmount) }}</el-descriptions-item>
            <el-descriptions-item label="其中税额">¥ {{ money(detail.taxAmount) }}</el-descriptions-item>
            <el-descriptions-item label="审核人">{{ detail.auditByName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="审核日期">{{ detail.auditAt || '-' }}</el-descriptions-item>
            <el-descriptions-item label="已出库进度">
              {{ formatQty(detail.outboundQty) }} / {{ formatQty(detail.totalQty) }}
              （{{ Number(detail.outboundProgress).toFixed(2) }}%）
            </el-descriptions-item>
            <el-descriptions-item label="单据时间">
              {{ detail.createdAt?.replace('T', ' ').slice(0, 19) || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="版本号">{{ detail.version }}</el-descriptions-item>
            <el-descriptions-item label="审核/驳回/关闭原因" :span="3">
              {{ detail.auditRemark || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="备注" :span="3">{{ detail.remark || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-table :data="detail.items" border size="small" class="print-table">
            <el-table-column type="index" label="序号" width="60" align="center" />
            <el-table-column label="商品编码" width="130">
              <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="商品名称" min-width="150">
              <template #default="{ row }">{{ row.goodsName || '-' }}</template>
            </el-table-column>
            <el-table-column label="规格" min-width="110">
              <template #default="{ row }">{{ row.spec || '-' }}</template>
            </el-table-column>
            <el-table-column label="单位" width="80" align="center">
              <template #default="{ row }">{{ row.unitName || '-' }}</template>
            </el-table-column>
            <el-table-column label="数量" width="90" align="right">
              <template #default="{ row }">{{ formatQty(row.qty) }}</template>
            </el-table-column>
            <el-table-column label="含税单价" width="100" align="right">
              <template #default="{ row }">{{ money(row.price) }}</template>
            </el-table-column>
            <el-table-column label="税率" width="80" align="right">
              <template #default="{ row }">{{ formatRate(row.taxRate) }}</template>
            </el-table-column>
            <el-table-column label="含税金额" width="110" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="已出库" width="90" align="right">
              <template #default="{ row }">{{ formatQty(row.outboundQty) }}</template>
            </el-table-column>
            <el-table-column label="剩余" width="90" align="right">
              <template #default="{ row }">{{ formatQty(row.remainingQty) }}</template>
            </el-table-column>
          </el-table>

          <div class="print-totals">
            <span>数量合计：{{ formatQty(detail.totalQty) }}</span>
            <span>金额合计（折后含税）：¥ {{ money(detail.totalAmount) }}</span>
            <span>其中税额：¥ {{ money(detail.taxAmount) }}</span>
            <span>累计出库：{{ formatQty(detail.outboundQty) }}</span>
          </div>

          <div class="print-footer">
            <span>制单人签字：______________</span>
            <span>审核人签字：______________</span>
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
.order-page {
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

.progress-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.progress-text {
  font-size: 12px;
  color: #606266;
}

.convert-alert {
  margin-bottom: 12px;
}

.credit-alert {
  margin-bottom: 12px;
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
