<script setup lang="ts">
/**
 * 付款管理页（F-04）：付款单 CRUD + 付款核销。
 * 数据闭环：采购入库确认生成应付（P-03）→ 新建付款单（不动应付）→ 核销冲减对应应付；
 * 核销进度（待核销/部分核销/已核销）由后端按已核销金额派生，本页只展示不计算；
 * 编辑/删除仅未核销可用，核销弹窗取该供应商未结清应付（unsettled=true）分配金额。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { getPayableList } from '@/api/payable'
import {
  createPayment,
  deletePayment,
  getPaymentDetail,
  getPaymentList,
  getPaymentSummary,
  updatePayment,
  writeoffPayment,
} from '@/api/payment'
import { getSupplierOptions } from '@/api/supplier'
import { useDict } from '@/composables/useDict'
import type { Supplier } from '@/types/supplier'
import type { Payable } from '@/types/payable'
import type { Payment, PaymentDetail, PaymentQuery, PaymentSummary } from '@/types/payment'
import { PAYMENT_STATUS, PAYMENT_STATUS_TAG } from '@/types/payment'

const toNum = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}
const money = (value: string | number | null | undefined): string => toNum(value).toFixed(2)

// 结算方式字典（settlement_type）：筛选列展示与表单下拉共用
const { options: methodOptions, labelOf: methodLabelOf } = useDict('settlement_type')

const loading = ref(false)
const rows = ref<Payment[]>([])
const total = ref(0)
const query = reactive<PaymentQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  supplierId: null,
  status: null,
  paymentDateStart: null,
  paymentDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)
const supplierOptions = ref<Supplier[]>([])

const summary = ref<PaymentSummary>({
  totalAmount: 0,
  totalAllocated: 0,
  totalUnallocated: 0,
  count: 0,
})

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.paymentDateStart = dateRange.value?.[0] ?? null
    query.paymentDateEnd = dateRange.value?.[1] ?? null
    const [list, totals] = await Promise.all([getPaymentList(query), getPaymentSummary(query)])
    rows.value = list.items
    total.value = list.total
    summary.value = totals
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
  query.supplierId = null
  query.status = null
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

const statusLabel = (status: number): string => PAYMENT_STATUS[status] ?? '未知'
const statusTag = (status: number): 'info' | 'warning' | 'success' =>
  PAYMENT_STATUS_TAG[status] ?? 'info'

// ---------- 新增 / 编辑付款单 ----------

interface PaymentForm {
  id: number | null
  supplierId: number | null
  paymentDate: string
  amount: number | null
  method: string | null
  account: string
  remark: string
  version: number | null
}

function createEmptyForm(): PaymentForm {
  return {
    id: null,
    supplierId: null,
    paymentDate: new Date().toISOString().slice(0, 10),
    amount: null,
    method: null,
    account: '',
    remark: '',
    version: null,
  }
}

const form = reactive<PaymentForm>(createEmptyForm())
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()

const formRules: FormRules = {
  supplierId: [{ required: true, message: '请选择供应商', trigger: 'change' }],
  paymentDate: [{ required: true, message: '请选择付款日期', trigger: 'change' }],
  amount: [{ required: true, message: '请输入付款金额', trigger: 'blur' }],
}

const openCreate = (): void => {
  drawerMode.value = 'create'
  Object.assign(form, createEmptyForm())
  drawerVisible.value = true
  formRef.value?.clearValidate()
}

const openEdit = async (row: Payment): Promise<void> => {
  const detail = await getPaymentDetail(row.id)
  drawerMode.value = 'edit'
  Object.assign(form, {
    id: detail.id,
    supplierId: detail.supplierId,
    paymentDate: detail.paymentDate,
    amount: toNum(detail.amount),
    method: detail.method,
    account: detail.account ?? '',
    remark: detail.remark ?? '',
    version: detail.version,
  })
  drawerVisible.value = true
  formRef.value?.clearValidate()
}

const handleSavePayment = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (form.supplierId === null || form.amount === null || toNum(form.amount) <= 0) {
    ElMessage.warning('请填写供应商与大于 0 的付款金额')
    return
  }
  submitting.value = true
  try {
    const payload = {
      supplierId: Number(form.supplierId),
      paymentDate: form.paymentDate,
      amount: toNum(form.amount).toFixed(2),
      method: form.method,
      account: form.account.trim() || null,
      remark: form.remark.trim() || null,
    }
    if (drawerMode.value === 'create') {
      await createPayment(payload, `payment-create-${Date.now()}`)
      ElMessage.success('付款单已保存（待核销）')
    } else if (form.id !== null) {
      await updatePayment(
        form.id,
        { ...payload, version: form.version ?? undefined },
        `payment-update-${form.id}-${Date.now()}`,
      )
      ElMessage.success('付款单已更新')
    }
    drawerVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

// ---------- 删除 ----------

const handleDelete = async (row: Payment): Promise<void> => {
  await ElMessageBox.confirm(
    `确定删除付款单「${row.paymentNo}」吗？仅未核销的付款单可删除。`,
    '删除付款单',
    { type: 'warning' },
  )
  await deletePayment(row.id, `payment-delete-${row.id}-${Date.now()}`)
  ElMessage.success('付款单已删除')
  await fetchList()
}

// ---------- 付款核销 ----------

/** 核销弹窗行：应付 + 本次分配金额（el-input-number 限额为该应付未付） */
interface WriteoffRow {
  payableId: number
  sourceNo: string
  billDate: string
  dueDate: string | null
  unpaidAmount: number
  writeoffAmount: number | null
}

