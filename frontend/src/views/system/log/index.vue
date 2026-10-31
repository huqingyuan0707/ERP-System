<script setup lang="ts">
/** 系统日志页（C-06）：操作日志 + 登录日志两个 Tab，筛选/分页/详情抽屉。 */
import { ref, reactive, onMounted } from 'vue'

import {
  getLoginLogList,
  getOperLogList,
} from '@/api/log'
import type {
  LoginLog,
  LoginLogQuery,
  OperLog,
  OperLogQuery,
} from '@/types/log'

const activeTab = ref<'oper' | 'login'>('oper')

// ---------- 操作日志 ----------
const operLoading = ref(false)
const operRows = ref<OperLog[]>([])
const operTotal = ref(0)
const operQuery = reactive<OperLogQuery>({
  page: 1,
  pageSize: 20,
  module: '',
  action: '',
  userId: null,
  status: null,
  startDate: '',
  endDate: '',
})
const operDateRange = ref<[string, string] | null>(null)

const fetchOperLogs = async (): Promise<void> => {
  operQuery.startDate = operDateRange.value?.[0] ?? ''
  operQuery.endDate = operDateRange.value?.[1] ?? ''
  operLoading.value = true
  try {
    const result = await getOperLogList(operQuery)
    operRows.value = result.items
    operTotal.value = result.total
  } finally {
    operLoading.value = false
  }
}

const handleOperPageChange = (page: number): void => {
  operQuery.page = page
  void fetchOperLogs()
}

const handleOperSizeChange = (size: number): void => {
  operQuery.pageSize = size
  operQuery.page = 1
  void fetchOperLogs()
}

// ---------- 登录日志 ----------
const loginLoading = ref(false)
const loginRows = ref<LoginLog[]>([])
const loginTotal = ref(0)
const loginQuery = reactive<LoginLogQuery>({
  page: 1,
  pageSize: 20,
  username: '',
  status: null,
  startDate: '',
  endDate: '',
})
const loginDateRange = ref<[string, string] | null>(null)

const fetchLoginLogs = async (): Promise<void> => {
  loginQuery.startDate = loginDateRange.value?.[0] ?? ''
  loginQuery.endDate = loginDateRange.value?.[1] ?? ''
  loginLoading.value = true
  try {
    const result = await getLoginLogList(loginQuery)
    loginRows.value = result.items
    loginTotal.value = result.total
  } finally {
    loginLoading.value = false
  }
}

const handleLoginPageChange = (page: number): void => {
  loginQuery.page = page
  void fetchLoginLogs()
}

const handleLoginSizeChange = (size: number): void => {
  loginQuery.pageSize = size
  loginQuery.page = 1
  void fetchLoginLogs()
}

// ---------- 详情抽屉 ----------
const detailVisible = ref(false)
const detailTitle = ref('')
const detailContent = ref('')

const showDetail = (record: OperLog | LoginLog, type: 'oper' | 'login'): void => {
  const lines: string[] = []
  if (type === 'oper') {
    const r = record as OperLog
    detailTitle.value = '操作日志详情'
    lines.push(`模块：${r.module ?? '-'}`)
    lines.push(`动作：${r.action ?? '-'}`)
    lines.push(`操作人：${r.userName ?? '-'}(ID: ${r.userId ?? '-'})`)
    lines.push(`方法：${r.method ?? '-'}`)
    lines.push(`URL：${r.url ?? '-'}`)
    lines.push(`IP：${r.ip ?? '-'}`)
    lines.push(`状态：${r.status === 1 ? '成功' : '失败'}`)
    lines.push(`耗时：${r.durationMs ?? '-'} ms`)
    lines.push(`参数：${r.params ?? '-'}`)
    lines.push(`结果：${r.result ?? '-'}`)
    lines.push(`创建时间：${r.createdAt ?? '-'}`)
  } else {
    const r = record as LoginLog
    detailTitle.value = '登录日志详情'
    lines.push(`账号：${r.username}`)
    lines.push(`IP：${r.ip ?? '-'}`)
    lines.push(`UA：${r.userAgent ?? '-'}`)
    lines.push(`状态：${r.status === 1 ? '成功' : '失败'}`)
    lines.push(`消息：${r.message ?? '-'}`)
    lines.push(`登录时间：${r.createdAt ?? '-'}`)
  }
  detailContent.value = lines.join('\n')
  detailVisible.value = true
}

const statusLabel = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return '-'
  return v === 1 ? '成功' : '失败'
}

const statusType = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return 'info'
  return v === 1 ? 'success' : 'danger'
}

