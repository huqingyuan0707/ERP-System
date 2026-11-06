<script setup lang="ts">
/**
 * 采购申请页（P-01）。
 * 结构：查询条件 + 申请单表格/分页 + 新增/编辑抽屉（明细行）+ 详情/打印抽屉 + 审核弹窗。
 * 与后端保持一致：
 *   - 新建即草稿；提交后进入待审，待审/已审不可编辑（409），需先驳回；
 *   - 通过 → 已审（仅作采购参考，V1 不自动转采购订单）；驳回 → 草稿并显示驳回原因；
 *   - 部门/申请人默认当前登录用户；明细校验商品启用、数量 > 0、需求日期必填；
 *   - 采购申请不写库存、不写财务，列表按状态/部门/需求日期筛选并受数据范围约束。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { getDeptTree } from '@/api/dept'
import { getGoodsList } from '@/api/goods'
import {
  auditPurApply,
  createPurApply,
  deletePurApply,
  getPurApplyDetail,
  getPurApplyList,
  submitPurApply,
  updatePurApply,
} from '@/api/purchaseApply'
import BaseCard from '@/components/BaseCard.vue'
import { useUserStore } from '@/stores/user'
import type { Dept } from '@/types/dept'
import type { Goods } from '@/types/goods'
import {
  APPLY_STATUS,
  APPLY_STATUS_LABEL,
  APPLY_STATUS_TAG,
  type PurApply,
  type PurApplyItemPayload,
  type PurApplyQuery,
} from '@/types/purchaseApply'

const userStore = useUserStore()

// ---------- 列表与筛选 ----------

const loading = ref(false)
const rows = ref<PurApply[]>([])
const total = ref(0)
const query = reactive<PurApplyQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  deptId: null,
  needDateStart: null,
  needDateEnd: null,
})

/** 需求日期区间：绑定一个数组，提交时拆成 start/end 两个参数。 */
const dateRange = ref<[string, string] | null>(null)

/** 部门下拉数据：平铺后按 sort 排序，展示「上级 / 下级」路径便于区分同名部门。 */
const depts = ref<Dept[]>([])
const deptNameMap = computed<Record<number, string>>(() => {
  const map: Record<number, string> = {}
  for (const dept of depts.value) map[dept.id] = dept.name
  return map
})

/** 商品候选：用于明细行选择商品（只取启用商品）。 */
const goodsOptions = ref<Goods[]>([])

const fetchDepts = async (): Promise<void> => {
  depts.value = await getDeptTree()
}

const fetchGoodsOptions = async (): Promise<void> => {
  // 采购申请只允许选启用商品；后端 pageSize 上限为 100，分页拉全避免漏掉可选商品。
  const pageSize = 100
  const options: Goods[] = []
  for (let page = 1; page <= 100; page += 1) {
    const result = await getGoodsList({ page, pageSize, status: 1 })
    options.push(...result.items)
    if (result.items.length < pageSize || options.length >= result.total) break
  }
  goodsOptions.value = options
}

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    query.needDateStart = dateRange.value?.[0] ?? null
    query.needDateEnd = dateRange.value?.[1] ?? null
    const result = await getPurApplyList(query)
    rows.value = result.items
    total.value = result.total
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
  query.status = null
  query.deptId = null
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

/** 状态标签文案：统一走常量，避免各页面各自硬编码。 */
const statusLabel = (status: number): string => APPLY_STATUS_LABEL[status] ?? '未知'
const statusTag = (status: number): 'info' | 'warning' | 'success' =>
  APPLY_STATUS_TAG[status] ?? 'info'

// ---------- 新增 / 编辑抽屉 ----------

interface ApplyItemForm {
  /** 行内唯一键：Element Plus 表格需要稳定 key，不使用商品 ID（同一商品只允许一行）。 */
  key: number
  goodsId: number | null
  qty: string
  needDate: string
}

interface ApplyForm {
  id: number | null
  deptId: number | null
  needDate: string
  remark: string
  items: ApplyItemForm[]
}

const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()

let itemKeySeed = 0

/** 明细行自增 key，仅用于表格渲染，不提交给后端。 */
const nextItemKey = (): number => {
  itemKeySeed += 1
  return itemKeySeed
}

