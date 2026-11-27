<script setup lang="ts">
/** I-02 出入库流水：按类型/方向/来源单号/日期筛选，导出与列表同条件。 */
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { getGoodsList } from '@/api/goods'
import { getFlows } from '@/api/inventory'
import {
  downloadTaskFile,
  exportFlows,
  getTaskDetail,
  isTerminalTaskError,
} from '@/api/task'
import { getWarehouseList } from '@/api/warehouse'
import type { FlowItem, FlowQuery, FlowType } from '@/types/inventory'
import type { Goods } from '@/types/goods'
import type { Warehouse } from '@/types/warehouse'

const FLOW_TYPES: Array<{ label: string; value: FlowType }> = [
  { label: '采购入库', value: 'purchase_in' },
  { label: '采购退货', value: 'purchase_return' },
  { label: '销售出库', value: 'sale_out' },
  { label: '销售退货', value: 'sale_return' },
  { label: '调拨入库', value: 'transfer_in' },
  { label: '调拨出库', value: 'transfer_out' },
  { label: '盘盈', value: 'check_gain' },
  { label: '盘亏', value: 'check_loss' },
]

const query = reactive<FlowQuery>({
  page: 1,
  pageSize: 20,
  keyword: null,
  warehouseId: null,
  goodsId: null,
  flowType: null,
  direction: null,
  sourceNo: null,
  startDate: null,
  endDate: null,
})
const dateRange = ref<[string, string] | null>(null)
const rows = ref<FlowItem[]>([])
const total = ref(0)
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
    const result = await getFlows(query)
    rows.value = result.items
    total.value = result.total
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
  query.flowType = null
  query.direction = null
  query.sourceNo = null
  dateRange.value = null
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
    const task = await exportFlows({
      keyword: query.keyword,
      warehouseId: query.warehouseId,
      goodsId: query.goodsId,
      flowType: query.flowType,
      direction: query.direction,
      sourceNo: query.sourceNo,
      startDate: query.startDate,
      endDate: query.endDate,
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
  <div class="flow-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>出入库流水</span>
          <el-button type="primary" :loading="exporting" @click="doExport">
            {{ exporting ? `导出中 ${exportProgress}%` : '导出流水' }}
          </el-button>
        </div>
      </template>

      <el-form :inline="true" class="filter-form" @submit.prevent="search">
        <el-form-item label="关键字">
          <el-input v-model="query.keyword" clearable placeholder="商品编码/名称" style="width: 170px" />
        </el-form-item>
        <el-form-item label="仓库">
          <el-select v-model="query.warehouseId" clearable filterable placeholder="全部" style="width: 150px">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="商品">
          <el-select v-model="query.goodsId" clearable filterable placeholder="全部" style="width: 170px">
            <el-option v-for="g in goods" :key="g.id" :label="`${g.code} ${g.name}`" :value="g.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="query.flowType" clearable placeholder="全部" style="width: 150px">
            <el-option v-for="item in FLOW_TYPES" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="方向">
          <el-select v-model="query.direction" clearable placeholder="全部" style="width: 100px">
            <el-option label="入库" :value="1" />
            <el-option label="出库" :value="-1" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源单号">
          <el-input v-model="query.sourceNo" clearable placeholder="精确匹配" style="width: 150px" />
        </el-form-item>
        <el-form-item label="业务时间">
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
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="bizTime" label="业务时间" width="170" />
        <el-table-column label="类型" width="120">
          <template #default="{ row }: { row: FlowItem }">
            {{ FLOW_TYPES.find((item) => item.value === row.flowType)?.label ?? row.flowType }}
          </template>
        </el-table-column>
        <el-table-column label="方向" width="80">
          <template #default="{ row }: { row: FlowItem }">
            <el-tag :type="row.direction === 1 ? 'success' : 'danger'" size="small">
              {{ row.direction === 1 ? '入库' : '出库' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="warehouseName" label="仓库" width="120" />
        <el-table-column prop="goodsCode" label="商品编码" width="120" />
        <el-table-column prop="goodsName" label="商品名称" min-width="140" />
        <el-table-column label="数量" width="100" align="right">
          <template #default="{ row }: { row: FlowItem }">{{ qty(row.qty) }}</template>
        </el-table-column>
        <el-table-column label="变动前/后" width="140" align="right">
          <template #default="{ row }: { row: FlowItem }">
            {{ qty(row.beforeQty) }} → {{ qty(row.afterQty) }}
          </template>
        </el-table-column>
        <el-table-column label="单位成本" width="110" align="right">
          <template #default="{ row }: { row: FlowItem }">{{ money(row.unitCost) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="120" align="right">
          <template #default="{ row }: { row: FlowItem }">{{ money(row.amount) }}</template>
        </el-table-column>
        <el-table-column prop="sourceNo" label="来源单号" width="160" />
        <el-table-column prop="remark" label="备注" min-width="150" />
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
  </div>
</template>

<style scoped>
.flow-page { padding: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.filter-form { flex-wrap: wrap; gap: 4px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
