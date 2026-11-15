<script setup lang="ts">
/**
 * 员工与部门（B-06）。
 * 左侧部门树：新增/编辑/删除/上移下移排序，节点显示引用计数用于预禁用删除；
 * 右侧员工表：按部门（可含下级）与状态筛选、新增/编辑/删除、关联系统用户、启用停用。
 * 后端约束：部门名称与员工编码同租户唯一；删除有子部门/员工/用户的部门返回 409；
 * 关联用户会同步 sys_user.dept_id（数据范围）；停用员工仅影响新单据可选列表。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { createDept, deleteDept, getDeptTree, reorderDepts, updateDept } from '@/api/dept'
import {
  createEmployee,
  deleteEmployee,
  getEmployeeList,
  getUserOptions,
  updateEmployee,
} from '@/api/employee'
import BaseCard from '@/components/BaseCard.vue'
import { useUserStore } from '@/stores/user'
import type { Dept, DeptSortItem } from '@/types/dept'
import type { Employee, EmployeeQuery, UserOption } from '@/types/employee'

interface DeptNode {
  value: number
  label: string
  children: DeptNode[]
  raw: Dept
}

const userStore = useUserStore()

// ---------- 部门树 ----------

const depts = ref<Dept[]>([])
const treeLoading = ref(false)
const activeDeptId = ref<number | null>(null)
const treeRef = ref()

/** 扁平部门列表转树，按 sort、id 稳定排序。 */
const deptTree = computed<DeptNode[]>(() => {
  const nodes = new Map<number, DeptNode>()
  for (const dept of depts.value) {
    nodes.set(dept.id, { value: dept.id, label: dept.name, children: [], raw: dept })
  }
  const roots: DeptNode[] = []
  for (const dept of depts.value) {
    const node = nodes.get(dept.id)
    if (!node) continue
    const parent = dept.parentId !== null ? nodes.get(dept.parentId) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const sortNodes = (items: DeptNode[]): void => {
    items.sort((left, right) => left.raw.sort - right.raw.sort || left.value - right.value)
    items.forEach((item) => sortNodes(item.children))
  }
  sortNodes(roots)
  return roots
})

/** 编辑部门时排除自身及全部下级，避免把父级设成自己的后代（后端同样返回 422）。 */
const excludedParentIds = computed<Set<number>>(() => {
  const result = new Set<number>()
  if (editingDeptId.value === null) return result
  const stack: number[] = [editingDeptId.value]
  while (stack.length > 0) {
    const current = stack.pop()
    if (current === undefined || result.has(current)) continue
    result.add(current)
    for (const dept of depts.value) {
      if (dept.parentId === current) stack.push(dept.id)
    }
  }
  return result
})

const parentOptions = computed<DeptNode[]>(() => {
  const filter = (items: DeptNode[]): DeptNode[] =>
    items
      .filter((item) => !excludedParentIds.value.has(item.value))
      .map((item) => ({ ...item, children: filter(item.children) }))
  return filter(deptTree.value)
})

const fetchDepts = async (): Promise<void> => {
  treeLoading.value = true
  try {
    depts.value = await getDeptTree()
  } finally {
    treeLoading.value = false
  }
}

/** 同级部门（按当前排序），用于上下移动。 */
const siblingsOf = (node: DeptNode): Dept[] =>
  depts.value
    .filter((dept) => dept.parentId === node.raw.parentId)
    .sort((left, right) => left.sort - right.sort || left.id - right.id)

const isFirstSibling = (node: DeptNode): boolean => siblingsOf(node)[0]?.id === node.value
const isLastSibling = (node: DeptNode): boolean => {
  const siblings = siblingsOf(node)
  return siblings[siblings.length - 1]?.id === node.value
}

/** 引用计数为 0 才允许删除；后端仍会二次校验并返回 409。 */
const canDeleteDept = (node: DeptNode): boolean =>
  node.raw.childrenCount === 0 &&
  node.raw.employeeCount === 0 &&
  node.raw.userCount === 0

const handleDeptSelect = (data: DeptNode | null): void => {
  activeDeptId.value = data?.value ?? null
  query.deptId = activeDeptId.value
  handleSearch()
}

const handleDeptClear = (): void => {
  activeDeptId.value = null
  query.deptId = null
  treeRef.value?.setCurrentKey(null)
  handleSearch()
}

// ---------- 员工列表 ----------

const loading = ref(false)
const rows = ref<Employee[]>([])
const total = ref(0)
const query = reactive<EmployeeQuery>({
  page: 1,
  pageSize: 20,
  keyword: '',
  status: null,
  deptId: null,
  includeSubDept: false,
})

const fetchList = async (): Promise<void> => {
  loading.value = true
  try {
    const result = await getEmployeeList(query)
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
  query.includeSubDept = false
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

// ---------- 部门表单 ----------

const deptDialogVisible = ref(false)
const deptMode = ref<'create' | 'edit'>('create')
const deptSubmitting = ref(false)
const deptFormRef = ref<FormInstance>()
const editingDeptId = ref<number | null>(null)

interface DeptForm {
  name: string
  parentId: number | null
  leader: string
  sort: number
  status: number
  remark: string
}

const emptyDeptForm = (): DeptForm => ({
  name: '',
  parentId: null,
  leader: '',
  sort: 0,
  status: 1,
  remark: '',
})

const deptForm = reactive<DeptForm>(emptyDeptForm())
const deptRules: FormRules = {
  name: [{ required: true, message: '请输入部门名称', trigger: 'blur' }],
}

const openDeptCreate = (parentId: number | null = null): void => {
  deptMode.value = 'create'
  editingDeptId.value = null
  Object.assign(deptForm, emptyDeptForm(), {
    parentId,
    sort: (depts.value.length + 1) * 10,
  })
  deptDialogVisible.value = true
  void nextTick(() => deptFormRef.value?.clearValidate())
}

const openDeptEdit = (node: DeptNode): void => {
  deptMode.value = 'edit'
  editingDeptId.value = node.value
  Object.assign(deptForm, {
    name: node.raw.name,
    parentId: node.raw.parentId,
    leader: node.raw.leader ?? '',
    sort: node.raw.sort,
    status: node.raw.status,
    remark: node.raw.remark ?? '',
  })
  deptDialogVisible.value = true
  void nextTick(() => deptFormRef.value?.clearValidate())
}

const submitDept = async (): Promise<void> => {
  const valid = await deptFormRef.value?.validate().catch(() => false)
  if (!valid) return
  deptSubmitting.value = true
  try {
    const payload = {
      name: deptForm.name.trim(),
      parentId: deptForm.parentId,
      leader: deptForm.leader.trim() || null,
      sort: deptForm.sort,
      status: deptForm.status,
      remark: deptForm.remark.trim() || null,
    }
    if (deptMode.value === 'create') {
      await createDept(payload)
      ElMessage.success('新增部门成功')
    } else if (editingDeptId.value !== null) {
      await updateDept(editingDeptId.value, payload)
      ElMessage.success('修改部门成功')
    }
    deptDialogVisible.value = false
    await fetchDepts()
    await fetchList()
  } finally {
    deptSubmitting.value = false
  }
}

const handleDeptDelete = async (node: DeptNode): Promise<void> => {
  await ElMessageBox.confirm(`确定删除部门「${node.label}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteDept(node.value)
  ElMessage.success('删除部门成功')
  if (activeDeptId.value === node.value) {
    activeDeptId.value = null
    query.deptId = null
    treeRef.value?.setCurrentKey(null)
  }
  await Promise.all([fetchDepts(), fetchList()])
}

/** 与相邻部门交换位置，整组排序值一次性提交，避免只成功一半。 */
const moveDept = async (node: DeptNode, direction: -1 | 1): Promise<void> => {
  const siblings = siblingsOf(node)
  const index = siblings.findIndex((dept) => dept.id === node.value)
  const targetIndex = index + direction
  if (index < 0 || targetIndex < 0 || targetIndex >= siblings.length) return
  const reordered = [...siblings]
  const moved = reordered[index]
  const target = reordered[targetIndex]
  if (!moved || !target) return
  reordered[index] = target
  reordered[targetIndex] = moved
  const items: DeptSortItem[] = reordered.map((dept, position) => ({
    id: dept.id,
    sort: (position + 1) * 10,
  }))
  await reorderDepts(items)
  await fetchDepts()
  ElMessage.success('部门排序已更新')
}

// ---------- 员工表单 ----------

const employeeDialogVisible = ref(false)
const employeeMode = ref<'create' | 'edit'>('create')
const employeeSubmitting = ref(false)
const employeeFormRef = ref<FormInstance>()
const editingEmployeeId = ref<number | null>(null)
const userOptions = ref<UserOption[]>([])

interface EmployeeForm {
  code: string
  name: string
  deptId: number | null
  position: string
  mobile: string
  entryDate: string
  status: number
  userId: number | null
  remark: string
}

const emptyEmployeeForm = (): EmployeeForm => ({
  code: '',
  name: '',
  deptId: activeDeptId.value,
  position: '',
  mobile: '',
  entryDate: '',
  status: 1,
  userId: null,
  remark: '',
})

const employeeForm = reactive<EmployeeForm>(emptyEmployeeForm())
const employeeRules: FormRules = {
  name: [{ required: true, message: '请输入员工姓名', trigger: 'blur' }],
  deptId: [{ required: true, message: '请选择所属部门', trigger: 'change' }],
}

const fetchUserOptions = async (): Promise<void> => {
  userOptions.value = await getUserOptions()
}

/** 已被其他员工占用的账号不可选；当前编辑员工自己的账号保持可选。 */
const isUserDisabled = (option: UserOption): boolean =>
  option.linkedEmployeeId !== null && option.linkedEmployeeId !== editingEmployeeId.value

const openEmployeeCreate = (): void => {
  employeeMode.value = 'create'
  editingEmployeeId.value = null
  Object.assign(employeeForm, emptyEmployeeForm())
  employeeDialogVisible.value = true
  void nextTick(() => employeeFormRef.value?.clearValidate())
  void fetchUserOptions().catch(() => {
    userOptions.value = []
  })
}

const openEmployeeEdit = (row: Employee): void => {
  employeeMode.value = 'edit'
  editingEmployeeId.value = row.id
  Object.assign(employeeForm, {
    code: row.code,
    name: row.name,
    deptId: row.deptId,
    position: row.position ?? '',
    mobile: row.mobile ?? '',
    entryDate: row.entryDate ?? '',
    status: row.status,
    userId: row.userId,
    remark: row.remark ?? '',
  })
  employeeDialogVisible.value = true
  void nextTick(() => employeeFormRef.value?.clearValidate())
  void fetchUserOptions().catch(() => {
    userOptions.value = []
  })
}

const submitEmployee = async (): Promise<void> => {
  const valid = await employeeFormRef.value?.validate().catch(() => false)
  if (!valid || employeeForm.deptId === null) return
  employeeSubmitting.value = true
  try {
    const payload = {
      code: employeeForm.code.trim() || null,
      name: employeeForm.name.trim(),
      deptId: employeeForm.deptId,
      position: employeeForm.position.trim() || null,
      mobile: employeeForm.mobile.trim() || null,
      entryDate: employeeForm.entryDate || null,
      status: employeeForm.status,
      userId: employeeForm.userId,
      remark: employeeForm.remark.trim() || null,
    }
    if (employeeMode.value === 'create') {
      await createEmployee(payload)
      ElMessage.success('新增员工成功')
    } else if (editingEmployeeId.value !== null) {
      await updateEmployee(editingEmployeeId.value, { ...payload, code: employeeForm.code.trim() })
      ElMessage.success('修改员工成功')
    }
    employeeDialogVisible.value = false
    await Promise.all([fetchList(), fetchDepts()])
  } finally {
    employeeSubmitting.value = false
  }
}

const handleEmployeeDelete = async (row: Employee): Promise<void> => {
  await ElMessageBox.confirm(`确定删除员工「${row.name}」吗？`, '删除确认', {
    type: 'warning',
  })
  await deleteEmployee(row.id)
  ElMessage.success('删除员工成功')
  await Promise.all([fetchList(), fetchDepts()])
}

/** 停用后新单据不可选，历史单据仍按原档案展示。 */
const handleEmployeeStatusChange = async (row: Employee, value: unknown): Promise<void> => {
  const status = Number(value)
  try {
    await updateEmployee(row.id, { status })
    row.status = status
    ElMessage.success(status === 1 ? '已启用' : '已停用')
  } catch {
    void fetchList()
  }
}

onMounted(() => {
  void Promise.all([fetchDepts(), fetchList()])
})
</script>

<template>
  <div class="page-container employee-page">
    <!-- 左侧：部门树 -->
    <div class="dept-pane">
      <BaseCard title="部门架构" subtitle="支持多级部门、排序与引用保护" :loading="treeLoading">
        <div class="dept-toolbar">
          <el-button
            v-perm="'base:dept:add'"
            type="primary"
            link
            :icon="'Plus'"
            @click="openDeptCreate()"
          >
            顶级部门
          </el-button>
          <el-button link @click="handleDeptClear">全部员工</el-button>
        </div>
        <el-tree
          ref="treeRef"
          class="dept-tree"
          :data="deptTree"
          node-key="value"
          :props="{ label: 'label', children: 'children' }"
          default-expand-all
          highlight-current
          :expand-on-click-node="false"
          :current-node-key="activeDeptId"
          @current-change="handleDeptSelect"
        >
          <template #default="{ data }">
            <div class="dept-node">
              <div class="dept-node__head">
                <span class="dept-node__label">{{ data.label }}</span>
                <el-tag v-if="data.raw.status === 0" size="small" type="info">停用</el-tag>
                <el-tag size="small" type="success" effect="plain">
                  {{ data.raw.employeeCount }} 人
                </el-tag>
              </div>
              <div class="dept-node__actions" @click.stop>
                <el-button
                  v-perm="'base:dept:add'"
                  link
                  size="small"
                  type="primary"
                  @click="openDeptCreate(data.value)"
                >
                  子类
                </el-button>
                <el-button
                  v-perm="'base:dept:edit'"
                  link
                  size="small"
                  @click="openDeptEdit(data)"
                >
                  编辑
                </el-button>
                <el-button
                  v-perm="'base:dept:edit'"
                  link
                  size="small"
                  :disabled="isFirstSibling(data)"
                  @click="moveDept(data, -1)"
                >
                  ↑
                </el-button>
                <el-button
                  v-perm="'base:dept:edit'"
                  link
                  size="small"
                  :disabled="isLastSibling(data)"
                  @click="moveDept(data, 1)"
                >
                  ↓
                </el-button>
                <el-tooltip
                  :disabled="canDeleteDept(data)"
                  content="存在子部门 / 员工 / 用户引用，无法删除"
                >
                  <el-button
                    v-perm="'base:dept:delete'"
                    link
                    size="small"
                    type="danger"
                    :disabled="!canDeleteDept(data)"
                    @click="handleDeptDelete(data)"
                  >
                    删除
                  </el-button>
                </el-tooltip>
              </div>
            </div>
          </template>
          <template #empty>
            <el-empty description="暂无部门" :image-size="60" />
          </template>
        </el-tree>
      </BaseCard>
    </div>

    <!-- 右侧：员工查询与列表 -->
    <div class="list-pane">
      <BaseCard title="查询条件" subtitle="按部门、状态与关键字筛选员工档案">
        <el-form class="query-form" :inline="true" label-width="72px" @submit.prevent="handleSearch">
          <el-form-item label="关键字">
            <el-input
              v-model="query.keyword"
              placeholder="编码 / 姓名 / 岗位 / 手机"
              clearable
              style="width: 210px"
              @keyup.enter="handleSearch"
            />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="query.status" placeholder="全部" clearable style="width: 120px">
              <el-option label="启用" :value="1" />
              <el-option label="停用" :value="0" />
            </el-select>
          </el-form-item>
          <el-form-item label="">
            <el-checkbox v-model="query.includeSubDept" :disabled="query.deptId === null">
              含下级部门
            </el-checkbox>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="'Search'" @click="handleSearch">查询</el-button>
            <el-button :icon="'Refresh'" @click="handleReset">重置</el-button>
            <el-button
              v-perm="'base:employee:add'"
              type="primary"
              :icon="'Plus'"
              @click="openEmployeeCreate"
            >
              新增员工
            </el-button>
          </el-form-item>
        </el-form>
      </BaseCard>

      <BaseCard class="result-card" title="员工列表" body-padding="0" show-divider :loading="loading">
        <el-table :data="rows" border stripe>
          <el-table-column prop="code" label="编码" width="110" />
          <el-table-column prop="name" label="姓名" width="110" />
          <el-table-column label="部门" min-width="130">
            <template #default="{ row }">{{ row.deptName || '-' }}</template>
          </el-table-column>
          <el-table-column label="岗位" min-width="120">
            <template #default="{ row }">{{ row.position || '-' }}</template>
          </el-table-column>
          <el-table-column label="手机" width="130">
            <template #default="{ row }">{{ row.mobile || '-' }}</template>
          </el-table-column>
          <el-table-column label="入职日期" width="120">
            <template #default="{ row }">{{ row.entryDate || '-' }}</template>
          </el-table-column>
          <el-table-column label="关联账号" min-width="130">
            <template #default="{ row }">{{ row.username || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-switch
                v-if="userStore.hasPerm('base:employee:edit')"
                :model-value="row.status"
                :active-value="1"
                :inactive-value="0"
                @change="handleEmployeeStatusChange(row, $event)"
              />
              <el-tag v-else :type="row.status === 1 ? 'success' : 'info'" size="small">
                {{ row.status === 1 ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right" align="center">
            <template #default="{ row }">
              <el-button
                v-perm="'base:employee:edit'"
                link
                type="primary"
                @click="openEmployeeEdit(row)"
              >
                编辑
              </el-button>
              <el-button
                v-perm="'base:employee:delete'"
                link
                type="danger"
                @click="handleEmployeeDelete(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无员工数据" />
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
    </div>

    <!-- 部门新增 / 编辑 -->
    <el-dialog
      v-model="deptDialogVisible"
      :title="deptMode === 'create' ? '新增部门' : '编辑部门'"
      width="560px"
      destroy-on-close
    >
      <el-form ref="deptFormRef" :model="deptForm" :rules="deptRules" label-width="100px">
        <el-form-item label="部门名称" prop="name">
          <el-input v-model="deptForm.name" placeholder="同租户内不可重复" maxlength="64" />
        </el-form-item>
        <el-form-item label="上级部门">
          <el-tree-select
            v-model="deptForm.parentId"
            :data="parentOptions"
            check-strictly
            clearable
            placeholder="留空为顶级部门"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="deptForm.leader" placeholder="可选" maxlength="64" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number
            v-model="deptForm.sort"
            :min="0"
            :max="9999"
            controls-position="right"
          />
          <div class="form-tip">数值越小越靠前，也可用节点上的 ↑ ↓ 调整同级顺序。</div>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="deptForm.status">
            <el-radio :value="1">启用</el-radio>
            <el-radio :value="0">停用</el-radio>
          </el-radio-group>
          <div class="form-tip">停用部门不能再分配给新员工或系统用户。</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="deptForm.remark" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deptDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="deptSubmitting" @click="submitDept">保存</el-button>
      </template>
    </el-dialog>

    <!-- 员工新增 / 编辑 -->
    <el-dialog
      v-model="employeeDialogVisible"
      :title="employeeMode === 'create' ? '新增员工' : '编辑员工'"
      width="680px"
      destroy-on-close
    >
      <el-form
        ref="employeeFormRef"
        :model="employeeForm"
        :rules="employeeRules"
        label-width="100px"
      >
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="员工编码">
              <el-input
                v-model="employeeForm.code"
                :disabled="employeeMode === 'edit'"
                placeholder="留空自动生成"
                maxlength="32"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="姓名" prop="name">
              <el-input v-model="employeeForm.name" placeholder="必填" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属部门" prop="deptId">
              <el-tree-select
                v-model="employeeForm.deptId"
                :data="deptTree"
                check-strictly
                placeholder="请选择部门"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="岗位">
              <el-input v-model="employeeForm.position" placeholder="可选" maxlength="64" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="手机">
              <el-input v-model="employeeForm.mobile" placeholder="可选" maxlength="32" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="入职日期">
              <el-date-picker
                v-model="employeeForm.entryDate"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="关联用户">
              <el-select
                v-model="employeeForm.userId"
                clearable
                filterable
                placeholder="可选，关联后同步用户部门"
                style="width: 100%"
              >
                <el-option
                  v-for="option in userOptions"
                  :key="option.id"
                  :label="option.realName ? `${option.username}（${option.realName}）` : option.username"
                  :value="option.id"
                  :disabled="isUserDisabled(option)"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="状态">
              <el-radio-group v-model="employeeForm.status">
                <el-radio :value="1">启用</el-radio>
                <el-radio :value="0">停用</el-radio>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="employeeForm.remark" type="textarea" :rows="2" maxlength="255" />
            </el-form-item>
          </el-col>
        </el-row>
        <div class="form-tip form-tip--block">
          关联系统用户会同步该用户的所属部门，影响「本部门」数据范围；停用员工只影响新单据可选列表，不影响历史单据。
        </div>
      </el-form>
      <template #footer>
        <el-button @click="employeeDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="employeeSubmitting" @click="submitEmployee">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.employee-page {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.dept-pane {
  width: 300px;
  flex-shrink: 0;
}

.list-pane {
  flex: 1;
  min-width: 0;
}

.dept-toolbar {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-bottom: 8px;
}

.dept-tree :deep(.el-tree-node__content) {
  height: auto;
  padding: 6px 4px;
  align-items: flex-start;
}

.dept-node {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.dept-node__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.dept-node__label {
  font-weight: 500;
}

.dept-node__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0 2px;
}

.query-form {
  margin-bottom: 0;
}

.result-card {
  margin-top: 16px;
}

.card-footer {
  display: flex;
  justify-content: flex-end;
}

.form-tip {
  font-size: 12px;
  line-height: 1.5;
  color: #909399;
}

.form-tip--block {
  padding-left: 100px;
}
</style>