const writeoffVisible = ref(false)
const writeoffLoading = ref(false)
const writeoffSubmitting = ref(false)
const writeoffTarget = ref<Payment | null>(null)
const writeoffRows = ref<WriteoffRow[]>([])

const remainingUnallocated = computed(() => toNum(writeoffTarget.value?.unallocatedAmount ?? 0))
const totalWriteoff = computed(() =>
  writeoffRows.value.reduce((sum, item) => sum + toNum(item.writeoffAmount), 0),
)
/** 剩余可分配（负数表示已超额，提交前拦截） */
const writeoffRemain = computed(() => remainingUnallocated.value - totalWriteoff.value)

const openWriteoff = async (row: Payment): Promise<void> => {
  writeoffTarget.value = row
  writeoffRows.value = []
  writeoffVisible.value = true
  writeoffLoading.value = true
  try {
    const payables = await getPayableList({
      page: 1,
      pageSize: 100,
      keyword: null,
      supplierId: row.supplierId,
      status: null,
      sourceNo: null,
      overdue: null,
      unsettled: true,
      billDateStart: null,
      billDateEnd: null,
    })
    writeoffRows.value = payables.items.map((item: Payable) => ({
      payableId: item.id,
      sourceNo: item.sourceNo,
      billDate: item.billDate,
      dueDate: item.dueDate,
      unpaidAmount: toNum(item.unpaidAmount),
      writeoffAmount: null,
    }))
    if (writeoffRows.value.length === 0) {
      ElMessage.warning('该供应商没有未结清应付可核销')
    }
  } finally {
    writeoffLoading.value = false
  }
}

const submitWriteoff = async (): Promise<void> => {
  const target = writeoffTarget.value
  if (!target) return
  const items = writeoffRows.value
    .filter((item) => toNum(item.writeoffAmount) > 0)
    .map((item) => ({
      payableId: item.payableId,
      writeoffAmount: toNum(item.writeoffAmount).toFixed(2),
    }))
  if (items.length === 0) {
    ElMessage.warning('请至少为一条应付填写核销金额')
    return
  }
  if (writeoffRemain.value < 0) {
    ElMessage.warning('核销总额超过付款单未分配余额，请调整金额')
    return
  }
  writeoffSubmitting.value = true
  try {
    const updated = await writeoffPayment(
      target.id,
      { items },
      `payment-writeoff-${target.id}-${Date.now()}`,
    )
    ElMessage.success(
      `核销成功：已核销 ¥${money(updated.allocatedAmount)}，未分配 ¥${money(updated.unallocatedAmount)}`,
    )
    writeoffVisible.value = false
    await fetchList()
  } finally {
    writeoffSubmitting.value = false
  }
}

// ---------- 详情 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<PaymentDetail | null>(null)

const openDetail = async (id: number): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await getPaymentDetail(id)
  } catch (error) {
    console.error('加载付款详情失败:', error)
    ElMessage.error('加载付款详情失败')
  } finally {
    detailLoading.value = false
  }
}

