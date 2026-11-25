<script setup lang="ts">
/** F-06 对账单（客户/供应商）：生成前预览 → 生成快照 → 列表/明细/导出（快照不可改）。 */
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  downloadTaskFile,
  getTaskDetail,
  isTerminalTaskError,
} from '@/api/task'
import { getCustomerOptions } from '@/api/customer'
import { getSupplierOptions } from '@/api/supplier'
import type { Customer } from '@/types/customer'
import {
  createStatement,
  exportStatement,
  getStatementDetail,
  getStatementList,
  previewStatement,
} from '@/api/statement'
import type { Statement, StatementBuild, StatementItem } from '@/types/statement'
import type { Supplier } from '@/types/supplier'

const query = reactive({
  page: 1,
  pageSize: 20,
  partyType: 2 as number | null,
  partyId: null as number | null,
  periodStart: null as string | null,
  periodEnd: null as string | null,
  startDate: null as string | null,
  endDate: null as string | null,
  statementNo: null as string | null,
})

const draft = reactive({
  partyType: 2 as 1 | 2,
  partyId: null as number | null,
  periodStart: null as string | null,
  periodEnd: null as string | null,
  remark: null as string | null,
})

const loading = ref(false)
const rows = ref<Statement[]>([])
const total = ref(0)
const suppliers = ref<Supplier[]>([])
const customers = ref<Customer[]>([])
const dialogVisible = ref(false)
const previewing = ref(false)
const building = ref(false)
const previewData = ref<StatementBuild | null>(null)
const detailVisible = ref(false)
const detail = ref<Statement | null>(null)
const detailLoading = ref(false)
const exporting = ref(false)
const exportProgress = ref(0)
let exportTimer: ReturnType<typeof setTimeout> | null = null
let exportController: AbortController | null = null
let disposed = false
let polls = 0
let failures = 0
const MAX_POLLS = 150
const MAX_FAILURES = 3

const partyTypeName = (type: number): string => (type === 1 ? '客户' : '供应商')

const partyName = (type: number, id: number): string => {
  const list = type === 1 ? customers.value : suppliers.value
  const found = list.find((item) => item.id === id)
  return found?.name ?? `#${id}`
}

/** 筛选+生成对话框共用的对象选项（随类型切换）。 */
const partyOptions = (type: number): { id: number; name: string }[] =>
  type === 1 ? customers.value : suppliers.value

/** 切换筛选类型时清空对象，避免客户 ID 误作供应商 ID 查询。 */
const handleQueryTypeChange = (): void => {
  query.partyId = null
}

/** 切换生成类型时清空已选对象与预览，防止跨类型误生成。 */
const handleDraftTypeChange = (): void => {
  draft.partyId = null
  previewData.value = null
}

const money = (value: string | number | undefined | null): string => {
  if (value === undefined || value === null || value === '') return '0.00'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

const loadParties = async (): Promise<void> => {
  try {
    const [supplierList, customerList] = await Promise.all([
      getSupplierOptions(),
      getCustomerOptions(),
    ])
    suppliers.value = supplierList
    customers.value = customerList
  } catch {
    // 统一拦截
  }
}

const load = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getStatementList(query)
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
  query.partyType = 2
  query.partyId = null
  query.periodStart = null
  query.periodEnd = null
  query.startDate = null
  query.endDate = null
  query.statementNo = null
  void load()
}

const openCreate = (): void => {
  draft.partyType = 2
  draft.partyId = null
  draft.periodStart = null
  draft.periodEnd = null
  draft.remark = null
  previewData.value = null
  dialogVisible.value = true
}

const doPreview = async (): Promise<void> => {
  if (!draft.partyId || !draft.periodStart || !draft.periodEnd) {
    ElMessage.warning(`请选择${partyTypeName(draft.partyType)}并填写对账期间`)
    return
  }
  previewing.value = true
  try {
    previewData.value = await previewStatement({
      partyType: draft.partyType,
      partyId: draft.partyId,
      periodStart: draft.periodStart,
      periodEnd: draft.periodEnd,
      remark: draft.remark,
    })
  } catch {
    // 统一拦截
  } finally {
    previewing.value = false
  }
}

