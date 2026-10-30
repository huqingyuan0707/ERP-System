<script setup lang="ts">
/**
 * 用户管理页（S-01）。
 * 结构：左侧部门树筛选 + 右侧查询表单/用户表/分页 + 新增编辑抽屉；
 * 支持重置密码、启用禁用；不能删除/禁用自己与内置 admin。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { createUser, deleteUser, getUserList, resetUserPassword, updateUser } from '@/api/user'
import { getRoleList } from '@/api/role'
import { getDeptList } from '@/api/system'
import { useUserStore } from '@/stores/user'
import type { Role } from '@/types/role'
import type { Dept } from '@/types/system'
import type { User, UserQuery } from '@/types/user'

interface DeptNode {
  value: number
  label: string
  children: DeptNode[]
}

const userStore = useUserStore()

const loading = ref(false)
const rows = ref<User[]>([])
const total = ref(0)
const depts = ref<Dept[]>([])
const roles = ref<Role[]>([])
const currentDeptId = ref<number | null>(null)
const treeRef = ref()

const query = reactive<UserQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  deptId: null,
})

// ---------- 部门树（扁平行转树） ----------
const deptTree = computed<DeptNode[]>(() => {
  const nodeMap = new Map<number, DeptNode>()
  const roots: DeptNode[] = []
  for (const dept of depts.value) {
    nodeMap.set(dept.id, { value: dept.id, label: dept.name, children: [] })
  }
  for (const dept of depts.value) {
    const node = nodeMap.get(dept.id)
    if (!node) continue
    const parent = dept.parentId !== null ? nodeMap.get(dept.parentId) : undefined
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
})

const deptName = (deptId: number | null): string => {
  if (deptId === null) return '-'
  const hit = depts.value.find((dept) => dept.id === deptId)
  return hit?.name ?? String(deptId)
}

const roleNames = (roleIds: number[]): string => {
  if (roleIds.length === 0) return '-'
  const map = new Map(roles.value.map((role) => [role.id, role.name]))
  return roleIds.map((id) => map.get(id) ?? String(id)).join('、')
}

const formatDateTime = (value: string | null): string => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

// ---------- 保护规则：不能删除/禁用自己与内置 admin ----------
const isProtected = (row: User): boolean =>
  row.id === userStore.user?.id || row.username === 'admin'

// ---------- 抽屉表单 ----------
const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)
const editingProtected = ref(false)

interface UserForm {
  username: string
  password: string
  realName: string
  mobile: string
  email: string
  deptId: number | null
  position: string
  roleIds: number[]
  status: number
}

const createEmptyForm = (): UserForm => ({
  username: '',
  password: '',
  realName: '',
  mobile: '',
  email: '',
  deptId: null,
  position: '',
  roleIds: [],
  status: 1,
})
const form = reactive<UserForm>(createEmptyForm())

const rules = computed<FormRules<UserForm>>(() => ({
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password:
    drawerMode.value === 'create'
      ? [
          { required: true, message: '请输入初始密码', trigger: 'blur' },
          { min: 6, max: 128, message: '密码长度 6-128 位', trigger: 'blur' },
        ]
      : [],
}))

// ---------- 列表 ----------
const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getUserList(query)
    rows.value = result.items
    total.value = result.total
  } finally {
    loading.value = false
  }
}

const fetchOptions = async (): Promise<void> => {
  const [deptList, roleList] = await Promise.all([getDeptList(), getRoleList()])
  depts.value = deptList
  roles.value = roleList
}

const handleSearch = (): void => {
  query.page = 1
  void fetchList()
}

const handleReset = (): void => {
  query.keyword = ''
  query.status = null
  query.deptId = null
  currentDeptId.value = null
  treeRef.value?.setCurrentKey(null)
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

const handleDeptSelect = (data: DeptNode | null): void => {
  currentDeptId.value = data?.value ?? null
  query.deptId = currentDeptId.value
  handleSearch()
}

const handleDeptClear = (): void => {
  currentDeptId.value = null
  treeRef.value?.setCurrentKey(null)
  query.deptId = null
  handleSearch()
}

// ---------- 新增 / 编辑 ----------
const openCreate = (): void => {
  drawerMode.value = 'create'
  editingId.value = null
  editingProtected.value = false
  Object.assign(form, createEmptyForm())
  drawerVisible.value = true
}

const openEdit = (row: User): void => {
  drawerMode.value = 'edit'
  editingId.value = row.id
  editingProtected.value = isProtected(row)
  Object.assign(form, {
    username: row.username,
    password: '',
    realName: row.realName ?? '',
    mobile: row.mobile ?? '',
    email: row.email ?? '',
    deptId: row.deptId,
    position: row.position ?? '',
    roleIds: [...row.roleIds],
    status: row.status,
  })
  drawerVisible.value = true
}

const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    if (drawerMode.value === 'create') {
      await createUser({
        username: form.username,
        password: form.password,
        realName: form.realName || null,
        mobile: form.mobile || null,
        email: form.email || null,
        deptId: form.deptId,
        position: form.position || null,
        status: form.status,
        roleIds: form.roleIds,
      })
      ElMessage.success('新增成功')
    } else if (editingId.value !== null) {
      await updateUser(editingId.value, {
        realName: form.realName || null,
        mobile: form.mobile || null,
        email: form.email || null,
        deptId: form.deptId,
        position: form.position || null,
        status: form.status,
        roleIds: form.roleIds,
      })
      ElMessage.success('修改成功')
    }
    drawerVisible.value = false
    void fetchList()
  } finally {
    submitting.value = false
  }
}

// ---------- 行操作 ----------
const handleToggleStatus = async (row: User): Promise<void> => {
  const disabling = row.status === 1
  const action = disabling ? '停用' : '启用'
  await ElMessageBox.confirm(
    disabling
      ? `确定停用用户「${row.username}」吗？停用后该账号无法登录，已有登录态立即失效。`
      : `确定启用用户「${row.username}」吗？`,
    `${action}确认`,
    { type: 'warning' },
  )
  await updateUser(row.id, { status: disabling ? 0 : 1 })
  ElMessage.success(`${action}成功`)
  void fetchList()
}

const handleResetPassword = async (row: User): Promise<void> => {
  const { value } = await ElMessageBox.prompt(
    '留空则重置为默认密码 123456',
    `重置密码 — ${row.username}`,
    {
      confirmButtonText: '确定重置',
      cancelButtonText: '取消',
      inputValue: '',
      inputValidator: (input: string) =>
        input.length === 0 || input.length >= 6 || '密码至少 6 位',
    },
  )
  await resetUserPassword(row.id, value || undefined)
  ElMessage.success('密码已重置，该账号原有登录态已失效')
}

const handleDelete = async (row: User): Promise<void> => {
  await ElMessageBox.confirm(
    `确定删除用户「${row.username}」吗？删除后账号不可登录，历史数据仍保留。`,
    '删除确认',
    { type: 'warning' },
  )
  await deleteUser(row.id)
  ElMessage.success('删除成功')
  void fetchList()
}

onMounted(() => {
  void fetchList()
  void fetchOptions()
})
</script>

<template>
  <div class="page-container">
    <div class="page-card">
      <div class="page-header">
        <h1 class="page-title">用户管理</h1>
        <p class="page-subtitle">维护系统用户及部门、角色、岗位信息；支持启用/停用与重置密码。</p>
      </div>

      <div class="user-page">
        <div class="page-card dept-pane">
          <div class="pane-header">
            <span class="pane-title">部门</span>
            <el-button
              link
              type="primary"
              size="small"
              :disabled="currentDeptId === null"
              @click="handleDeptClear"
            >
              全部
            </el-button>
          </div>
          <el-tree
            ref="treeRef"
            class="dept-tree"
            :data="deptTree"
            node-key="value"
            highlight-current
            default-expand-all
            :expand-on-click-node="false"
            @current-change="handleDeptSelect"
          />
        </div>

        <div class="page-card list-pane">
          <el-form :inline="true" class="query-form" @submit.prevent="handleSearch">
            <el-form-item label="关键字">
              <el-input
                v-model="query.keyword"
                placeholder="用户名 / 姓名 / 手机号"
                clearable
                :style="{ width: '260px' }"
                @keyup.enter="handleSearch"
              />
            </el-form-item>
            <el-form-item label="状态">
              <el-select v-model="query.status" placeholder="全部" clearable :style="{ width: '160px' }">
                <el-option label="启用" :value="1" />
                <el-option label="停用" :value="0" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
              <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
            </el-form-item>
          </el-form>

          <div class="toolbar">
            <el-button v-perm="'sys:user:add'" type="primary" :icon="'Plus'" @click="openCreate">
              新增用户
            </el-button>
          </div>

          <el-table v-loading="loading" :data="rows" border stripe>
            <el-table-column prop="username" label="用户名" width="140" show-overflow-tooltip />
            <el-table-column prop="realName" label="姓名" width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.realName ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="手机号" width="150">
              <template #default="{ row }">{{ row.mobile ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="部门" width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ deptName(row.deptId) }}</template>
            </el-table-column>
            <el-table-column label="岗位" width="120">
              <template #default="{ row }">{{ row.position ?? '-' }}</template>
            </el-table-column>
            <el-table-column label="角色" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ roleNames(row.roleIds) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="100" align="center">
              <template #default="{ row }">
                <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
                  {{ row.status === 1 ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最后登录" width="190">
              <template #default="{ row }">{{ formatDateTime(row.lastLoginAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right" align="center">
              <template #default="{ row }">
                <el-button v-perm="'sys:user:edit'" link type="primary" @click="openEdit(row)">
                  编辑
                </el-button>
                <el-button
                  v-perm="'sys:user:reset'"
                  link
                  type="warning"
                  @click="handleResetPassword(row)"
                >
                  重置密码
                </el-button>
                <el-button
                  v-perm="'sys:user:edit'"
                  link
                  :type="row.status === 1 ? 'danger' : 'success'"
                  :disabled="isProtected(row)"
                  @click="handleToggleStatus(row)"
                >
                  {{ row.status === 1 ? '停用' : '启用' }}
                </el-button>
                <el-button
                  v-perm="'sys:user:delete'"
                  link
                  type="danger"
                  :disabled="isProtected(row)"
                  @click="handleDelete(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty description="暂无用户数据" />
            </template>
          </el-table>

          <div class="pager">
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
        </div>
      </div>

      <el-drawer
        v-model="drawerVisible"
        :title="drawerMode === 'create' ? '新增用户' : `编辑用户 — ${form.username}`"
        size="600px"
        destroy-on-close
      >
        <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
          <el-form-item label="用户名" prop="username">
            <el-input
              v-model="form.username"
              :disabled="drawerMode === 'edit'"
              placeholder="登录账号"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item v-if="drawerMode === 'create'" label="初始密码" prop="password">
            <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" maxlength="128" />
          </el-form-item>
          <el-form-item label="姓名">
            <el-input v-model="form.realName" placeholder="真实姓名" maxlength="64" />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input v-model="form.mobile" placeholder="可选" maxlength="32" />
          </el-form-item>
          <el-form-item label="邮箱">
            <el-input v-model="form.email" placeholder="可选" maxlength="128" />
          </el-form-item>
          <el-form-item label="部门">
            <el-tree-select
              v-model="form.deptId"
              :data="deptTree"
              check-strictly
              clearable
              placeholder="请选择部门"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="岗位">
            <el-input v-model="form.position" placeholder="如 采购专员" maxlength="64" />
          </el-form-item>
          <el-form-item label="角色">
            <el-select
              v-model="form.roleIds"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              placeholder="可多选"
              style="width: 100%"
            >
              <el-option v-for="role in roles" :key="role.id" :label="role.name" :value="role.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-radio-group v-model="form.status" :disabled="editingProtected">
              <el-radio :value="1">启用</el-radio>
              <el-radio :value="0">停用</el-radio>
            </el-radio-group>
            <div v-if="editingProtected" class="protected-tip">
              内置管理员与当前登录账号不允许停用
            </div>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="drawerVisible = false">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
        </template>
      </el-drawer>
    </div>
  </div>
</template>

<style scoped>
.user-page {
  display: flex;
  gap: 20px;
  height: 100%;
  min-height: 0;
  overflow-x: auto;
}

.dept-pane {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.list-pane {
  flex: 1;
  min-width: 600px;
  display: flex;
  flex-direction: column;
}

.pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.pane-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.dept-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.protected-tip {
  width: 100%;
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
</style>