onMounted(async () => {
  try {
    supplierOptions.value = await getSupplierOptions()
  } catch (error) {
    console.error('加载供应商选项失败:', error)
  }
  await fetchList()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">付款管理</h1>
        <p class="page-subtitle">
          付款单先登记后核销：核销把付款金额分配到该供应商的未结清应付，可分次核销直到用完。
        </p>
      </div>

      <!-- 汇总卡片 -->
      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">付款总额</div>
          <div class="summary-value">¥{{ money(summary.totalAmount) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">已核销</div>
          <div class="summary-value paid">¥{{ money(summary.totalAllocated) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">未分配</div>
          <div class="summary-value unpaid">¥{{ money(summary.totalUnallocated) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">付款笔数</div>
          <div class="summary-value">{{ summary.count }}</div>
        </div>
      </div>

      <!-- 筛选 -->
      <el-form class="filter-form" :inline="true" label-width="72px">
        <el-form-item label="关键字">
          <el-input
            v-model="query.keyword"
            placeholder="付款单号/备注"
            clearable
            style="width: 180px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="供应商">
          <el-select
            v-model="query.supplierId"
            placeholder="全部"
            clearable
            filterable
            style="width: 180px"
          >
            <el-option
              v-for="item in supplierOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="核销进度">
          <el-select v-model="query.status" placeholder="全部" clearable style="width: 130px">
            <el-option label="待核销" :value="0" />
            <el-option label="部分核销" :value="1" />
            <el-option label="已核销" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item label="付款日期">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item>
          <el-button v-perm="'fin:payment:add'" type="primary" @click="openCreate">
            新增付款
          </el-button>
          <el-button type="primary" plain @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="paymentNo" label="付款单号" min-width="150" show-overflow-tooltip />
        <el-table-column prop="supplierName" label="供应商" min-width="140" show-overflow-tooltip />
        <el-table-column prop="paymentDate" label="付款日期" width="110" />
        <el-table-column label="付款金额" min-width="110" align="right">
          <template #default="{ row }">¥{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已核销" min-width="110" align="right">
          <template #default="{ row }">¥{{ money(row.allocatedAmount) }}</template>
        </el-table-column>
        <el-table-column label="未分配" min-width="110" align="right">
          <template #default="{ row }">¥{{ money(row.unallocatedAmount) }}</template>
        </el-table-column>
        <el-table-column label="结算方式" min-width="100">
          <template #default="{ row }">{{ methodLabelOf(row.method) || '—' }}</template>
        </el-table-column>
        <el-table-column label="核销进度" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.id)">详情</el-button>
            <el-button
              v-if="toNum(row.allocatedAmount) === 0"
              v-perm="'fin:payment:edit'"
              link
              type="primary"
              @click="openEdit(row)"
            >
              编辑
            </el-button>
            <el-button
              v-if="toNum(row.unallocatedAmount) > 0"
              v-perm="'fin:payment:writeoff'"
              link
              type="success"
              @click="openWriteoff(row)"
            >
              核销
            </el-button>
            <el-button
              v-if="toNum(row.allocatedAmount) === 0"
              v-perm="'fin:payment:delete'"
              link
              type="danger"
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无付款单" /></template>
      </el-table>

      <div class="pagination">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :page-size="query.pageSize"
          :current-page="query.page"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- 新增 / 编辑抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新增付款单' : '编辑付款单'"
      size="520px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="88px">
        <el-form-item label="供应商" prop="supplierId">
          <el-select v-model="form.supplierId" filterable placeholder="请选择供应商">
            <el-option
              v-for="item in supplierOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="付款日期" prop="paymentDate">
          <el-date-picker
            v-model="form.paymentDate"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="付款金额" prop="amount">
          <el-input-number
            v-model="form.amount"
            :min="0.01"
            :precision="2"
            :step="100"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="结算方式">
          <el-select v-model="form.method" clearable placeholder="请选择结算方式">
            <el-option
              v-for="item in methodOptions"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="付款账户">
          <el-input v-model="form.account" placeholder="银行账户/支付账户（选填）" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="3" placeholder="备注（选填）" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="handleSavePayment">
            保存
          </el-button>
          <el-button @click="drawerVisible = false">取消</el-button>
        </el-form-item>
      </el-form>
    </el-drawer>

    <!-- 核销对话框 -->
    <el-dialog
      v-model="writeoffVisible"
      title="付款核销"
      width="760px"
      :close-on-click-modal="false"
    >
      <template v-if="writeoffTarget">
        <el-descriptions :column="3" border size="small" class="writeoff-desc">
          <el-descriptions-item label="付款单号">
            {{ writeoffTarget.paymentNo }}
          </el-descriptions-item>
          <el-descriptions-item label="未分配余额">
            ¥{{ money(remainingUnallocated) }}
          </el-descriptions-item>
          <el-descriptions-item label="本次合计">
            ¥{{ money(totalWriteoff) }}
            <span :class="writeoffRemain < 0 ? 'over-amount' : 'left-amount'">
              （剩余 ¥{{ money(writeoffRemain) }}）
            </span>
          </el-descriptions-item>
        </el-descriptions>

        <el-table v-loading="writeoffLoading" :data="writeoffRows" border size="small">
          <el-table-column prop="sourceNo" label="来源单号" min-width="150" show-overflow-tooltip />
          <el-table-column prop="billDate" label="单据日期" width="100" />
          <el-table-column prop="dueDate" label="到期日" width="100" />
          <el-table-column label="未付金额" width="110" align="right">
            <template #default="{ row }">¥{{ money(row.unpaidAmount) }}</template>
          </el-table-column>
          <el-table-column label="本次核销金额" min-width="180">
            <template #default="{ row }">
              <el-input-number
                v-model="row.writeoffAmount"
                :min="0"
                :max="row.unpaidAmount"
                :precision="2"
                :step="100"
                size="small"
                style="width: 160px"
              />
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="该供应商没有未结清应付" :image-size="50" />
          </template>
        </el-table>

        <div class="dialog-footer">
          <el-button @click="writeoffVisible = false">取消</el-button>
          <el-button
            v-perm="'fin:payment:writeoff'"
            type="primary"
            :loading="writeoffSubmitting"
            :disabled="writeoffRemain < 0"
            @click="submitWriteoff"
          >
            确认核销
          </el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="付款单详情" size="56%">
      <div v-if="detailLoading" class="drawer-loading">加载中…</div>
      <template v-else-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="付款单号">{{ detail.paymentNo }}</el-descriptions-item>
          <el-descriptions-item label="供应商">{{ detail.supplierName ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="付款日期">{{ detail.paymentDate }}</el-descriptions-item>
          <el-descriptions-item label="结算方式">
            {{ methodLabelOf(detail.method) || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="付款金额">¥{{ money(detail.amount) }}</el-descriptions-item>
          <el-descriptions-item label="已核销">
            ¥{{ money(detail.allocatedAmount) }}
          </el-descriptions-item>
          <el-descriptions-item label="未分配">
            ¥{{ money(detail.unallocatedAmount) }}
          </el-descriptions-item>
          <el-descriptions-item label="付款账户">{{ detail.account ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="核销进度">
            <el-tag :type="statusTag(detail.status)" size="small">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="备注">{{ detail.remark ?? '—' }}</el-descriptions-item>
        </el-descriptions>

        <h3 class="section-title">核销明细</h3>
        <el-table :data="detail.items" border size="small">
          <el-table-column
            prop="payableSourceNo"
            label="应付来源单号"
            min-width="150"
            show-overflow-tooltip
          />
          <el-table-column label="核销金额" min-width="120">
            <template #default="{ row }">¥{{ money(row.writeoffAmount) }}</template>
          </el-table-column>
          <el-table-column prop="writeoffAt" label="核销时间" min-width="180" />
          <template #empty><el-empty description="暂无核销明细" :image-size="50" /></template>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.summary-card {
  padding: 16px;
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.summary-label {
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 8px;
}
.summary-value {
  font-size: 20px;
  font-weight: 600;
  color: #111827;
}
.summary-value.paid {
  color: #10b981;
}
.summary-value.unpaid {
  color: #3b82f6;
}
.filter-form {
  margin-bottom: 8px;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.writeoff-desc {
  margin-bottom: 12px;
}
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.over-amount {
  color: #ef4444;
  font-weight: 600;
}
.left-amount {
  color: #6b7280;
}
.section-title {
  font-size: 15px;
  color: #111827;
  margin: 24px 0 12px;
}
.drawer-loading {
  padding: 40px 0;
  text-align: center;
  color: #6b7280;
}
</style>
