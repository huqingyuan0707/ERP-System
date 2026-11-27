<script setup lang="ts">
/** I-01 实时库存：筛选/汇总/导出/低库存高亮，详情抽屉展示最近流水。 */
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getGoodsList } from '@/api/goods'
import { getFlows, getStocks, getStockSummary } from '@/api/inventory'
import {
  downloadTaskFile,
  exportStocks,
  getTaskDetail,
  isTerminalTaskError,
} from '@/api/task'
import { getWarehouseList } from '@/api/warehouse'
import type { FlowItem, FlowQuery, StockItem, StockQuery, StockSummary } from '@/types/inventory'
import type { Goods } from '@/types/goods'
import type { Warehouse } from '@/types/warehouse'

const query = reactive<StockQuery>({
  page: 1,
  pageSize: 20,
  keyword: null,
  warehouseId: null,
  goodsId: null,
  onlyAvailable: false,
  belowLower: false,
})
const rows = ref<StockItem[]>([])
const total = ref(0)
const summary = ref<StockSummary | null>(null)
const warehouses = ref<Warehouse[]>([])
const goods = ref<Goods[]>([])
const loading = ref(false)
const exporting = ref(false)
const exportProgress = ref(0)
let exportTimer: ReturnType<typeof setTimeout> | null = null
let exportController: AbortController | null = null
let disposed = false
let polls = 0
let failures = 0
const MAX_POLLS = 150
const MAX_FAILURES = 3

const flowDrawerVisible = ref(false)
const flowLoading = ref(false)
const flowRows = ref<FlowItem[]>([])
const activeStock = ref<StockItem | null>(null)

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
const warehouseName = (id: number): string =>
  warehouses.value.find((item) => item.id === id)?.name ?? `#${id}`
const goodsName = (id: number): string =>
  goods.value.find((item) => item.id === id)?.name ?? `#${id}`

const load = async (): Promise<void> => {
  loading.value = true
  try {
    const [list, aggregation] = await Promise.all([getStocks(query), getStockSummary(query)])
    rows.value = list.items
    total.value = list.total
    summary.value = aggregation
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
  query.keyword = null
  query.warehouseId = null
  query.goodsId = null
  query.onlyAvailable = false
  query.belowLower = false
  void load()
}

const openFlows = async (row: StockItem): Promise<void> => {
  activeStock.value = row
  flowDrawerVisible.value = true
  flowLoading.value = true
  try {
    const flowQuery: FlowQuery = {
      page: 1,
      pageSize: 20,
      keyword: null,
      warehouseId: row.warehouseId,
      goodsId: row.goodsId,
      flowType: null,
      direction: null,
      sourceNo: null,
      startDate: null,
      endDate: null,
    }
    flowRows.value = (await getFlows(flowQuery)).items
  } catch {
    // 统一拦截
  } finally {
    flowLoading.value = false
  }
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
    const task = await exportStocks({
      keyword: query.keyword,
      warehouseId: query.warehouseId,
      goodsId: query.goodsId,
      onlyAvailable: query.onlyAvailable,
      belowLower: query.belowLower,
    })
    if (disposed) return
    if (!task.taskId) throw new Error('未获取到任务 ID')
    polls = 0
    failures = 0
    void pollExport(task.taskId)
  } catch {
    stopPolling()
  }
}

onMounted(async () => {
  try {
    const [warehouseList, goodsList] = await Promise.all([
      getWarehouseList({ page: 1, pageSize: 200 }),
      getGoodsList({ page: 1, pageSize: 200 }),
    ])
    warehouses.value = warehouseList.items
    goods.value = goodsList.items
  } catch {
    // 统一拦截
  }
  await load()
})
</script>