const formatDate = (value: Date): string => {
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

const resetOper = (): void => {
  operQuery.module = ''
  operQuery.action = ''
  operQuery.status = null
  operQuery.startDate = ''
  operQuery.endDate = ''
  operDateRange.value = null
  void fetchOperLogs()
}

const resetLogin = (): void => {
  loginQuery.username = ''
  loginQuery.status = null
  loginQuery.startDate = ''
  loginQuery.endDate = ''
  loginDateRange.value = null
  void fetchLoginLogs()
}

const handleTabChange = (name: string): void => {
  if (name === 'oper') {
    void fetchOperLogs()
  } else {
    void fetchLoginLogs()
  }
}

onMounted(() => {
  void fetchOperLogs()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">操作日志</h1>
        <p class="page-subtitle">记录登录与关键业务操作，仅支持查询与审计，不提供删除。</p>
      </div>

      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <el-tab-pane name="oper" label="操作日志">
          <el-form :inline="true" class="query-form" @submit.prevent="fetchOperLogs">
            <el-form-item label="模块">
              <el-input v-model="operQuery.module" placeholder="如 系统管理" clearable />
            </el-form-item>
            <el-form-item label="动作">
              <el-input v-model="operQuery.action" placeholder="如 新增用户" clearable />
            </el-form-item>
            <el-form-item label="状态">
              <el-select v-model="operQuery.status" placeholder="全部" clearable style="width: 120px">
                <el-option label="成功" :value="1" />
                <el-option label="失败" :value="0" />
              </el-select>
            </el-form-item>
            <el-form-item label="时间">
              <el-date-picker
                v-model="operDateRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                :format="[formatDate]"
                value-format="yyyy-MM-dd"
                style="width: 260px"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :icon="'Search'" @click="fetchOperLogs">查询</el-button>
              <el-button :icon="'Refresh'" @click="resetOper">重置</el-button>
            </el-form-item>
          </el-form>

          <el-table v-loading="operLoading" :data="operRows" border stripe>
            <el-table-column prop="userName" label="操作人" width="120" show-overflow-tooltip />
            <el-table-column prop="module" label="模块" width="120" show-overflow-tooltip />
            <el-table-column prop="action" label="动作" min-width="140" show-overflow-tooltip />
            <el-table-column prop="method" label="方法" width="80" align="center" />
            <el-table-column prop="ip" label="IP" width="120" />
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="durationMs" label="耗时(ms)" width="100" align="right" />
            <el-table-column prop="createdAt" label="创建时间" width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button link size="small" @click="showDetail(row, 'oper')">详情</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无操作日志" />
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              :current-page="operQuery.page"
              :page-size="operQuery.pageSize"
              :page-sizes="[10, 20, 50, 100]"
              :total="operTotal"
              layout="total, sizes, prev, pager, next, jumper"
              @current-change="handleOperPageChange"
              @size-change="handleOperSizeChange"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane name="login" label="登录日志">
          <el-form :inline="true" class="query-form" @submit.prevent="fetchLoginLogs">
            <el-form-item label="账号">
              <el-input v-model="loginQuery.username" placeholder="用户名" clearable />
            </el-form-item>
            <el-form-item label="状态">
              <el-select v-model="loginQuery.status" placeholder="全部" clearable style="width: 120px">
                <el-option label="成功" :value="1" />
                <el-option label="失败" :value="0" />
              </el-select>
            </el-form-item>
            <el-form-item label="时间">
              <el-date-picker
                v-model="loginDateRange"
                type="daterange"
                range-separator="至"
                start-placeholder="开始日期"
                end-placeholder="结束日期"
                :format="[formatDate]"
                value-format="yyyy-MM-dd"
                style="width: 260px"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :icon="'Search'" @click="fetchLoginLogs">查询</el-button>
              <el-button :icon="'Refresh'" @click="resetLogin">重置</el-button>
            </el-form-item>
          </el-form>

          <el-table v-loading="loginLoading" :data="loginRows" border stripe>
            <el-table-column prop="username" label="账号" width="140" show-overflow-tooltip />
            <el-table-column prop="ip" label="IP" width="120" />
            <el-table-column prop="userAgent" label="浏览器/UA" min-width="200" show-overflow-tooltip />
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small">
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="消息" min-width="160" show-overflow-tooltip />
            <el-table-column prop="createdAt" label="登录时间" width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="100" align="center">
              <template #default="{ row }">
                <el-button link size="small" @click="showDetail(row, 'login')">详情</el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无登录日志" />
            </template>
          </el-table>

          <div class="pager">
            <el-pagination
              :current-page="loginQuery.page"
              :page-size="loginQuery.pageSize"
              :page-sizes="[10, 20, 50, 100]"
              :total="loginTotal"
              layout="total, sizes, prev, pager, next, jumper"
              @current-change="handleLoginPageChange"
              @size-change="handleLoginSizeChange"
            />
          </div>
        </el-tab-pane>
      </el-tabs>

      <el-drawer v-model="detailVisible" :title="detailTitle" size="600px" destroy-on-close>
        <pre class="detail-pre">{{ detailContent }}</pre>
      </el-drawer>
    </div>
  </div>
</template>

<style scoped>
.page-container {
  padding: 0 24px;
}
.query-form {
  margin-bottom: 16px;
}
.detail-pre {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
  background: #f5f7fa;
  padding: 16px;
  border-radius: 4px;
  margin: 0;
  height: 320px;
  overflow-y: auto;
}
.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