// 必须放在 itemKeySeed / nextItemKey 之后：createEmptyForm() 会同步调用 nextItemKey()，
// 若前置则命中 let/const 的暂时性死区（TDZ），setup 阶段抛 ReferenceError 导致页面白屏。
const form = reactive<ApplyForm>(createEmptyForm())

function createEmptyItem(needDate = ''): ApplyItemForm {
  return { key: nextItemKey(), goodsId: null, qty: '1', needDate }
}

function createEmptyForm(): ApplyForm {
  const today = new Date().toISOString().slice(0, 10)
  return {
    id: null,
    // 申请部门默认当前用户部门（后端在未传时同样兜底取 sys_user.dept_id）
    deptId: userStore.user?.deptId ?? null,
    needDate: today,
    remark: '',
    items: [createEmptyItem(today)],
  }
}

const formRules: FormRules = {
  deptId: [{ required: true, message: '请选择申请部门', trigger: 'change' }],
  needDate: [{ required: true, message: '请选择需求日期', trigger: 'change' }],
}

/** 打开新增抽屉：部门/申请人默认当前用户，明细默认一行。 */
const openCreate = async (): Promise<void> => {
  drawerMode.value = 'create'
  Object.assign(form, createEmptyForm())
  // 当前用户部门由 /auth/me 未下发时，改由后端兜底：这里保持 null 也能提交成功
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (goodsOptions.value.length === 0) await fetchGoodsOptions()
}

/** 编辑草稿：拉取详情后回填（详情接口返回完整明细）。 */
const openEdit = async (row: PurApply): Promise<void> => {
  const detail = await getPurApplyDetail(row.id)
  drawerMode.value = 'edit'
  Object.assign(form, {
    id: detail.id,
    deptId: detail.deptId,
    needDate: detail.needDate ?? '',
    remark: detail.remark ?? '',
    items: detail.items.map((item) => ({
      key: nextItemKey(),
      goodsId: item.goodsId,
      qty: String(item.qty),
      needDate: item.needDate ?? '',
    })),
  })
  if (form.items.length === 0) form.items.push(createEmptyItem(form.needDate))
  drawerVisible.value = true
  formRef.value?.clearValidate()
  if (goodsOptions.value.length === 0) await fetchGoodsOptions()
}

const handleAddItem = (): void => {
  form.items.push(createEmptyItem(form.needDate))
}

const handleRemoveItem = (index: number): void => {
  if (form.items.length <= 1) {
    ElMessage.warning('至少保留一行明细')
    return
  }
  form.items.splice(index, 1)
}

/** 商品下拉变化：回填行级需求日期，避免用户逐行重复填写。 */
const handleGoodsChange = (item: ApplyItemForm): void => {
  if (!item.needDate) item.needDate = form.needDate
}

/** 明细校验：商品必选、数量 > 0、需求日期必填，同一商品不得重复。 */
const validateItems = (): string | null => {
  if (form.items.length === 0) return '至少需要一行明细'
  const used = new Set<number>()
  for (const [index, item] of form.items.entries()) {
    if (item.goodsId === null) return `第 ${index + 1} 行未选择商品`
    if (used.has(item.goodsId)) return '同一商品不能重复录入多行，请合并数量'
    used.add(item.goodsId)
    const qty = Number(item.qty)
    if (!Number.isFinite(qty) || qty <= 0) return `第 ${index + 1} 行数量必须大于 0`
    if (!item.needDate) return `第 ${index + 1} 行请选择需求日期`
  }
  return null
}

