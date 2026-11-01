<script setup lang="ts">
/**
 * 客户管理页（B-05）。
 * 结构：查询条件 + 客户表格/分页 + 新增/编辑弹窗 + 信用概况抽屉。
 * 与后端保持一致：
 *   - 编码重复 409；信用额度/账期为负 422；
 *   - 删除前检查销售订单/出库/应收引用，有引用返回 409；
 *   - 停用后不出现在新单据的可选列表，历史单据仍可正常显示；
 *   - 应收余额为应收单据未核销金额汇总，账期决定应收到期日；
 *   - creditLimit 参与订单审核（已发生应收 + 本次金额超额度则阻断）。
 */
import { nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import {
  createCustomer,
  deleteCustomer,
  getCustomerCredit,
  getCustomerList,
  updateCustomer,
} from '@/api/customer'
import type { Customer, CustomerCredit, CustomerQuery } from '@/types/customer'
import BaseCard from '@/components/BaseCard.vue'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const rows = ref<Customer[]>([])
const total = ref(0)

const query = reactive<CustomerQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
})

// ---------- 客户列表 ----------

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getCustomerList(query)
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

/** 金额格式化：后端以字符串返回金额，统一按两位小数展示。 */
const formatAmount = (value: string | number | null | undefined): string => {
  const amount = Number(value ?? 0)
  return Number.isFinite(amount) ? amount.toFixed(2) : '0.00'
}

/** 是否超出信用额度：应收余额 > 信用额度时高亮告警。 */
const isOverLimit = (row: Customer): boolean =>
  Number(row.receivableAmount ?? 0) > Number(row.creditLimit ?? 0)

// ---------- 客户表单 ----------

const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)

interface CustomerForm {
  code: string
  name: string
  contact: string
  phone: string
  taxNo: string
  bank: string
  account: string
  creditLimit: number
  creditDays: number
  status: number
  remark: string
}

const createEmptyForm = (): CustomerForm => ({
  code: '',
  name: '',
  contact: '',
  phone: '',
  taxNo: '',
  bank: '',
  account: '',
  creditLimit: 0,
  creditDays: 0,
  status: 1,
  remark: '',
})

const form = reactive<CustomerForm>(createEmptyForm())

const rules: FormRules = {
  name: [{ required: true, message: '请输入客户名称', trigger: 'blur' }],
  creditLimit: [
    { required: true, message: '请输入信用额度', trigger: 'blur' },
    // 信用额度不能为负：前端先拦截，后端同样返回 422 兜底
    { type: 'number', min: 0, message: '信用额度不能为负数', trigger: 'blur' },
  ],
  creditDays: [
    { required: true, message: '请输入账期天数', trigger: 'blur' },
    { type: 'number', min: 0, message: '账期天数不能为负数', trigger: 'blur' },
  ],
}

