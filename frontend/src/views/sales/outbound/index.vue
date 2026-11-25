<script setup lang="ts">
/** SA-03 销售出库：列表 + 按销售订单预填生成草稿（锁库存）+ 确认/删除。 */
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { getCustomerOptions } from '@/api/customer'
import { getWarehouseList } from '@/api/warehouse'
import {
  confirmOutbound,
  createOutbound,
  deleteOutbound,
  getOutbound,
  getOutboundFromOrder,
  getOutbounds,
} from '@/api/salesOutbound'
import type { Customer } from '@/types/customer'
import type { Warehouse, WarehouseQuery } from '@/types/warehouse'
import type {
  Outbound,
  OutboundFromOrder,
  OutboundFromOrderItem,
  OutboundQuery,
} from '@/types/salesOutbound'

const query = reactive<OutboundQuery>({
  page: 1,
  pageSize: 20,
  status: null,
  customerId: null,
  warehouseId: null,
  startDate: null,
  endDate: null,
  keyword: null,
})

const rows = ref<Outbound[]>([])
const total = ref(0)
const loading = ref(false)
const customers = ref<Customer[]>([])
const warehouses = ref<Warehouse[]>([])

const dialogVisible = ref(false)
const orderId = ref<number | null>(null)
const warehouseId = ref<number | null>(null)
const outboundDate = ref<string>(new Date().toISOString().slice(0, 10))
const remark = ref<string>('')
const prefetched = ref<OutboundFromOrder | null>(null)
type DraftItem = Omit<OutboundFromOrderItem, 'qty'> & { qty: number }
const draftItems = ref<DraftItem[]>([])
const prefetching = ref(false)
const saving = ref(false)
const detailVisible = ref(false)
const detail = ref<Outbound | null>(null)
const confirmBusy = ref<Set<number>>(new Set())

const num = (value: string | number | undefined | null): string => {
  if (value === undefined || value === null || value === '') return '0'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}
const money = (value: string | number | undefined | null): string => {
  if (value === undefined || value === null || value === '') return '0.00'
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

const loadBase = async (): Promise<void> => {
  try {
    customers.value = await getCustomerOptions()
    const wh = await getWarehouseList({ page: 1, pageSize: 200 } as WarehouseQuery)
    warehouses.value = wh.items
  } catch {
    // 统一拦截
  }
}

const load = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getOutbounds(query)
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
  query.status = null
  query.customerId = null
  query.warehouseId = null
  query.startDate = null
  query.endDate = null
  query.keyword = null
  void load()
}

const openCreate = (): void => {
  orderId.value = null
  warehouseId.value = null
  outboundDate.value = new Date().toISOString().slice(0, 10)
  remark.value = ''
  prefetched.value = null
  draftItems.value = []
  dialogVisible.value = true
}

const doPrefetch = async (): Promise<void> => {
  if (!orderId.value) {
    ElMessage.warning('请先填写销售订单 ID')
    return
  }
  prefetching.value = true
  try {
    prefetched.value = await getOutboundFromOrder(orderId.value)
    draftItems.value = prefetched.value.items.map((item) => ({
      ...item,
      qty: Number(item.remainingQty),
    }))
  } catch {
    // 统一拦截
  } finally {
    prefetching.value = false
  }
}

const doCreate = async (): Promise<void> => {
  if (!prefetched.value || !warehouseId.value) {
    ElMessage.warning('请先按订单预填并选择出库仓库')
    return
  }
  const items = draftItems.value
    .filter((item) => Number(item.remainingQty) > 0)
    .map((item) => ({ orderItemId: item.orderItemId, qty: item.qty }))
  if (items.length === 0) {
    ElMessage.warning('没有可出库的明细行')
    return
  }
  saving.value = true
  try {
    await createOutbound({
      orderId: prefetched.value.orderId,
      warehouseId: warehouseId.value,
      outboundDate: outboundDate.value,
      remark: remark.value || null,
      items,
    })
    ElMessage.success('出库草稿已创建，库存已锁定')
    dialogVisible.value = false
    await load()
  } catch {
    // 统一拦截
  } finally {
    saving.value = false
  }
}

const openDetail = async (id: number): Promise<void> => {
  detailVisible.value = true
  detail.value = null
  try {
    detail.value = await getOutbound(id)
  } catch {
    // 统一拦截
  }
}

const doConfirm = async (row: Outbound): Promise<void> => {
  confirmBusy.value = new Set([...confirmBusy.value, row.id])
  try {
    await confirmOutbound(row.id)
    ElMessage.success('出库已确认，库存/订单/应收已同步')
    await load()
  } catch {
    // 统一拦截
  } finally {
    const next = new Set(confirmBusy.value)
    next.delete(row.id)
    confirmBusy.value = next
  }
}