/** 提交表单：新增走 POST，编辑走 PUT（仅草稿可编辑，后端会再校验一次）。 */
const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  const itemError = validateItems()
  if (itemError) {
    ElMessage.warning(itemError)
    return
  }
  submitting.value = true
  try {
    const items: PurApplyItemPayload[] = form.items.map((item) => ({
      goodsId: Number(item.goodsId),
      qty: item.qty,
      needDate: item.needDate || null,
    }))
    if (drawerMode.value === 'create') {
      await createPurApply(
        {
          deptId: form.deptId,
          needDate: form.needDate,
          remark: form.remark.trim() || null,
          items,
        },
        `pur-apply-create-${Date.now()}`,
      )
      ElMessage.success('已保存为草稿')
    } else if (form.id !== null) {
      await updatePurApply(
        form.id,
        {
          deptId: form.deptId,
          needDate: form.needDate,
          remark: form.remark.trim() || null,
          items,
        },
        `pur-apply-update-${form.id}-${Date.now()}`,
      )
      ElMessage.success('草稿已更新')
    }
    drawerVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

// ---------- 提交 / 删除 / 审核 ----------

/** 提交草稿：草稿 → 待审；后端会复核商品启用与数量。 */
const handleSubmitApply = async (row: PurApply): Promise<void> => {
  await ElMessageBox.confirm(
    `确定提交申请单「${row.applyNo}」吗？提交后不可再编辑。`,
    '提交确认',
    { type: 'warning' },
  )
  await submitPurApply(row.id, `pur-apply-submit-${row.id}-${Date.now()}`)
  ElMessage.success('已提交，等待审核')
  await fetchList()
}

/** 删除草稿：仅草稿可删，后端为软删除并保留明细流水。 */
const handleDelete = async (row: PurApply): Promise<void> => {
  await ElMessageBox.confirm(`确定删除草稿「${row.applyNo}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deletePurApply(row.id, `pur-apply-delete-${row.id}-${Date.now()}`)
  ElMessage.success('删除成功')
  await fetchList()
}

const auditVisible = ref(false)
const auditSubmitting = ref(false)
const auditTarget = ref<PurApply | null>(null)
const auditForm = reactive<{ result: 'approved' | 'rejected'; remark: string }>({
  result: 'approved',
  remark: '',
})

/** 打开审核弹窗：默认「通过」，选择驳回时原因必填。 */
const openAudit = (row: PurApply): void => {
  auditTarget.value = row
  auditForm.result = 'approved'
  auditForm.remark = ''
  auditVisible.value = true
}

/** 执行审核：通过 → 已审；驳回 → 草稿并记录驳回原因（前端先拦截空原因）。 */
const handleAudit = async (): Promise<void> => {
  if (!auditTarget.value) return
  if (auditForm.result === 'rejected' && !auditForm.remark.trim()) {
    ElMessage.warning('驳回必须填写驳回原因')
    return
  }
  auditSubmitting.value = true
  try {
    await auditPurApply(
      auditTarget.value.id,
      { result: auditForm.result, remark: auditForm.remark.trim() || null },
      `pur-apply-audit-${auditTarget.value.id}-${Date.now()}`,
    )
    ElMessage.success(auditForm.result === 'approved' ? '审核通过' : '已驳回，单据回到草稿')
    auditVisible.value = false
    await fetchList()
  } finally {
    auditSubmitting.value = false
  }
}

// ---------- 详情 / 打印 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<PurApply | null>(null)

/** 打开详情抽屉：拉取完整明细，供查看与打印。 */
const openDetail = async (row: PurApply): Promise<void> => {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getPurApplyDetail(row.id)
  } finally {
    detailLoading.value = false
  }
}

/** 打印：只打印抽屉内的打印区（抬头 + 明细 + 审核意见），不打印页面其它元素。 */
const handlePrint = (): void => {
  window.print()
}

/** 数量展示：去掉多余的 0，例如 2.5000 → 2.5。 */
const formatQty = (value: string | number | null | undefined): string => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? String(Number(num.toFixed(4))) : '0'
}

onMounted(() => {
  void Promise.all([fetchList(), fetchDepts(), fetchGoodsOptions()])
})
</script>

<template>
  <div class="page-container apply-page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="按状态、申请部门与需求日期筛选采购申请；数据范围由角色决定（本人/本部门/全部）。"
        >
          <el-form class="query-form" label-width="80px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="单号 / 备注"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable>
                    <el-option label="草稿" :value="APPLY_STATUS.DRAFT" />
                    <el-option label="待审" :value="APPLY_STATUS.PENDING" />
                    <el-option label="已审" :value="APPLY_STATUS.APPROVED" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="申请部门">
                  <el-select v-model="query.deptId" placeholder="全部" clearable filterable>
                    <el-option
                      v-for="dept in depts"
                      :key="dept.id"
                      :label="deptNameMap[dept.id]"
                      :value="dept.id"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="需求日期">
                  <el-date-picker
                    v-model="dateRange"
                    type="daterange"
                    value-format="YYYY-MM-DD"
                    start-placeholder="开始日期"
                    end-placeholder="结束日期"
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <div class="query-actions">
              <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
              <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
              <el-button v-perm="'pur:apply:add'" type="primary" :icon="'Plus'" @click="openCreate">
                新增申请
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="申请单列表"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="applyNo" label="申请单号" width="180" />
            <el-table-column label="申请部门" width="150">
              <template #default="{ row }">{{ row.deptName || '-' }}</template>
            </el-table-column>
            <el-table-column label="申请人" width="120">
              <template #default="{ row }">{{ row.applicantName || '-' }}</template>
            </el-table-column>
            <el-table-column label="需求日期" width="120" align="center">
              <template #default="{ row }">{{ row.needDate || '-' }}</template>
            </el-table-column>
            <el-table-column label="明细行数" width="100" align="center">
              <template #default="{ row }">{{ row.itemsCount }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="驳回原因" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.auditRemark || '-' }}</template>
            </el-table-column>
            <el-table-column label="备注" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.remark || '-' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="280" fixed="right" align="center">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDetail(row)">详情</el-button>
                <el-button
                  v-if="row.status === APPLY_STATUS.DRAFT"
                  v-perm="'pur:apply:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="row.status === APPLY_STATUS.DRAFT"
                  v-perm="'pur:apply:submit'"
                  link
                  type="success"
                  @click="handleSubmitApply(row)"
                >
                  提交
                </el-button>
                <el-button
                  v-if="row.status === APPLY_STATUS.PENDING"
                  v-perm="'pur:apply:audit'"
                  link
                  type="warning"
                  @click="openAudit(row)"
                >
                  审核
                </el-button>
                <el-button
                  v-if="row.status === APPLY_STATUS.DRAFT"
                  v-perm="'pur:apply:edit'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无采购申请" />
            </template>
          </el-table>

          <template #footer>
            <div class="card-footer">
              <el-pagination
                :current-page="query.page"
                :page-size="query.pageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="total"
                layout="total, sizes, prev, pager, next, jumper"
                @current-change="handlePageChange"
                @size-change="handleSizeChange"
              />
            </div>
          </template>
        </BaseCard>
      </el-col>
    </el-row>

    <!-- 新增 / 编辑草稿：部门默认当前用户，明细行选商品、数量与需求日期 -->
    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新增采购申请' : '编辑采购申请草稿'"
      size="900px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="formRules" label-width="90px">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="申请部门" prop="deptId">
              <el-select v-model="form.deptId" placeholder="请选择部门" filterable style="width: 100%">
                <el-option
                  v-for="dept in depts"
                  :key="dept.id"
                  :label="deptNameMap[dept.id]"
                  :value="dept.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="需求日期" prop="needDate">
              <el-date-picker
                v-model="form.needDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="必填"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input
                v-model="form.remark"
                type="textarea"
                :rows="2"
                maxlength="255"
                show-word-limit
                placeholder="申请原因 / 用途说明（可选）"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <div class="item-toolbar">
          <span class="item-title">申请明细</span>
          <el-button type="primary" link :icon="'Plus'" @click="handleAddItem">添加一行</el-button>
        </div>

        <el-table :data="form.items" border size="small" row-key="key">
          <el-table-column label="商品" min-width="240">
            <template #default="{ row }">
              <el-select
                v-model="row.goodsId"
                placeholder="请选择商品"
                filterable
                style="width: 100%"
                @change="handleGoodsChange(row)"
              >
                <el-option
                  v-for="goods in goodsOptions"
                  :key="goods.id"
                  :label="`${goods.code} ${goods.name}`"
                  :value="goods.id"
                />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="数量" width="150">
            <template #default="{ row }">
              <el-input v-model="row.qty" placeholder=">0" />
            </template>
          </el-table-column>
          <el-table-column label="需求日期" width="180">
            <template #default="{ row }">
              <el-date-picker
                v-model="row.needDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="必填"
                style="width: 100%"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" align="center">
            <template #default="{ $index }">
              <el-button link type="danger" @click="handleRemoveItem($index)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p class="form-tip">
          提示：采购申请只记录需求（商品 / 数量 / 需求日期），不含价格与金额；提交后进入待审，待审与已审不可编辑。
        </p>
      </el-form>

      <template #footer>
        <el-button @click="drawerVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存草稿</el-button>
      </template>
    </el-drawer>

    <!-- 审核：通过 / 驳回（驳回必须填写原因） -->
    <el-dialog v-model="auditVisible" title="审核采购申请" width="520px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="申请单号">
          <span>{{ auditTarget?.applyNo }}</span>
        </el-form-item>
        <el-form-item label="审核结论">
          <el-radio-group v-model="auditForm.result">
            <el-radio label="approved">通过</el-radio>
            <el-radio label="rejected">驳回</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="auditForm.result === 'rejected' ? '驳回原因' : '审核意见'">
          <el-input
            v-model="auditForm.remark"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            :placeholder="auditForm.result === 'rejected' ? '必填：说明驳回原因' : '可选'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="auditVisible = false">取消</el-button>
        <el-button type="primary" :loading="auditSubmitting" @click="handleAudit">确定</el-button>
      </template>
    </el-dialog>


    <!-- 详情 + 打印：打印区单独包裹，@media print 只显示该区域 -->
    <el-drawer v-model="detailVisible" title="采购申请详情" size="900px" destroy-on-close>
      <div v-loading="detailLoading" class="print-area">
        <template v-if="detail">
          <h2 class="print-title">采购申请单</h2>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="申请单号">{{ detail.applyNo }}</el-descriptions-item>
            <el-descriptions-item label="申请部门">{{ detail.deptName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="申请人">
              {{ detail.applicantName || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="需求日期">
              {{ detail.needDate || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTag(detail.status)" size="small">
                {{ statusLabel(detail.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="单据时间">
              {{ detail.createdAt?.replace('T', ' ').slice(0, 19) || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="审核人">{{ detail.auditByName || '-' }}</el-descriptions-item>
            <el-descriptions-item label="审核日期">{{ detail.auditAt || '-' }}</el-descriptions-item>
            <el-descriptions-item label="审核意见">
              {{ detail.auditRemark || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="备注" :span="3">{{ detail.remark || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-table :data="detail.items" border size="small" class="print-table">
            <el-table-column type="index" label="序号" width="70" align="center" />
            <el-table-column label="商品编码" width="140">
              <template #default="{ row }">{{ row.goodsCode || '-' }}</template>
            </el-table-column>
            <el-table-column label="商品名称" min-width="180">
              <template #default="{ row }">{{ row.goodsName || '-' }}</template>
            </el-table-column>
            <el-table-column label="规格" min-width="140">
              <template #default="{ row }">{{ row.spec || '-' }}</template>
            </el-table-column>
            <el-table-column label="单位" width="90" align="center">
              <template #default="{ row }">{{ row.unitName || '-' }}</template>
            </el-table-column>
            <el-table-column label="数量" width="110" align="right">
              <template #default="{ row }">{{ formatQty(row.qty) }}</template>
            </el-table-column>
            <el-table-column label="需求日期" width="130" align="center">
              <template #default="{ row }">{{ row.needDate || '-' }}</template>
            </el-table-column>
          </el-table>

          <div class="print-footer">
            <span>申请人签字：______________</span>
            <span>审核人签字：______________</span>
            <span>日期：______________</span>
          </div>
        </template>
      </div>

      <template #footer>
        <el-button :icon="'Printer'" @click="handlePrint">打印</el-button>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.apply-page {
  padding-bottom: 8px;
}

.query-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.result-card {
  margin-top: 16px;
}

.card-footer {
  display: flex;
  justify-content: flex-end;
}

/* 明细编辑区工具栏 */
.item-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
}

.item-title {
  font-weight: 600;
  color: #303133;
}

.form-tip {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: #909399;
}

/* 报告抬头与明细 */
.print-title {
  margin: 4px 0 16px;
  font-size: 20px;
  text-align: center;
  letter-spacing: 4px;
}

.print-table {
  margin-top: 16px;
}

.print-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  font-size: 13px;
  color: #303133;
}

/* 打印：仅输出详情区，隐藏抽屉外壳与页面其它元素 */
@media print {
  :global(body *) {
    visibility: hidden;
  }

  .print-area,
  .print-area * {
    visibility: visible;
  }

  .print-area {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    padding: 0 8mm;
  }
}
</style>