const openCreate = (): void => {
  dialogMode.value = 'create'
  editingId.value = null
  Object.assign(form, createEmptyForm())
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

const openEdit = (row: Customer): void => {
  dialogMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, {
    code: row.code,
    name: row.name,
    contact: row.contact ?? '',
    phone: row.phone ?? '',
    taxNo: row.taxNo ?? '',
    bank: row.bank ?? '',
    account: row.account ?? '',
    creditLimit: Number(row.creditLimit ?? 0),
    creditDays: row.creditDays,
    status: row.status,
    remark: row.remark ?? '',
  })
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    const payload = {
      code: form.code || null,
      name: form.name.trim(),
      contact: form.contact.trim() || null,
      phone: form.phone.trim() || null,
      taxNo: form.taxNo.trim() || null,
      bank: form.bank.trim() || null,
      account: form.account.trim() || null,
      creditLimit: form.creditLimit,
      creditDays: form.creditDays,
      status: form.status,
      remark: form.remark.trim() || null,
    }
    if (dialogMode.value === 'create') {
      await createCustomer(payload)
      ElMessage.success('新增成功')
    } else if (editingId.value !== null) {
      await updateCustomer(editingId.value, payload)
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    await fetchList()
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row: Customer): Promise<void> => {
  await ElMessageBox.confirm(`确定删除客户「${row.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteCustomer(row.id)
  ElMessage.success('删除成功')
  await fetchList()
}

/** 启用/停用切换：停用后新单据不可选，历史单据仍可显示。 */
const handleStatusChange = async (row: Customer, value: unknown): Promise<void> => {
  const status = Number(value)
  try {
    await updateCustomer(row.id, { status })
    row.status = status
    ElMessage.success(status === 1 ? '已启用' : '已停用')
  } catch {
    // 后端拒绝时回滚开关，重新拉取真实状态
    void fetchList()
  }
}

// ---------- 信用概况入口 ----------

const creditVisible = ref(false)
const currentCustomer = ref<Customer | null>(null)
const creditInfo = ref<CustomerCredit | null>(null)

/** 打开信用概况抽屉：展示信用额度、应收余额、可用额度与是否超限。 */
const openCredit = async (row: Customer): Promise<void> => {
  currentCustomer.value = row
  creditInfo.value = null
  creditVisible.value = true
  const info = await getCustomerCredit(row.id)
  creditInfo.value = info
}

/** 跳转到应收管理，并带上客户 ID 便于筛选。 */
const goReceivable = (): void => {
  if (!currentCustomer.value) return
  void router.push({
    path: '/finance/receivable',
    query: { customerId: String(currentCustomer.value.id) },
  })
}

onMounted(() => {
  void fetchList()
})
</script>

<template>
  <div class="page-container customer-page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="24">
        <BaseCard
          title="查询条件"
          subtitle="维护客户基础档案，支持编码/名称/联系人/电话关键字与状态筛选。"
        >
          <el-form class="query-form" label-width="72px" @submit.prevent="handleSearch">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="关键字">
                  <el-input
                    v-model="query.keyword"
                    placeholder="编码 / 名称 / 联系人 / 电话"
                    clearable
                    @keyup.enter="handleSearch"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8" :lg="6">
                <el-form-item label="状态">
                  <el-select v-model="query.status" placeholder="全部" clearable>
                    <el-option label="启用" :value="1" />
                    <el-option label="停用" :value="0" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>

            <div class="query-actions">
              <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
              <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
              <el-button
                v-perm="'base:customer:add'"
                type="primary"
                :icon="'Plus'"
                @click="openCreate"
              >
                新增客户
              </el-button>
            </div>
          </el-form>
        </BaseCard>

        <BaseCard
          class="result-card"
          title="查询结果"
          body-padding="0"
          show-divider
          :loading="loading"
        >
          <el-table :data="rows" border stripe>
            <el-table-column prop="code" label="编码" width="110" />
            <el-table-column prop="name" label="名称" min-width="150" />
            <el-table-column prop="contact" label="联系人" width="100">
              <template #default="{ row }">{{ row.contact || '-' }}</template>
            </el-table-column>
            <el-table-column prop="phone" label="电话" width="130">
              <template #default="{ row }">{{ row.phone || '-' }}</template>
            </el-table-column>
            <el-table-column prop="taxNo" label="税号" width="150">
              <template #default="{ row }">{{ row.taxNo || '-' }}</template>
            </el-table-column>
            <el-table-column label="信用额度" width="120" align="right">
              <template #default="{ row }">
                <span class="credit-limit">￥{{ formatAmount(row.creditLimit) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="账期" width="90" align="center">
              <template #default="{ row }">{{ row.creditDays }} 天</template>
            </el-table-column>
            <el-table-column label="应收余额" width="140" align="right">
              <template #default="{ row }">
                <span :class="isOverLimit(row) ? 'over-limit' : 'receivable-amount'">
                  ￥{{ formatAmount(row.receivableAmount) }}
                </span>
                <el-tag v-if="isOverLimit(row)" type="danger" size="small" effect="dark">
                  超限
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-switch
                  v-perm="'base:customer:edit'"
                  :model-value="row.status"
                  :active-value="1"
                  :inactive-value="0"
                  @change="handleStatusChange(row, $event)"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right" align="center">
              <template #default="{ row }">
                <el-button
                  v-perm="'base:customer:edit'"
                  link
                  type="primary"
                  @click="openEdit(row)"
                >
                  编辑
                </el-button>
                <el-button link type="warning" @click="openCredit(row)">信用</el-button>
                <el-button
                  v-perm="'base:customer:delete'"
                  link
                  type="danger"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无客户数据" />
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

    <!-- 客户新增/编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新增客户' : '编辑客户'"
      width="680px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="客户编码">
              <el-input
                v-model="form.code"
                :disabled="dialogMode === 'edit'"
                placeholder="留空由后端自动生成"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="客户名称" prop="name">
              <el-input v-model="form.name" placeholder="必填" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系人">
              <el-input v-model="form.contact" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="联系电话">
              <el-input v-model="form.phone" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="税号">
              <el-input v-model="form.taxNo" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="开户行">
              <el-input v-model="form.bank" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="银行账号">
              <el-input v-model="form.account" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="信用额度" prop="creditLimit">
              <el-input-number
                v-model="form.creditLimit"
                :min="0"
                :max="999999999"
                :step="1000"
                controls-position="right"
                class="full"
              />
              <div class="form-tip">信用额度参与订单审核：已发生应收 + 本次订单金额超额度时阻断。</div>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="账期" prop="creditDays">
              <el-input-number
                v-model="form.creditDays"
                :min="0"
                :max="3650"
                :step="15"
                controls-position="right"
                class="full"
              />
              <div class="form-tip">账期天数，用于推算应收到期日（出库日期 + 账期）。</div>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="地址">
              <el-input v-model="form.remark" type="textarea" :rows="3" maxlength="255" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="状态">
              <el-radio-group v-model="form.status">
                <el-radio :value="1">启用</el-radio>
                <el-radio :value="0">停用</el-radio>
              </el-radio-group>
              <div class="form-tip">停用后该客户不能被新销售订单选中，历史单据不受影响。</div>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 信用概况入口 -->
    <el-drawer v-model="creditVisible" title="信用概况" size="440px">
      <el-descriptions v-if="currentCustomer" :column="1" border>
        <el-descriptions-item label="客户编码">
          {{ currentCustomer.code }}
        </el-descriptions-item>
        <el-descriptions-item label="客户名称">
          {{ currentCustomer.name }}
        </el-descriptions-item>
        <el-descriptions-item label="信用额度">
          <span class="credit-limit">￥{{ formatAmount(creditInfo?.creditLimit ?? 0) }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="应收余额">
          <span class="receivable-amount">
            ￥{{ formatAmount(creditInfo?.receivableAmount ?? 0) }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="可用额度">
          <span :class="(creditInfo?.overLimit ?? false) ? 'over-limit' : 'receivable-amount'">
            ￥{{ formatAmount(creditInfo?.availableCredit ?? 0) }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="是否超限">
          <el-tag :type="(creditInfo?.overLimit ?? false) ? 'danger' : 'success'">
            {{ (creditInfo?.overLimit ?? false) ? '已超限' : '正常' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="到期日规则">
          出库日期 + 账期天数 = 应收到期日
        </el-descriptions-item>
      </el-descriptions>

      <el-alert
        v-if="creditInfo?.overLimit"
        class="credit-tip"
        type="error"
        :closable="false"
        title="该客户已超出信用额度，新销售订单审核将被阻断。"
      />
      <el-alert
        v-else
        class="credit-tip"
        type="info"
        :closable="false"
        title="应收明细模块开发中，后续将在此展示该客户的应收单据与核销记录。"
      />

      <el-button
        v-if="userStore.hasPerm('fin:receivable:list')"
        type="primary"
        link
        @click="goReceivable"
      >
        前往应收管理
      </el-button>
    </el-drawer>
  </div>
</template>

<style scoped>
.query-form {
  margin-bottom: 0;
}

.query-form :deep(.el-input),
.query-form :deep(.el-select) {
  width: 100%;
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

.full {
  width: 100%;
}

.credit-limit {
  font-weight: 600;
  color: #1f6feb;
}

.receivable-amount {
  font-weight: 600;
  color: #d48806;
}

.over-limit {
  font-weight: 600;
  color: #f5222d;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}

.credit-tip {
  margin-top: 16px;
}
</style>
