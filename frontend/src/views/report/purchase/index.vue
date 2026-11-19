<script setup lang="ts">
/** R-02 采购报表：明细 / 汇总 / 排行，退货以负数行展示，合计 = 入库 - 退货。 */
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  downloadTaskFile,
  getTaskDetail,
  isTerminalTaskError,
} from '@/api/task'
import {
  exportPurchaseReport,
  getPurchaseReport,
} from '@/api/purchaseReport'
import type {
  PurchaseReportDetailItem,
  PurchaseReportGroupBy,
  PurchaseReportOut,
  PurchaseReportQuery,
  PurchaseReportRankItem,
  PurchaseReportSummaryItem,
  PurchaseReportView,
} from '@/types/purchaseReport'

const VIEW_OPTIONS: Array<{ label: string; value: PurchaseReportView }> = [
  { label: '明细', value: 'detail' },
  { label: '汇总', value: 'summary' },
  { label: '排行', value: 'rank' },
]

const query = reactive<PurchaseReportQuery>({
  page: 1,
  pageSize: 20,
  view: 'detail',
  groupBy: 'goods',
  startDate: null,
  endDate: null,
  supplierId: null,
  goodsId: null,
  warehouseId: null,
})

const loading = ref(false)
const report = ref<PurchaseReportOut | null>(null)
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
    report.value = await getPurchaseReport(query)
  } catch {
    // 错误由 request 拦截器统一提示
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
  query.view = 'detail'
  query.groupBy = 'goods'
  query.startDate = null
  query.endDate = null
  query.supplierId = null
  query.goodsId = null
  query.warehouseId = null
  void load()
}

const changeView = (view: PurchaseReportView): void => {
  query.view = view
  query.page = 1
  void load()
}

const changeGroupBy = (group: PurchaseReportGroupBy): void => {
  query.groupBy = group
  query.page = 1
  void load()
}

