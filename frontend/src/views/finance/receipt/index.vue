<script setup lang="ts">
/**
 * 收款管理页（F-03）：收款单 CRUD + 收款核销。
 * 数据闭环：销售出库确认生成应收（SA-03）→ 新建收款单（不动应收）→ 核销冲减对应应收；
 * 核销弹窗取该客户未结清应收（unsettled=true）分配金额，校验客户一致由后端保证；
 * 编辑/删除仅未核销可用；核销进度由后端派生，本页只展示。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import { getReceivableList } from '@/api/receivable'
import {
  createReceipt,
  deleteReceipt,
  getReceiptDetail,
  getReceiptList,
  getReceiptSummary,
  updateReceipt,
  writeoffReceipt,
} from '@/api/receipt'
import { getCustomerOptions } from '@/api/customer'
import { useDict } from '@/composables/useDict'
import type { Customer } from '@/types/customer'
import type { Receivable } from '@/types/receivable'
import type { Receipt, ReceiptQuery, ReceiptSummary } from '@/types/receipt'
import { RECEIPT_STATUS } from '@/types/receipt'

const toNum = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}
const money = (value: string | number | null | undefined): string => toNum(value).toFixed(2)

const { options: methodOptions, labelOf: methodLabelOf } = useDict('settlement_type')

const loading = ref(false)
const rows = ref<Receipt[]>([])
const total = ref(0)
const query = reactive<ReceiptQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  customerId: null,
  status: null,
  receiptDateStart: null,
  receiptDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)
const customerOptions = ref<Customer[]>([])
const summary = ref<ReceiptSummary>({
  totalAmount: 0,
  totalAllocated: 0,
  totalUnallocated: 0,
  count: 0,
})

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.receiptDateStart = dateRange.value?.[0] ?? null
    query.receiptDateEnd = dateRange.value?.[1] ?? null
    const [list, totals] = await Promise.all([getReceiptList(query), getReceiptSummary(query)])
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
  query.customerId = null
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
const statusLabel = (status: number): string => RECEIPT_STATUS[status] ?? '未知'
const statusTag = (status: number): 'info' | 'warning' | 'success' =>
  ({ 0: 'info', 1: 'warning', 2: 'success' } as Record<number, 'info' | 'warning' | 'success'>)[
    status
  ] ?? 'info'

// ---------- 新增 / 编辑收款单 ----------
interface ReceiptForm {
  customerId: number | null
  receiptDate: string
  amount: number | null
  method: string | null
  account: string
  remark: string
  version: number | null
}
const emptyForm = (): ReceiptForm => ({
  customerId: null,
  receiptDate: new Date().toISOString().slice(0, 10),
  amount: null,
  method: null,
  account: '',
  remark: '',
  version: null,
})
const form = reactive<ReceiptForm>(emptyForm())
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)
const formRules: FormRules = {
  customerId: [{ required: true, message: '请选择客户', trigger: 'change' }],
  receiptDate: [{ required: true, message: '请选择收款日期', trigger: 'change' }],
  amount: [{ required: true, message: '请输入收款金额', trigger: 'blur' }],
}

const openCreate = (): void => {
  drawerMode.value = 'create'
  editingId.value = null
  Object.assign(form, emptyForm())
  drawerVisible.value = true
  formRef.value?.clearValidate()
}
const openEdit = async (row: Receipt): Promise<void> => {
  const detail = await getReceiptDetail(row.id)
  drawerMode.value = 'edit'
  editingId.value = detail.id
  Object.assign(form, {
    customerId: detail.customerId,
    receiptDate: detail.receiptDate,
    amount: toNum(detail.amount),
    method: detail.method,
    account: detail.account ?? '',
    remark: detail.remark ?? '',
    version: detail.version,
  })
  drawerVisible.value = true
  formRef.value?.clearValidate()
}
const handleSave = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  if (form.customerId === null || form.amount === null || toNum(form.amount) <= 0) {
    ElMessage.warning('请填写客户与大于 0 的收款金额')
    return
  }
  submitting.value = true
  try {
    const payload = {
      customerId: Number(form.customerId),
      receiptDate: form.receiptDate,
      amount: toNum(form.amount).toFixed(2),
      method: form.method || null,
      account: form.account || null,
      remark: form.remark || null,
      version: form.version ?? undefined,
    }
    if (drawerMode.value === 'create') {
      await createReceipt(payload)
      ElMessage.success('收款单已创建（待核销）')
    } else {
      await updateReceipt(editingId.value!, payload)
      ElMessage.success('收款单已修改')
    }
    drawerVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row: Receipt): Promise<void> => {
  try {
    await ElMessageBox.confirm(`确认删除收款单 ${row.receiptNo}？`, '提示', { type: 'warning' })
    await deleteReceipt(row.id)
    ElMessage.success('已删除')
    await fetchList()
  } catch {
    // 用户取消或后端拒绝
  }
}

// ---------- 核销 ----------
interface WriteoffLine {
  receivableId: number
  sourceNo: string
  remaining: number
  amount: number | null
}
const writeoffVisible = ref(false)
const writeoffReceiptId = ref<number | null>(null)
const writeoffRemaining = ref(0)
const writeoffLines = ref<WriteoffLine[]>([])
const writeoffSubmitting = ref(false)

const openWriteoff = async (row: Receipt): Promise<void> => {
  writeoffReceiptId.value = row.id
  writeoffRemaining.value = toNum(row.unallocatedAmount)
  writeoffLines.value = []
  writeoffVisible.value = true
  try {
    const list = await getReceivableList({
      page: 1,
      pageSize: 200,
      keyword: null,
      customerId: row.customerId,
      status: null,
      sourceNo: null,
      overdue: null,
      unsettled: true,
      billDateStart: null,
      billDateEnd: null,
    })
    writeoffLines.value = list.items.map((item: Receivable) => ({
      receivableId: item.id,
      sourceNo: item.sourceNo,
      remaining: toNum(item.unreceivedAmount),
      amount: null,
    }))
  } catch {
    ElMessage.error('加载未结清应收失败')
  }
}
const writeoffTotal = (): number =>
  writeoffLines.value.reduce((sum, line) => sum + toNum(line.amount), 0)
const handleWriteoff = async (): Promise<void> => {
  const validLines = writeoffLines.value.filter((line) => toNum(line.amount) > 0)
  if (!validLines.length) {
    ElMessage.warning('请至少填写一条核销金额')
    return
  }
  for (const line of validLines) {
    if (toNum(line.amount) > line.remaining) {
      ElMessage.warning(`应收 ${line.sourceNo} 核销金额超过未收 ${money(line.remaining)}`)
      return
    }
  }
  if (writeoffTotal() > writeoffRemaining.value + 0.0001) {
    ElMessage.warning('核销总额超过收款单未分配金额')
    return
  }
  writeoffSubmitting.value = true
  try {
    await writeoffReceipt(writeoffReceiptId.value!, {
      items: validLines.map((line) => ({
        receivableId: line.receivableId,
        writeoffAmount: toNum(line.amount).toFixed(2),
      })),
    })
    ElMessage.success('收款核销成功，应收余额已同步')
    writeoffVisible.value = false
    await fetchList()
  } finally {
    writeoffSubmitting.value = false
  }
}

// ---------- 详情 ----------
const detailVisible = ref(false)
const detail = ref<Receipt | null>(null)
const openDetail = async (row: Receipt): Promise<void> => {
  detailVisible.value = true
  detail.value = null
  try {
    detail.value = await getReceiptDetail(row.id)
  } catch {
    ElMessage.error('加载收款单详情失败')
  }
}

onMounted(async () => {
  try {
    customerOptions.value = await getCustomerOptions()
  } catch {
    // 统一拦截
  }
  await fetchList()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">收款管理</h1>
        <p class="page-subtitle">
          销售出库确认生成应收；新建收款单后核销冲减对应应收，可分次核销。
        </p>
        <el-button type="primary" class="create-btn" @click="openCreate">新增收款单</el-button>
      </div>

      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">收款总额</div>
          <div class="summary-value">¥{{ money(summary.totalAmount) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">已核销</div>
          <div class="summary-value received">¥{{ money(summary.totalAllocated) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">未分配</div>
          <div class="summary-value unallocated">¥{{ money(summary.totalUnallocated) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">收款单数</div>
          <div class="summary-value">{{ summary.count }}</div>
        </div>
      </div>

      <el-form :inline="true" class="filter-form" @submit.prevent="handleSearch">
        <el-form-item label="客户">
          <el-select v-model="query.customerId" clearable filterable placeholder="全部" style="width: 170px">
            <el-option v-for="c in customerOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="核销进度">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 130px">
            <el-option label="待核销" :value="0" />
            <el-option label="部分核销" :value="1" />
            <el-option label="已核销" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款日期">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="~"
            start-placeholder="起"
            end-placeholder="止"
            style="width: 230px"
          />
        </el-form-item>
        <el-form-item label="关键字">
          <el-input v-model="query.keyword" clearable placeholder="单号/备注" style="width: 150px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="receiptNo" label="收款单号" width="170" />
        <el-table-column prop="customerName" label="客户" min-width="150" />
        <el-table-column prop="receiptDate" label="收款日期" width="120" />
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }: { row: Receipt }">{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已核销" width="120" align="right">
          <template #default="{ row }: { row: Receipt }">{{ money(row.allocatedAmount) }}</template>
        </el-table-column>
        <el-table-column label="未分配" width="120" align="right">
          <template #default="{ row }: { row: Receipt }">{{ money(row.unallocatedAmount) }}</template>
        </el-table-column>
        <el-table-column label="进度" width="110">
          <template #default="{ row }: { row: Receipt }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结算方式" width="110">
          <template #default="{ row }: { row: Receipt }">{{ methodLabelOf(row.method) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }: { row: Receipt }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button v-if="toNum(row.unallocatedAmount) > 0" link type="primary" @click="openWriteoff(row)">核销</el-button>
            <el-button v-if="row.status === 0" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="row.status === 0" link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next, jumper"
          :current-page="query.page"
          :page-size="query.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- 新增 / 编辑收款单 -->
    <el-drawer v-model="drawerVisible" :title="drawerMode === 'create' ? '新增收款单' : '编辑收款单'" size="480px">
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="90px">
        <el-form-item label="客户" prop="customerId">
          <el-select v-model="form.customerId" filterable placeholder="请选择客户" style="width: 100%">
            <el-option v-for="c in customerOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款日期" prop="receiptDate">
          <el-date-picker v-model="form.receiptDate" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="收款金额" prop="amount">
          <el-input-number v-model="form.amount" :min="0.01" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结算方式">
          <el-select v-model="form.method" clearable placeholder="请选择" style="width: 100%">
            <el-option v-for="opt in methodOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="收款账户">
          <el-input v-model="form.account" placeholder="银行账户（可选）" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSave">保存</el-button>
      </template>
    </el-drawer>

    <!-- 核销对话框 -->
    <el-dialog v-model="writeoffVisible" title="收款核销" width="860px">
      <el-alert type="info" :closable="false" class="hint"
        :title="`本单未分配 ¥${money(writeoffRemaining)}，填写各应收本次核销金额（可多次核销）`" />
      <el-table :data="writeoffLines" border style="width: 100%">
        <el-table-column prop="sourceNo" label="应收来源单号" min-width="180" />
        <el-table-column label="未收金额" width="130" align="right">
          <template #default="{ row }: { row: WriteoffLine }">{{ money(row.remaining) }}</template>
        </el-table-column>
        <el-table-column label="本次核销" width="170">
          <template #default="{ row }: { row: WriteoffLine }">
            <el-input-number v-model="row.amount" :min="0" :max="row.remaining" :precision="2" size="small" style="width: 140px" />
          </template>
        </el-table-column>
      </el-table>
      <div class="writeoff-total">核销合计：¥{{ money(writeoffTotal()) }} / 未分配 ¥{{ money(writeoffRemaining) }}</div>
      <template #footer>
        <el-button @click="writeoffVisible = false">取消</el-button>
        <el-button type="primary" :loading="writeoffSubmitting" @click="handleWriteoff">确认核销</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-drawer v-model="detailVisible" title="收款单详情" size="55%">
      <div v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="收款单号">{{ detail.receiptNo }}</el-descriptions-item>
          <el-descriptions-item label="客户">{{ detail.customerName }}</el-descriptions-item>
          <el-descriptions-item label="收款日期">{{ detail.receiptDate }}</el-descriptions-item>
          <el-descriptions-item label="结算方式">{{ methodLabelOf(detail.method) }}</el-descriptions-item>
          <el-descriptions-item label="收款金额">{{ money(detail.amount) }}</el-descriptions-item>
          <el-descriptions-item label="已核销">{{ money(detail.allocatedAmount) }}</el-descriptions-item>
          <el-descriptions-item label="未分配">{{ money(detail.unallocatedAmount) }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ statusLabel(detail.status) }}</el-descriptions-item>
          <el-descriptions-item label="账户">{{ detail.account || '-' }}</el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">{{ detail.remark || '-' }}</el-descriptions-item>
        </el-descriptions>
        <h4>核销明细</h4>
        <el-table :data="detail.items" border size="small">
          <el-table-column prop="receivableSourceNo" label="应收来源单号" min-width="180" />
          <el-table-column label="核销金额" width="130" align="right">
            <template #default="{ row }: { row: Receipt['items'][number] }">{{ money(row.writeoffAmount) }}</template>
          </el-table-column>
          <el-table-column prop="writeoffAt" label="核销时间" min-width="180" />
        </el-table>
        <div v-if="!detail.items.length" class="empty-hint">暂无核销明细</div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-container { padding: 16px; }
.page-card { background: #fff; padding: 16px; border-radius: 4px; }
.page-header { position: relative; margin-bottom: 16px; }
.page-title { font-size: 20px; margin: 0 0 4px; }
.page-subtitle { color: #909399; font-size: 13px; margin: 0; }
.create-btn { position: absolute; right: 0; top: 0; }
.summary-grid { display: flex; gap: 12px; margin-bottom: 16px; }
.summary-card { flex: 1; border: 1px solid #ebeef5; border-radius: 4px; padding: 12px 16px; }
.summary-label { color: #909399; font-size: 13px; }
.summary-value { font-size: 20px; font-weight: 600; margin-top: 6px; }
.summary-value.received { color: #10b981; }
.summary-value.unallocated { color: #3b82f6; }
.filter-form { margin-bottom: 12px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
.hint { margin-bottom: 12px; }
.writeoff-total { text-align: right; font-weight: 600; margin-top: 12px; }
h4 { margin: 20px 0 8px; }
.empty-hint { color: #909399; font-size: 13px; padding: 8px 0; }
</style>