const doCreate = async (): Promise<void> => {
  if (!draft.partyId || !draft.periodStart || !draft.periodEnd) return
  building.value = true
  try {
    await createStatement({
      partyType: draft.partyType,
      partyId: draft.partyId,
      periodStart: draft.periodStart,
      periodEnd: draft.periodEnd,
      remark: draft.remark,
    })
    ElMessage.success('对账单快照已生成')
    dialogVisible.value = false
    await load()
  } catch {
    // 统一拦截（含 409）
  } finally {
    building.value = false
  }
}

const openDetail = async (id: number): Promise<void> => {
  detailLoading.value = true
  detailVisible.value = true
  try {
    detail.value = await getStatementDetail(id)
  } catch {
    // 统一拦截
  } finally {
    detailLoading.value = false
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

const doExport = async (id: number): Promise<void> => {
  if (exporting.value) return
  try {
    exporting.value = true
    exportProgress.value = 0
    const task = await exportStatement(id)
    if (disposed) return
    if (!task.taskId) throw new Error('未获取到任务 ID')
    polls = 0
    failures = 0
    void pollExport(task.taskId)
  } catch {
    stopPolling()
  }
}

const previewItems = computed<StatementItem[]>(() => previewData.value?.items ?? [])

void loadParties()
void load()
</script>

<template>
  <div class="statement-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>对账单（客户 / 供应商）</span>
          <el-button type="primary" @click="openCreate">生成对账单</el-button>
        </div>
      </template>

      <el-form :inline="true" class="filter-form" @submit.prevent="search">
        <el-form-item label="类型">
          <el-select v-model="query.partyType" clearable placeholder="全部" style="width: 110px" @change="handleQueryTypeChange">
            <el-option label="客户" :value="1" />
            <el-option label="供应商" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item :label="query.partyType === 1 ? '客户' : '对象'">
          <el-select v-model="query.partyId" clearable filterable placeholder="全部" style="width: 200px">
            <el-option v-for="item in partyOptions(query.partyType ?? 2)" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="对账期间">
          <el-date-picker v-model="query.periodStart" type="date" value-format="YYYY-MM-DD" placeholder="起" style="width: 140px" />
          <span class="sep">~</span>
          <el-date-picker v-model="query.periodEnd" type="date" value-format="YYYY-MM-DD" placeholder="止" style="width: 140px" />
        </el-form-item>
        <el-form-item label="对账单号">
          <el-input v-model="query.statementNo" clearable placeholder="精确匹配" style="width: 180px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="statementNo" label="对账单号" width="180" />
        <el-table-column label="往来单位" min-width="160">
          <template #default="{ row }: { row: Statement }">
            <el-tag size="small" class="party-tag">{{ partyTypeName(row.partyType) }}</el-tag>
            {{ partyName(row.partyType, row.partyId) }}
          </template>
        </el-table-column>
        <el-table-column prop="periodStart" label="期间起" width="120" />
        <el-table-column prop="periodEnd" label="期间止" width="120" />
        <el-table-column label="期初" width="120" align="right">
          <template #default="{ row }: { row: Statement }">{{ money(row.beginAmount) }}</template>
        </el-table-column>
        <el-table-column label="本期" width="120" align="right">
          <template #default="{ row }: { row: Statement }">{{ money(row.currentAmount) }}</template>
        </el-table-column>
        <el-table-column label="期末" width="120" align="right">
          <template #default="{ row }: { row: Statement }">
            <span class="net">{{ money(row.endAmount) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }: { row: Statement }">
            <el-button link type="primary" @click="openDetail(row.id)">明细</el-button>
            <el-button link type="primary" :loading="exporting" @click="doExport(row.id)">
              {{ exporting ? `${exportProgress}%` : '导出' }}
            </el-button>
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

    <!-- 生成：先预览再确认，快照不可改 -->
    <el-dialog v-model="dialogVisible" title="生成对账单" width="920px">
      <el-form :inline="true" class="filter-form">
        <el-form-item label="类型">
          <el-select v-model="draft.partyType" style="width: 110px" @change="handleDraftTypeChange">
            <el-option label="客户" :value="1" />
            <el-option label="供应商" :value="2" />
          </el-select>
        </el-form-item>
        <el-form-item :label="draft.partyType === 1 ? '客户' : '供应商'">
          <el-select v-model="draft.partyId" filterable placeholder="请选择" style="width: 220px">
            <el-option v-for="item in partyOptions(draft.partyType)" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="期间">
          <el-date-picker v-model="draft.periodStart" type="date" value-format="YYYY-MM-DD" placeholder="起" style="width: 140px" />
          <span class="sep">~</span>
          <el-date-picker v-model="draft.periodEnd" type="date" value-format="YYYY-MM-DD" placeholder="止" style="width: 140px" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="draft.remark" clearable style="width: 180px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="previewing" @click="doPreview">预览</el-button>
        </el-form-item>
      </el-form>

      <template v-if="previewData">
        <div class="preview-totals">
          <div><span>期初</span><b>{{ money(previewData.beginAmount) }}</b></div>
          <div><span>本期</span><b>{{ money(previewData.currentAmount) }}</b></div>
          <div><span>期末</span><b class="net">{{ money(previewData.endAmount) }}</b></div>
        </div>
        <el-table :data="previewItems" border stripe max-height="360" style="width: 100%">
          <el-table-column prop="bizDate" label="业务日期" width="120" />
          <el-table-column prop="sourceNo" label="来源单号" width="160" />
          <el-table-column prop="summary" label="摘要" min-width="180" />
          <el-table-column label="借方" width="120" align="right">
            <template #default="{ row }: { row: StatementItem }">{{ money(row.debitAmount) }}</template>
          </el-table-column>
          <el-table-column label="贷方" width="120" align="right">
            <template #default="{ row }: { row: StatementItem }">{{ money(row.creditAmount) }}</template>
          </el-table-column>
          <el-table-column label="余额" width="120" align="right">
            <template #default="{ row }: { row: StatementItem }">{{ money(row.balance) }}</template>
          </el-table-column>
        </el-table>
      </template>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!previewData" :loading="building" @click="doCreate">
          确认生成快照
        </el-button>
      </template>
    </el-dialog>

    <!-- 已生成快照明细：只读 -->
    <el-dialog v-model="detailVisible" title="对账单明细（快照）" width="920px">
      <div v-if="detail" class="preview-totals">
        <div><span>对账单号</span><b>{{ detail.statementNo }}</b></div>
        <div><span>期初</span><b>{{ money(detail.beginAmount) }}</b></div>
        <div><span>本期</span><b>{{ money(detail.currentAmount) }}</b></div>
        <div><span>期末</span><b class="net">{{ money(detail.endAmount) }}</b></div>
      </div>
      <el-table v-loading="detailLoading" :data="detail?.items ?? []" border stripe max-height="480" style="width: 100%">
        <el-table-column prop="bizDate" label="业务日期" width="120" />
        <el-table-column prop="sourceNo" label="来源单号" width="160" />
        <el-table-column prop="summary" label="摘要" min-width="180" />
        <el-table-column label="借方" width="120" align="right">
          <template #default="{ row }: { row: StatementItem }">{{ money(row.debitAmount) }}</template>
        </el-table-column>
        <el-table-column label="贷方" width="120" align="right">
          <template #default="{ row }: { row: StatementItem }">{{ money(row.creditAmount) }}</template>
        </el-table-column>
        <el-table-column label="余额" width="120" align="right">
          <template #default="{ row }: { row: StatementItem }">{{ money(row.balance) }}</template>
        </el-table-column>
      </el-table>
      <el-alert
        type="info"
        :closable="false"
        class="hint"
        title="快照生成后不随业务变化；如需反映新单据，请为新的期间重新生成。"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.statement-page { padding: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.filter-form { margin-bottom: 4px; }
.sep { padding: 0 6px; color: #909399; }
.preview-totals { display: flex; gap: 24px; margin: 8px 0 12px; }
.preview-totals div { display: flex; flex-direction: column; gap: 4px; }
.preview-totals span { font-size: 12px; color: #909399; }
.preview-totals b { font-size: 18px; }
.net { color: #409eff; }
.party-tag { margin-right: 6px; }
.hint { margin-top: 12px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