const handlePageChange = (page: number): void => {
  query.page = page
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
        ElMessage.error('当前任务没有可下载文件或无下载权限')
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

const exportReport = async (): Promise<void> => {
  if (exporting.value) return
  try {
    exporting.value = true
    exportProgress.value = 0
    const task = await exportPurchaseReport(query)
    if (disposed) return
    if (!task.taskId) throw new Error('未获取到任务 ID')
    polls = 0
    failures = 0
    void pollExport(task.taskId)
  } catch {
    stopPolling()
  }
}

void load()
</script>

<template>
  <div class="report-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>采购报表</span>
          <div class="header-actions">
            <el-button
              type="primary"
              :loading="exporting"
              :disabled="!report || report.total === 0"
              @click="exportReport"
            >
              {{ exporting ? `${exportProgress}%...` : '导出 Excel' }}
            </el-button>
          </div>
        </div>
      </template>

      <el-form :inline="true" class="filter-form" @submit.prevent="search">
        <el-form-item label="视图">
          <el-radio-group :model-value="query.view" @change="changeView">
            <el-radio-button v-for="option in VIEW_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="query.view === 'summary'" label="汇总维度">
          <el-select
            :model-value="query.groupBy"
            style="width: 120px"
            @change="changeGroupBy"
          >
            <el-option label="按商品" value="goods" />
            <el-option label="按供应商" value="supplier" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期区间">
          <el-date-picker
            v-model="query.startDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="开始日期"
            style="width: 150px"
          />
          <span class="date-separator">~</span>
          <el-date-picker
            v-model="query.endDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="结束日期"
            style="width: 150px"
          />
        </el-form-item>
        <el-form-item label="供应商 ID">
          <el-input-number
            v-model="query.supplierId"
            :min="1"
            controls-position="right"
            placeholder="全部"
            style="width: 130px"
          />
        </el-form-item>
        <el-form-item label="商品 ID">
          <el-input-number
            v-model="query.goodsId"
            :min="1"
            controls-position="right"
            placeholder="全部"
            style="width: 130px"
          />
        </el-form-item>
        <el-form-item label="仓库 ID">
          <el-input-number
            v-model="query.warehouseId"
            :min="1"
            controls-position="right"
            placeholder="全部"
            style="width: 130px"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <div v-if="totals" class="totals">
        <div class="total-item">
          <div class="total-label">入库金额</div>
          <div class="total-value">{{ money(totals.inboundAmount) }}</div>
        </div>
        <div class="total-item">
          <div class="total-label">退货金额</div>
          <div class="total-value return">{{ money(totals.returnAmount) }}</div>
        </div>
        <div class="total-item">
          <div class="total-label">采购净额</div>
          <div class="total-value net">{{ money(totals.netAmount) }}</div>
        </div>
        <div class="total-item">
          <div class="total-label">净数量</div>
          <div class="total-value">{{ qty(totals.netQty) }}</div>
        </div>
        <div class="total-item">
          <div class="total-label">数据行数</div>
          <div class="total-value">{{ totals.rowCount }}</div>
        </div>
      </div>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="口径说明"
        title="口径：仅统计已确认的采购入库与退货；退货以负数行展示，财务账仍由退货单直接冲减原应付，不生成红字单据。"
      />

      <el-table
        v-loading="loading"
        :data="report?.items ?? []"
        border
        stripe
        style="width: 100%; margin-top: 12px"
      >
        <template v-if="query.view === 'detail'">
          <el-table-column label="类型" width="80">
            <template #default="{ row }: { row: PurchaseReportDetailItem }">
              <el-tag :type="row.type === 'inbound' ? 'success' : 'danger'" size="small">
                {{ row.type === 'inbound' ? '入库' : '退货' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="date" label="单据日期" width="120" />
          <el-table-column prop="docNo" label="单据号" width="150" />
          <el-table-column prop="sourceNo" label="来源入库单" width="150" />
          <el-table-column prop="supplierName" label="供应商" min-width="150" />
          <el-table-column prop="goodsCode" label="商品编码" width="120" />
          <el-table-column prop="goodsName" label="商品名称" min-width="150" />
          <el-table-column prop="warehouseName" label="仓库" width="120" />
          <el-table-column label="数量" width="110" align="right">
            <template #default="{ row }: { row: PurchaseReportDetailItem }">
              {{ qty(row.qty) }}
            </template>
          </el-table-column>
          <el-table-column label="单价" width="110" align="right">
            <template #default="{ row }: { row: PurchaseReportDetailItem }">
              {{ money(row.price) }}
            </template>
          </el-table-column>
          <el-table-column label="金额" width="130" align="right">
            <template #default="{ row }: { row: PurchaseReportDetailItem }">
              <span :class="{ 'negative-amount': Number(row.amount) < 0 }">
                {{ money(row.amount) }}
              </span>
            </template>
          </el-table-column>
        </template>

        <template v-else-if="query.view === 'summary'">
          <el-table-column prop="groupId" label="分组 ID" width="100" />
          <el-table-column prop="groupName" label="名称" min-width="180" />
          <el-table-column label="入库数量" width="120" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              {{ qty(row.inboundQty) }}
            </template>
          </el-table-column>
          <el-table-column label="退货数量" width="120" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              {{ qty(row.returnQty) }}
            </template>
          </el-table-column>
          <el-table-column label="净数量" width="120" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              {{ qty(row.netQty) }}
            </template>
          </el-table-column>
          <el-table-column label="入库金额" width="130" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              {{ money(row.inboundAmount) }}
            </template>
          </el-table-column>
          <el-table-column label="退货金额" width="130" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              {{ money(row.returnAmount) }}
            </template>
          </el-table-column>
          <el-table-column label="净额" width="130" align="right">
            <template #default="{ row }: { row: PurchaseReportSummaryItem }">
              <span class="net">{{ money(row.netAmount) }}</span>
            </template>
          </el-table-column>
        </template>

        <template v-else>
          <el-table-column prop="rank" label="排名" width="80" align="center" />
          <el-table-column prop="supplierId" label="供应商 ID" width="110" />
          <el-table-column prop="supplierName" label="供应商" min-width="200" />
          <el-table-column label="净数量" width="130" align="right">
            <template #default="{ row }: { row: PurchaseReportRankItem }">
              {{ qty(row.netQty) }}
            </template>
          </el-table-column>
          <el-table-column label="采购净额" width="150" align="right">
            <template #default="{ row }: { row: PurchaseReportRankItem }">
              <span class="net">{{ money(row.netAmount) }}</span>
            </template>
          </el-table-column>
        </template>
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
          @size-change="(size: number) => { query.pageSize = size; query.page = 1; void load() }"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.report-page { padding: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.header-actions { display: flex; gap: 8px; }
.filter-form { margin-bottom: 4px; }
.date-separator { padding: 0 6px; color: #909399; }
.totals { display: flex; flex-wrap: wrap; gap: 12px; margin: 8px 0 12px; }
.total-item {
  min-width: 150px; padding: 12px 16px; border: 1px solid #ebeef5;
  border-radius: 6px; background: #fafafa;
}
.total-label { font-size: 12px; color: #909399; margin-bottom: 6px; }
.total-value { font-size: 18px; font-weight: 600; color: #303133; }
.total-value.return, .negative-amount { color: #f56c6c; }
.total-value.net, .net { color: #409eff; }
.口径说明 { margin-bottom: 4px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
