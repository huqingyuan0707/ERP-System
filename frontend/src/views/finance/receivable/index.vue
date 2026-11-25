<script setup lang="ts">
/**
 * 应收管理页（F-01）：只读。
 * 数据来源：销售出库确认生成应收，收款核销更新已收/未收，销售退货直接冲减原应收；
 * 本页不做任何编辑/删除，负未收显示「应退/预收」，账龄只统计正数未结项。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getReceivableAging,
  getReceivableDetail,
  getReceivableList,
  getReceivableSummary,
} from '@/api/receivable'
import { getCustomerOptions } from '@/api/customer'
import type { Customer } from '@/types/customer'
import type {
  AgingBucket,
  Receivable,
  ReceivableDetail,
  ReceivableQuery,
  ReceivableSummary,
} from '@/types/receivable'
import { RECEIVABLE_STATUS } from '@/types/receivable'

const toNum = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}
const money = (value: string | number | null | undefined): string => toNum(value).toFixed(2)

const loading = ref(false)
const rows = ref<Receivable[]>([])
const total = ref(0)
const query = reactive<ReceivableQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  customerId: null,
  status: null,
  sourceNo: null,
  overdue: null,
  unsettled: null,
  billDateStart: null,
  billDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)
const customerOptions = ref<Customer[]>([])

const summary = ref<ReceivableSummary>({
  totalAmount: 0,
  totalReceived: 0,
  totalUnreceived: 0,
  creditAmount: 0,
  overdueAmount: 0,
  count: 0,
})
const aging = ref<AgingBucket[]>([])
const hasCredit = computed(() => toNum(summary.value.creditAmount) > 0)

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.billDateStart = dateRange.value?.[0] ?? null
    query.billDateEnd = dateRange.value?.[1] ?? null
    const [list, totals, buckets] = await Promise.all([
      getReceivableList(query),
      getReceivableSummary(query),
      getReceivableAging(query),
    ])
    rows.value = list.items
    total.value = list.total
    summary.value = totals
    aging.value = buckets
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
  query.sourceNo = null
  query.overdue = null
  query.unsettled = null
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

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<ReceivableDetail | null>(null)
const openDetail = async (id: number): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await getReceivableDetail(id)
  } catch (error) {
    console.error('加载应收详情失败:', error)
    ElMessage.error('加载应收详情失败')
  } finally {
    detailLoading.value = false
  }
}

const statusLabel = (status: number): string => RECEIVABLE_STATUS[status] ?? '未知'
const statusTag = (status: number): string =>
  ({ 0: 'info', 1: 'warning', 2: 'success' } as Record<number, string>)[status] ?? 'info'

onMounted(async () => {
  try {
    customerOptions.value = await getCustomerOptions()
  } catch (error) {
    console.error('加载客户选项失败:', error)
  }
  await fetchList()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">应收管理</h1>
        <p class="page-subtitle">
          只读视图：销售出库确认生成应收，收款核销更新余额，销售退货直接冲减原应收。
        </p>
      </div>

      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">应收总额</div>
          <div class="summary-value">¥{{ money(summary.totalAmount) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">已收金额</div>
          <div class="summary-value received">¥{{ money(summary.totalReceived) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">未收余额</div>
          <div class="summary-value unreceived">¥{{ money(summary.totalUnreceived) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">逾期未收</div>
          <div class="summary-value overdue">¥{{ money(summary.overdueAmount) }}</div>
        </div>
        <div v-if="hasCredit" class="summary-card credit">
          <div class="summary-label">客户应退/预收</div>
          <div class="summary-value credit">¥{{ money(summary.creditAmount) }}</div>
        </div>
      </div>

      <div v-if="aging.length" class="aging-bar">
        <span class="aging-title">账龄分布（仅正数未结项）</span>
        <el-tag v-for="bucket in aging" :key="bucket.bucket" class="aging-tag" type="info">
          {{ bucket.bucket }} · {{ bucket.count }} 笔 · ¥{{ money(bucket.amount) }}
        </el-tag>
      </div>

      <el-form :inline="true" class="filter-form" @submit.prevent="handleSearch">
        <el-form-item label="客户">
          <el-select v-model="query.customerId" clearable filterable placeholder="全部" style="width: 170px">
            <el-option v-for="c in customerOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 120px">
            <el-option label="未收" :value="0" />
            <el-option label="部分收" :value="1" />
            <el-option label="已收" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源单号">
          <el-input v-model="query.sourceNo" clearable placeholder="出库单号" style="width: 150px" />
        </el-form-item>
        <el-form-item label="单据日期">
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
        <el-form-item label="逾期">
          <el-select v-model="query.overdue" clearable placeholder="全部" style="width: 110px">
            <el-option label="仅逾期" :value="true" />
            <el-option label="未逾期" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键字">
          <el-input v-model="query.keyword" clearable placeholder="单号/备注" style="width: 140px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="customerName" label="客户" min-width="150" />
        <el-table-column prop="sourceNo" label="来源单号" width="170" />
        <el-table-column prop="billDate" label="单据日期" width="120" />
        <el-table-column prop="dueDate" label="到期日" width="120" />
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }: { row: Receivable }">{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已收" width="120" align="right">
          <template #default="{ row }: { row: Receivable }">{{ money(row.receivedAmount) }}</template>
        </el-table-column>
        <el-table-column label="未收" width="120" align="right">
          <template #default="{ row }: { row: Receivable }">
            <span :class="toNum(row.unreceivedAmount) < 0 ? 'credit-text' : ''">
              {{ money(row.unreceivedAmount) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }: { row: Receivable }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="账龄" width="110">
          <template #default="{ row }: { row: Receivable }">
            <span :class="row.agingDays > 0 ? 'overdue-text' : ''">{{ row.agingBucket }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }: { row: Receivable }">
            <el-button link type="primary" @click="openDetail(row.id)">详情</el-button>
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

    <el-drawer v-model="detailVisible" title="应收详情" size="50%">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="客户">{{ detail.customerName }}</el-descriptions-item>
            <el-descriptions-item label="来源单号">{{ detail.sourceNo }}</el-descriptions-item>
            <el-descriptions-item label="单据日期">{{ detail.billDate }}</el-descriptions-item>
            <el-descriptions-item label="到期日">{{ detail.dueDate || '-' }}</el-descriptions-item>
            <el-descriptions-item label="应收金额">{{ money(detail.amount) }}</el-descriptions-item>
            <el-descriptions-item label="已收金额">{{ money(detail.receivedAmount) }}</el-descriptions-item>
            <el-descriptions-item label="未收金额">{{ money(detail.unreceivedAmount) }}</el-descriptions-item>
            <el-descriptions-item v-if="toNum(detail.creditAmount) > 0" label="应退/预收">
              {{ money(detail.creditAmount) }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">{{ statusLabel(detail.status) }}</el-descriptions-item>
            <el-descriptions-item label="账龄">{{ detail.agingBucket }}</el-descriptions-item>
            <el-descriptions-item label="备注" :span="2">{{ detail.remark || '-' }}</el-descriptions-item>
          </el-descriptions>

          <h4>收款核销记录</h4>
          <el-table :data="detail.writeoffs" border size="small">
            <el-table-column prop="receiptId" label="收款单 ID" width="120" />
            <el-table-column label="核销金额" width="140" align="right">
              <template #default="{ row }: { row: ReceivableDetail['writeoffs'][number] }">
                {{ money(row.writeoffAmount) }}
              </template>
            </el-table-column>
            <el-table-column prop="writeoffAt" label="核销时间" min-width="180" />
          </el-table>
          <div v-if="!detail.writeoffs.length" class="empty-hint">暂无收款核销记录</div>

          <h4>关联销售退货</h4>
          <el-table :data="detail.returns" border size="small">
            <el-table-column prop="returnNo" label="退货单号" width="170" />
            <el-table-column prop="returnDate" label="退货日期" width="120" />
            <el-table-column label="退货金额" width="130" align="right">
              <template #default="{ row }: { row: ReceivableDetail['returns'][number] }">
                {{ money(row.totalAmount) }}
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!detail.returns.length" class="empty-hint">暂无关联销售退货</div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-container { padding: 16px; }
.page-card { background: #fff; padding: 16px; border-radius: 4px; }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 20px; margin: 0 0 4px; }
.page-subtitle { color: #909399; font-size: 13px; margin: 0; }
.summary-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.summary-card { flex: 1 1 180px; border: 1px solid #ebeef5; border-radius: 4px; padding: 12px 16px; }
.summary-label { color: #909399; font-size: 13px; }
.summary-value { font-size: 20px; font-weight: 600; margin-top: 6px; }
.summary-value.received { color: #10b981; }
.summary-value.unreceived { color: #3b82f6; }
.summary-value.overdue { color: #ef4444; }
.summary-value.credit, .credit-text { color: #f59e0b; }
.summary-card.credit { border-color: #fcd34d; background: #fffbeb; }
.aging-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.aging-title { color: #606266; font-size: 13px; }
.filter-form { margin-bottom: 12px; }
.overdue-text { color: #ef4444; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
h4 { margin: 20px 0 8px; }
.empty-hint { color: #909399; font-size: 13px; padding: 8px 0; }
</style>
