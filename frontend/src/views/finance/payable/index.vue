<script setup lang="ts">
/**
 * 应付管理页（F-02）：只读。
 * 数据来源：采购入库确认生成，付款核销更新已付/未付，采购退货直接冲减原应付；
 * 本页不做任何编辑/删除，负未付显示「应退/预付」，账龄只统计正数未结项。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getPayableAging,
  getPayableDetail,
  getPayableList,
  getPayableSummary,
} from '@/api/payable'
import { getSupplierOptions } from '@/api/supplier'
import type { Supplier } from '@/types/supplier'
import type {
  AgingBucket,
  Payable,
  PayableDetail,
  PayableQuery,
  PayableSummary,
} from '@/types/payable'
import { PAYABLE_STATUS } from '@/types/payable'

const toNum = (value: string | number | null | undefined): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}
const money = (value: string | number | null | undefined): string => toNum(value).toFixed(2)

const loading = ref(false)
const rows = ref<Payable[]>([])
const total = ref(0)
const query = reactive<PayableQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  supplierId: null,
  status: null,
  sourceNo: null,
  overdue: null,
  unsettled: null,
  billDateStart: null,
  billDateEnd: null,
})
const dateRange = ref<[string, string] | null>(null)
const supplierOptions = ref<Supplier[]>([])

const summary = ref<PayableSummary>({
  totalAmount: 0,
  totalPaid: 0,
  totalUnpaid: 0,
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
      getPayableList(query),
      getPayableSummary(query),
      getPayableAging(query),
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
  query.supplierId = null
  query.status = null
  query.sourceNo = null
  query.overdue = null
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

// 详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<PayableDetail | null>(null)

const openDetail = async (id: number): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await getPayableDetail(id)
  } catch (error) {
    console.error('加载应付详情失败:', error)
    ElMessage.error('加载应付详情失败')
  } finally {
    detailLoading.value = false
  }
}

const statusLabel = (status: number): string => PAYABLE_STATUS[status] ?? '未知'
const statusTag = (status: number): string =>
  ({ 0: 'info', 1: 'warning', 2: 'success' } as Record<number, string>)[status] ?? 'info'

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
        <h1 class="page-title">应付管理</h1>
        <p class="page-subtitle">
          只读视图：采购入库确认生成应付，付款核销更新余额，采购退货直接冲减原应付。
        </p>
      </div>

      <!-- 汇总卡片 -->
      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">应付总额</div>
          <div class="summary-value">¥{{ money(summary.totalAmount) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">已付金额</div>
          <div class="summary-value paid">¥{{ money(summary.totalPaid) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">未付余额</div>
          <div class="summary-value unpaid">¥{{ money(summary.totalUnpaid) }}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">逾期未付</div>
          <div class="summary-value overdue">¥{{ money(summary.overdueAmount) }}</div>
        </div>
        <div v-if="hasCredit" class="summary-card credit">
          <div class="summary-label">供应商应退/预付</div>
          <div class="summary-value credit">¥{{ money(summary.creditAmount) }}</div>
        </div>
      </div>

      <!-- 账龄分桶 -->
      <div v-if="aging.length" class="aging-bar">
        <span class="aging-title">账龄分布（仅正数未结项）</span>
        <el-tag v-for="bucket in aging" :key="bucket.bucket" class="aging-tag" type="info">
          {{ bucket.bucket }} · {{ bucket.count }} 笔 · ¥{{ money(bucket.amount) }}
        </el-tag>
      </div>

      <!-- 筛选 -->
      <el-form class="filter-form" :inline="true" label-width="72px">
        <el-form-item label="关键字">
          <el-input
            v-model="query.keyword"
            placeholder="来源单号/备注"
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
        <el-form-item label="状态">
          <el-select v-model="query.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="未付" :value="0" />
            <el-option label="部分付" :value="1" />
            <el-option label="已付" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item label="逾期">
          <el-select v-model="query.overdue" placeholder="全部" clearable style="width: 120px">
            <el-option label="仅逾期" :value="true" />
            <el-option label="未逾期" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="单据日期">
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
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe>
        <el-table-column prop="supplierName" label="供应商" min-width="140" show-overflow-tooltip />
        <el-table-column prop="sourceNo" label="来源单号" min-width="150" show-overflow-tooltip />
        <el-table-column prop="billDate" label="单据日期" width="120" />
        <el-table-column prop="dueDate" label="到期日" width="120" />
        <el-table-column label="应付金额" min-width="120" align="right">
          <template #default="{ row }">¥{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已付" min-width="110" align="right">
          <template #default="{ row }">¥{{ money(row.paidAmount) }}</template>
        </el-table-column>
        <el-table-column label="未付" min-width="140" align="right">
          <template #default="{ row }">
            <span v-if="toNum(row.unpaidAmount) < 0" class="credit-text">
              应退/预付 ¥{{ money(row.creditAmount) }}
            </span>
            <span v-else>¥{{ money(row.unpaidAmount) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="账龄" min-width="120">
          <template #default="{ row }">
            <span :class="row.agingDays > 0 ? 'overdue-text' : ''">{{ row.agingBucket }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.id)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无应付数据" /></template>
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

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="应付详情" size="56%">
      <div v-if="detailLoading" class="drawer-loading">加载中…</div>
      <template v-else-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="供应商">{{ detail.supplierName ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="来源单号">{{ detail.sourceNo }}</el-descriptions-item>
          <el-descriptions-item label="单据日期">{{ detail.billDate }}</el-descriptions-item>
          <el-descriptions-item label="到期日">{{ detail.dueDate ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="应付金额">¥{{ money(detail.amount) }}</el-descriptions-item>
          <el-descriptions-item label="已付金额">¥{{ money(detail.paidAmount) }}</el-descriptions-item>
          <el-descriptions-item label="未付余额">¥{{ money(detail.unpaidAmount) }}</el-descriptions-item>
          <el-descriptions-item label="应退/预付">¥{{ money(detail.creditAmount) }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(detail.status) }}</el-descriptions-item>
          <el-descriptions-item label="账龄">{{ detail.agingBucket }}</el-descriptions-item>
        </el-descriptions>

        <h3 class="section-title">付款核销记录</h3>
        <el-table :data="detail.writeoffs" border size="small">
          <el-table-column prop="paymentId" label="付款单 ID" width="110" />
          <el-table-column label="核销金额" min-width="120">
            <template #default="{ row }">¥{{ money(row.writeoffAmount) }}</template>
          </el-table-column>
          <el-table-column prop="writeoffAt" label="核销时间" min-width="180" />
          <template #empty><el-empty description="暂无核销记录" :image-size="50" /></template>
        </el-table>

        <h3 class="section-title">采购退货冲减</h3>
        <el-table :data="detail.returns" border size="small">
          <el-table-column prop="returnNo" label="退货单号" min-width="140" />
          <el-table-column prop="returnDate" label="退货日期" width="120" />
          <el-table-column label="冲减金额" min-width="120">
            <template #default="{ row }">¥{{ money(row.totalAmount) }}</template>
          </el-table-column>
          <template #empty><el-empty description="暂无退货冲减" :image-size="50" /></template>
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
.summary-value.paid { color: #10b981; }
.summary-value.unpaid { color: #3b82f6; }
.summary-value.overdue { color: #ef4444; }
.summary-value.credit { color: #f59e0b; }
.summary-card.credit { border-color: #fcd34d; background: #fffbeb; }
.aging-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.aging-title {
  font-size: 13px;
  color: #6b7280;
  margin-right: 4px;
}
.filter-form {
  margin-bottom: 8px;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.credit-text, .overdue-text {
  color: #f59e0b;
  font-weight: 600;
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