const doDelete = async (row: Outbound): Promise<void> => {
  try {
    await ElMessageBox.confirm(`确认删除出库草稿 ${row.outboundNo}？将释放锁定库存。`, '提示', {
      type: 'warning',
    })
    await deleteOutbound(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // 用户取消或请求失败，统一忽略
  }
}

void loadBase()
void load()
</script>

<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="header">
          <span>销售出库</span>
          <el-button type="primary" @click="openCreate">新增出库</el-button>
        </div>
      </template>

      <el-form :inline="true" @submit.prevent="search">
        <el-form-item label="客户">
          <el-select v-model="query.customerId" clearable filterable placeholder="全部" style="width: 180px">
            <el-option v-for="c in customers" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="query.status" clearable placeholder="全部" style="width: 120px">
            <el-option label="草稿" :value="0" />
            <el-option label="已确认" :value="1" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="query.startDate" type="date" value-format="YYYY-MM-DD" placeholder="起" style="width: 140px" />
          <span class="sep">~</span>
          <el-date-picker v-model="query.endDate" type="date" value-format="YYYY-MM-DD" placeholder="止" style="width: 140px" />
        </el-form-item>
        <el-form-item label="关键字">
          <el-input v-model="query.keyword" clearable placeholder="单号/备注" style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">查询</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table v-loading="loading" :data="rows" border stripe style="width: 100%">
        <el-table-column prop="outboundNo" label="出库单号" width="170" />
        <el-table-column prop="orderNo" label="来源订单" width="150" />
        <el-table-column prop="customerName" label="客户" min-width="150" />
        <el-table-column prop="warehouseName" label="仓库" width="130" />
        <el-table-column prop="outboundDate" label="出库日期" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }: { row: Outbound }">
            <el-tag :type="row.status === 1 ? 'success' : 'warning'" size="small">
              {{ row.status === 1 ? '已确认' : '草稿' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }: { row: Outbound }">{{ money(row.totalAmount) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }: { row: Outbound }">
            <el-button link type="primary" @click="openDetail(row.id)">明细</el-button>
            <el-button
              v-if="row.status === 0"
              link
              type="primary"
              :loading="confirmBusy.has(row.id)"
              @click="doConfirm(row)"
            >确认</el-button>
            <el-button v-if="row.status === 0" link type="danger" @click="doDelete(row)">删除</el-button>
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
          @current-change="(p: number) => { query.page = p; void load() }"
          @size-change="(s: number) => { query.pageSize = s; query.page = 1; void load() }"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" title="新增销售出库（保存即锁定库存）" width="900px">
      <el-form :inline="true">
        <el-form-item label="销售订单 ID">
          <el-input-number v-model="orderId" :min="1" controls-position="right" style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button :loading="prefetching" @click="doPrefetch">按订单预填</el-button>
        </el-form-item>
        <el-form-item label="出库仓库">
          <el-select v-model="warehouseId" filterable placeholder="请选择" style="width: 180px">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="出库日期">
          <el-date-picker v-model="outboundDate" type="date" value-format="YYYY-MM-DD" style="width: 150px" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="remark" style="width: 160px" />
        </el-form-item>
      </el-form>

      <template v-if="prefetched">
        <el-alert type="info" :closable="false" class="hint"
          :title="`订单 ${prefetched.orderNo}： quantity 为本次出库数量，默认带出剩余可出库量，可修改（不得超过可退量）`" />
        <el-table :data="draftItems" border style="width: 100%">
          <el-table-column prop="goodsCode" label="编码" width="120" />
          <el-table-column prop="goodsName" label="商品" min-width="160" />
          <el-table-column label="订购" width="100" align="right">
            <template #default="{ row }: { row: DraftItem }">{{ num(row.qty) }}</template>
          </el-table-column>
          <el-table-column label="已出库" width="100" align="right">
            <template #default="{ row }: { row: DraftItem }">{{ num(row.outboundQty) }}</template>
          </el-table-column>
          <el-table-column label="可出库" width="100" align="right">
            <template #default="{ row }: { row: DraftItem }">{{ num(row.remainingQty) }}</template>
          </el-table-column>
          <el-table-column label="含税单价" width="110" align="right">
            <template #default="{ row }: { row: DraftItem }">{{ money(row.price) }}</template>
          </el-table-column>
          <el-table-column label="本次出库数量" width="150">
            <template #default="{ row }: { row: DraftItem }">
              <el-input-number v-model="row.qty" :min="0" :max="Number(row.remainingQty)" :precision="4" size="small" style="width: 130px" />
            </template>
          </el-table-column>
        </el-table>
      </template>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!prefetched" :loading="saving" @click="doCreate">
          保存草稿并锁库存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailVisible" title="销售出库单明细" width="860px">
      <div v-if="detail" class="detail-head">
        <span>{{ detail.outboundNo }}</span>
        <span>{{ detail.customerName }} · {{ detail.warehouseName }}</span>
        <span>金额 {{ money(detail.totalAmount) }}</span>
      </div>
      <el-table :data="detail?.items ?? []" border style="width: 100%">
        <el-table-column prop="goodsName" label="商品" min-width="180" />
        <el-table-column label="数量" width="120" align="right">
          <template #default="{ row }: { row: Outbound['items'][number] }">{{ num(row.qty) }}</template>
        </el-table-column>
        <el-table-column label="单价" width="120" align="right">
          <template #default="{ row }: { row: Outbound['items'][number] }">{{ money(row.price) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="130" align="right">
          <template #default="{ row }: { row: Outbound['items'][number] }">{{ money(row.amount) }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 16px; }
.header { display: flex; justify-content: space-between; align-items: center; }
.sep { padding: 0 6px; color: #909399; }
.hint { margin: 8px 0; }
.detail-head { display: flex; gap: 24px; margin-bottom: 12px; font-weight: 600; }
.pagination { display: flex; justify-content: flex-end; margin-top: 16px; }
</style>
