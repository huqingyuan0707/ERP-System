<script setup lang="ts">
/** R-03 销售报表：明细/汇总/客户排行/商品排行；退货以负数行展示，合计=出库-退货。 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  downloadTaskFile,
  getTaskDetail,
  isTerminalTaskError,
} from '@/api/task'
import { exportSalesReport, getSalesReport } from '@/api/salesReport'
import type {
  SalesReportDetailItem,
  SalesReportGroupBy,
  SalesReportOut,
  SalesReportQuery,
  SalesReportRankItem,
  SalesReportSummaryItem,
  SalesReportView,
} from '@/types/salesReport'

const VIEW_OPTIONS: Array<{ label: string; value: SalesReportView }> = [
  { label: '明细', value: 'detail' },
  { label: '汇总', value: 'summary' },
  { label: '客户排行', value: 'customer_rank' },
  { label: '商品排行', value: 'goods_rank' },
]

const query = reactive<SalesReportQuery>({
  page: 1,
  pageSize: 20,
  view: 'detail',
  groupBy: 'goods',
  startDate: null,
  endDate: null,
  customerId: null,
  goodsId: null,
  warehouseId: null,
})
const dateRange = ref<[string, string] | null>(null)
const loading = ref(false)
const report = ref<SalesReportOut | null>(null)
const exporting = ref(false)
const exportProgress = ref(0)
let exportTimer: ReturnType<typeof setTimeout> | null = null
let exportController: AbortController | null = null
let disposed = false
let polls = 0
let failures = 0
const MAX_POLLS = 150
const MAX_FAILURES = 3

const totals = computed(() => report.value?.totals ?? null)
const items = computed(() => report.value?.items ?? [])
const money = (value: string | number | undefined | null): string => {
  if (value === undefined || value === null || value === '') return '0.00'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}
const qty = (value: string | number | undefined | null): string => {
  if (value === undefined || value === null || value === '') return '0'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

const load = async (): Promise<void> => {
  loading.value = true
  try {
    query.startDate = dateRange.value?.[0] ?? null
    query.endDate = dateRange.value?.[1] ?? null
    report.value = await getSalesReport(query)
  } catch {
    // 统一拦截
  } finally {
    loading.value = false
  }
}
const search = (): void => {
  query.page = 1
  void load()
}
const reset = (): void => {
  query.page = 1
  query.customerId = null
  query.goodsId = null
  query.warehouseId = null
  dateRange.value = null
  void load()
}
const changeView = (view: SalesReportView): void => {
  query.view = view
  query.page = 1
  void load()
}
const changeGroupBy = (group: SalesReportGroupBy): void => {
  query.groupBy = group
  query.page = 1
  void load()
}
const handlePageChange = (page: number): void => {
  query.page = page
  void load()
}
const handleSizeChange = (size: number): void => {
  query.pageSize = size
  query.page = 1
  void load()
}

const stopPolling = (): void => {
  if (exportTimer) clearTimeout(exportTimer)
  exportTimer = null
  exporting.value = false
}
onBeforeUnmount(() => {
  disposed = true
  stopPolling()
  exportController?.abort()
})

const pollExport = async (taskId: string): Promise<void> => {
  if (disposed || !exporting.value) return
  polls += 1
  exportController = new AbortController()
  try {
    const task = await getTaskDetail(taskId, exportController.signal)
    if (disposed) return
    failures = 0
    exportProgress.value = task.progress
    if (task.status === 3) {
      stopPolling()
      exportProgress.value = 100
      if (!task.canDownload) {
        ElMessage.error('任务没有可下载文件或无下载权限')
        return
      }
      await downloadTaskFile(task)
      if (!disposed) ElMessage.success('导出完成，文件已下载')
      return
    }
    if (task.status === 4) {
      stopPolling()
      ElMessage.error(`导出失败：${task.errorMsg ?? '未知错误'}`)
      return
    }
  } catch (error) {
    if (disposed) return
    failures += 1
    if (!exporting.value) return
    if (isTerminalTaskError(error) || failures >= MAX_FAILURES) {
      stopPolling()
      ElMessage.error('无法继续查询任务，请稍后在任务列表查看')
      return
    }
  }
  if (disposed || !exporting.value) return
  if (polls >= MAX_POLLS) {
    stopPolling()
    ElMessage.warning('已停止等待，请稍后在任务列表查看结果')
    return
  }
  exportTimer = setTimeout(() => void pollExport(taskId), 2000 * Math.max(1, failures))
}
const doExport = async (): Promise<void> => {
  if (exporting.value) return
  try {
    exporting.value = true
    exportProgress.value = 0
    const task = await exportSalesReport(query)
    if (disposed) return
    if (!task.taskId) throw new Error('未获取到任务 ID')
    polls = 0
    failures = 0
    void pollExport(task.taskId)
  } catch {
    stopPolling()
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="sales-report-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>销售报表</span>
          <el-button type="primary" :loading="exporting" @click="doExport">
            {{ exporting ? `导出中 ${exportProgress}%` : '导出' }}
          </el-button>
        </div>
      </template>

      <el-tabs :model-value="query.view" class="view-tabs" @tab-change="changeView">
        <el-tab-pane v-for="opt in VIEW_OPTIONS" :key="opt.value" :label="opt.label" :name="opt.value" />
      </el-tabs>

      <el-form :inline="true" class="filter-form" @submit.prevent="search">
        <el-form-item label="日期">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="~"
            start-placeholder="起"
            end-placeholder="止"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="客户ID">
          <el-input-number v-model="query.customerId" :min="1" controls-position="right" placeholder="全部" style="width: 140px" />
        </el-form-item>
        <el-form-item label="商品ID">
          <el-input-number v-model="query.goodsId" :min="1" controls-position="right" placeholder="全部" style="width: 140px" />
        </el-form-item>
        <el-form-item label="仓库ID">
          <el-input-number v-model="query.warehouseId" :min="1" controls-position="right" placeholder="全部" style="width: 140px" />
        </el-form-item>
        <el-form-item v-if="query.view === 'summary'" label="汇总维度">
          <el-select :model-value="query.groupBy" style="width: 120px" @update:model-value="changeGroupBy">
            <el-option label="按商品" value="goods" />
            <el-option label="按客户" value="customer" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <div v-if="totals" class="totals-grid">
        <div class="totals-card">
          <span>出库金额</span><b>¥{{ money(totals.outboundAmount) }}</b>
        </div>
        <div class="totals-card">
          <span>退货金额</span><b class="return">¥{{ money(totals.returnAmount) }}</b>
        </div>
        <div class="totals-card">
          <span>净销售</span><b class="net">¥{{ money(totals.netAmount) }}</b>
        </div>
        <div class="totals-card">
          <span>净数量</span><b>{{ qty(totals.netQty) }}</b>
        </div>
        <div class="totals-card">
          <span>行数</span><b>{{ totals.rowCount }}</b>
        </div>
      </div>

      <!-- 明细 -->
      <el-table
        v-if="query.view === 'detail'"
        v-loading="loading"
        :data="items as SalesReportDetailItem[]"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column label="类型" width="90">
          <template #default="{ row }: { row: SalesReportDetailItem }">
            <el-tag :type="row.type === 'outbound' ? 'success' : 'danger'" size="small">
              {{ row.type === 'outbound' ? '出库' : '退货' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="date" label="单据日期" width="120" />
        <el-table-column prop="docNo" label="单据号" width="170" />
        <el-table-column prop="sourceNo" label="来源出库单" width="170" />
        <el-table-column prop="customerName" label="客户" min-width="140" />
        <el-table-column prop="goodsCode" label="商品编码" width="120" />
        <el-table-column prop="goodsName" label="商品名称" min-width="140" />
        <el-table-column prop="warehouseName" label="仓库" width="120" />
        <el-table-column label="数量" width="110" align="right">
          <template #default="{ row }: { row: SalesReportDetailItem }">{{ qty(row.qty) }}</template>
        </el-table-column>
        <el-table-column label="单价" width="110" align="right">
          <template #default="{ row }: { row: SalesReportDetailItem }">{{ money(row.price) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }: { row: SalesReportDetailItem }">
            <span :class="Number(row.amount) < 0 ? 'negative' : ''">{{ money(row.amount) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 汇总 -->
      <el-table
        v-else-if="query.view === 'summary'"
        v-loading="loading"
        :data="items as SalesReportSummaryItem[]"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column prop="groupId" label="分组 ID" width="100" />
        <el-table-column prop="groupName" label="名称" min-width="180" />
        <el-table-column label="出库数量" width="120" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">{{ qty(row.outboundQty) }}</template>
        </el-table-column>
        <el-table-column label="退货数量" width="120" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">{{ qty(row.returnQty) }}</template>
        </el-table-column>
        <el-table-column label="净数量" width="120" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">{{ qty(row.netQty) }}</template>
        </el-table-column>
        <el-table-column label="出库金额" width="130" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">{{ money(row.outboundAmount) }}</template>
        </el-table-column>
        <el-table-column label="退货金额" width="130" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">{{ money(row.returnAmount) }}</template>
        </el-table-column>
        <el-table-column label="净金额" width="130" align="right">
          <template #default="{ row }: { row: SalesReportSummaryItem }">
            <span class="net">{{ money(row.netAmount) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <!-- 排行 -->
      <el-table
        v-else
        v-loading="loading"
        :data="items as SalesReportRankItem[]"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column prop="rank" label="排名" width="80" />
        <el-table-column prop="rankName" label="名称" min-width="200" />
        <el-table-column label="净数量" width="140" align="right">
          <template #default="{ row }: { row: SalesReportRankItem }">{{ qty(row.netQty) }}</template>
        </el-table-column>
        <el-table-column label="净金额" width="160" align="right">
          <template #default="{ row }: { row: SalesReportRankItem }">
            <span class="net">{{ money(row.netAmount) }}</span>
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
          :total="report?.total ?? 0"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.sales-report-page { padding: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.view-tabs { margin-bottom: 8px; }
.filter-form { flex-wrap: wrap; gap: 4px; }
.totals-grid { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.totals-card { flex: 1 1 140px; border: 1px solid #ebeef5; border-radius: 4px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px; }
.totals-card span { font-size: 12px; color: #909399; }
.totals-card b { font-size: 18px; }
.totals-card .return, .negative { color: #ef4444; }
.totals-card .net, .net { color: #409eff; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