<template>
  <div class="stock-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>实时库存</span>
          <el-button type="primary" :loading="exporting" @click="doExport">
            {{ exporting ? `导出中 ${exportProgress}%` : '导出库存' }}
          </el-button>
        </div>
      </template>

      <div v-if="summary" class="summary-grid">
        <div><span>账面数量</span><b>{{ qty(summary.totalQty) }}</b></div>
        <div><span>锁定数量</span><b>{{ qty(summary.totalLockedQty) }}</b></div>
        <div><span>可用数量</span><b>{{ qty(summary.totalAvailableQty) }}</b></div>
        <div><span>库存金额</span><b>¥{{ money(summary.totalStockAmount) }}</b></div>
        <div><span>行数</span><b>{{ summary.lineCount }}</b></div>
      </div>

      <el-form :inline="true" class="filter-form" @submit.prevent="search">
        <el-form-item label="关键字">
          <el-input v-model="query.keyword" clearable placeholder="商品编码/名称/条码" style="width: 200px" />
        </el-form-item>
        <el-form-item label="仓库">
          <el-select v-model="query.warehouseId" clearable filterable placeholder="全部" style="width: 160px">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="商品">
          <el-select v-model="query.goodsId" clearable filterable placeholder="全部" style="width: 180px">
            <el-option v-for="g in goods" :key="g.id" :label="`${g.code} ${g.name}`" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="仅可用">
          <el-switch v-model="query.onlyAvailable" />
        </el-form-item>
        <el-form-item label="仅预警">
          <el-switch v-model="query.belowLower" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="warehouseName" label="仓库" width="130" />
        <el-table-column prop="goodsCode" label="商品编码" width="120" />
        <el-table-column prop="goodsName" label="商品名称" min-width="150" />
        <el-table-column prop="goodsSpec" label="规格" width="110" />
        <el-table-column prop="unitName" label="单位" width="80" />
        <el-table-column label="账面" width="110" align="right">
          <template #default="{ row }: { row: StockItem }">{{ qty(row.qty) }}</template>
        </el-table-column>
        <el-table-column label="锁定" width="110" align="right">
          <template #default="{ row }: { row: StockItem }">{{ qty(row.lockedQty) }}</template>
        </el-table-column>
        <el-table-column label="可用" width="110" align="right">
          <template #default="{ row }: { row: StockItem }">
            <span :class="row.isBelowLower ? 'danger' : ''">{{ qty(row.availableQty) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="平均成本" width="110" align="right">
          <template #default="{ row }: { row: StockItem }">{{ money(row.avgCost) }}</template>
        </el-table-column>
        <el-table-column label="库存金额" width="130" align="right">
          <template #default="{ row }: { row: StockItem }">{{ money(row.stockAmount) }}</template>
        </el-table-column>
        <el-table-column label="下限/上限" width="130" align="right">
          <template #default="{ row }: { row: StockItem }">
            {{ qty(row.stockLower) }} / {{ qty(row.stockUpper) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }: { row: StockItem }">
            <el-button link type="primary" @click="openFlows(row)">流水</el-button>
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
          @current-change="(page: number) => { query.page = page; void load() }"
          @size-change="(size: number) => { query.pageSize = size; query.page = 1; void load() }"
        />
      </div>
    </el-card>

    <el-drawer v-model="flowDrawerVisible" title="最近出入库流水" size="60%">
      <div v-if="activeStock" class="drawer-meta">
        {{ warehouseName(activeStock.warehouseId) }} · {{ goodsName(activeStock.goodsId) }}
      </div>
      <el-table v-loading="flowLoading" :data="flowRows" border stripe style="width: 100%">
        <el-table-column prop="bizTime" label="业务时间" width="170" />
        <el-table-column prop="flowType" label="类型" width="130" />
        <el-table-column label="方向" width="80">
          <template #default="{ row }: { row: FlowItem }">
            {{ row.direction === 1 ? '入库' : '出库' }}
          </template>
        </el-table-column>
        <el-table-column label="数量" width="100" align="right">
          <template #default="{ row }: { row: FlowItem }">{{ qty(row.qty) }}</template>
        </el-table-column>
        <el-table-column label="变动前/后" width="140" align="right">
          <template #default="{ row }: { row: FlowItem }">
            {{ qty(row.beforeQty) }} → {{ qty(row.afterQty) }}
          </template>
        </el-table-column>
        <el-table-column prop="sourceNo" label="来源单号" min-width="150" />
      </el-table>
    </el-drawer>
  </div>
</template>

<style scoped>
.stock-page { padding: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.summary-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.summary-grid div { flex: 1 1 130px; border: 1px solid #ebeef5; border-radius: 4px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px; }
.summary-grid span { font-size: 12px; color: #909399; }
.summary-grid b { font-size: 18px; }
.filter-form { flex-wrap: wrap; gap: 4px; }
.danger { color: #ef4444; font-weight: 600; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
.drawer-meta { margin-bottom: 12px; color: #606266; }
</style>
