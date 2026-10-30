<script setup lang="ts">
/**
 * 角色权限页（C-04 前端）。
 * 结构：角色列表 + 新增/编辑 + 删除 + 数据范围设置（仅本人/本部门/全部，本部门可叠加自定义部门）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'

import { assignRoleDataScope, createRole, deleteRole, getRoleList, updateRole } from '@/api/role'
import { getDeptList } from '@/api/system'
import { DATA_SCOPE_ALL, DATA_SCOPE_DEPT, DATA_SCOPE_SELF } from '@/types/role'
import type { Role } from '@/types/role'
import type { Dept } from '@/types/system'

interface DeptNode {
  value: number
  label: string
  children: DeptNode[]
}

const SCOPE_OPTIONS = [
  { value: DATA_SCOPE_SELF, label: '仅本人' },
  { value: DATA_SCOPE_DEPT, label: '本部门' },
  { value: DATA_SCOPE_ALL, label: '全部' },
]

const loading = ref(false)
const roles = ref<Role[]>([])
const depts = ref<Dept[]>([])

const scopeLabel = (scope: number): string =>
  SCOPE_OPTIONS.find((item) => item.value === scope)?.label ?? String(scope)

const scopeTagType = (scope: number): 'info' | 'warning' | 'success' => {
  if (scope === DATA_SCOPE_ALL) return 'success'
  if (scope === DATA_SCOPE_DEPT) return 'warning'
  return 'info'
}

// ---------- 角色新增/编辑弹窗 ----------
const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'edit'>('create')
const submitting = ref(false)
const formRef = ref<FormInstance>()
const editingId = ref<number | null>(null)

interface RoleForm {
  code: string
  name: string
  remark: string
}

const createEmptyForm = (): RoleForm => ({ code: '', name: '', remark: '' })
const form = reactive<RoleForm>(createEmptyForm())

const rules: FormRules = {
  code: [
    { required: true, message: '请输入角色编码', trigger: 'blur' },
    {
      pattern: /^[a-z][a-z0-9_]*$/,
      message: '小写字母开头，仅含小写字母、数字、下划线',
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: '请输入角色名称', trigger: 'blur' }],
}

// ---------- 数据范围弹窗 ----------
const scopeDialogVisible = ref(false)
const scopeSubmitting = ref(false)
const scopeRoleId = ref<number | null>(null)
const scopeRoleName = ref('')
const scopeForm = reactive<{ dataScope: number; deptIds: number[] }>({
  dataScope: DATA_SCOPE_SELF,
  deptIds: [],
})

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

const fetchRoles = async (): Promise<void> => {
  loading.value = true
  try {
    roles.value = await getRoleList()
  } finally {
    loading.value = false
  }
}

const fetchDepts = async (): Promise<void> => {
  depts.value = await getDeptList()
}

// ---------- 角色 CRUD ----------
const openCreate = (): void => {
  dialogMode.value = 'create'
  editingId.value = null
  Object.assign(form, createEmptyForm())
  dialogVisible.value = true
}

const openEdit = (row: Role): void => {
  dialogMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, { code: row.code, name: row.name, remark: row.remark ?? '' })
  dialogVisible.value = true
}

const handleSubmit = async (): Promise<void> => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const payload = { name: form.name, remark: form.remark || null }
    if (dialogMode.value === 'create') {
      await createRole({ ...payload, code: form.code, dataScope: DATA_SCOPE_SELF })
      ElMessage.success('新增成功，可在「数据范围」中调整可见范围')
    } else if (editingId.value !== null) {
      await updateRole(editingId.value, payload)
      ElMessage.success('修改成功')
    }
    dialogVisible.value = false
    void fetchRoles()
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row: Role): Promise<void> => {
  await ElMessageBox.confirm(`确定删除角色「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await deleteRole(row.id)
  ElMessage.success('删除成功')
  void fetchRoles()
}

// ---------- 数据范围 ----------
const openScope = (row: Role): void => {
  scopeRoleId.value = row.id
  scopeRoleName.value = row.name
  scopeForm.dataScope = row.dataScope
  scopeForm.deptIds = [...row.deptIds]
  scopeDialogVisible.value = true
}

const handleScopeSubmit = async (): Promise<void> => {
  if (scopeRoleId.value === null) return
  scopeSubmitting.value = true
  try {
    await assignRoleDataScope(scopeRoleId.value, {
      dataScope: scopeForm.dataScope,
      deptIds: scopeForm.deptIds,
    })
    ElMessage.success('数据范围已更新')
    scopeDialogVisible.value = false
    void fetchRoles()
  } finally {
    scopeSubmitting.value = false
  }
}

onMounted(() => {
  void fetchRoles()
  void fetchDepts()
})
</script>

<template>
  <div class="page-card">
    <div class="toolbar">
      <el-button v-perm="'sys:role:add'" type="primary" :icon="'Plus'" @click="openCreate">
        新增角色
      </el-button>
    </div>

    <el-table v-loading="loading" :data="roles" border stripe>
      <el-table-column prop="code" label="编码" width="140" />
      <el-table-column prop="name" label="名称" width="160" />
      <el-table-column label="数据范围" min-width="180">
        <template #default="{ row }">
          <el-tag :type="scopeTagType(row.dataScope)" size="small">
            {{ scopeLabel(row.dataScope) }}
          </el-tag>
          <span v-if="row.dataScope === DATA_SCOPE_DEPT && row.deptIds.length > 0" class="dept-count">
            自定义 {{ row.deptIds.length }} 个部门
          </span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80" align="center">
        <template #default="{ row }">
          <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
            {{ row.status === 1 ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="操作" width="220" fixed="right" align="center">
        <template #default="{ row }">
          <el-button v-perm="'sys:role:edit'" link type="primary" @click="openEdit(row)">
            编辑
          </el-button>
          <el-button v-perm="'sys:role:assign'" link type="warning" @click="openScope(row)">
            数据范围
          </el-button>
          <el-button
            v-if="row.code !== 'admin'"
            v-perm="'sys:role:delete'"
            link
            type="danger"
            @click="handleDelete(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无角色数据" />
      </template>
    </el-table>

    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新增角色' : '编辑角色'"
      width="480px"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="角色编码" prop="code">
          <el-input v-model="form.code" :disabled="dialogMode === 'edit'" placeholder="如 purchase_manager" maxlength="64" />
        </el-form-item>
        <el-form-item label="角色名称" prop="name">
          <el-input v-model="form.name" placeholder="如 采购经理" maxlength="64" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" maxlength="255" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="scopeDialogVisible" :title="`数据范围 — ${scopeRoleName}`" width="480px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="数据范围">
          <el-radio-group v-model="scopeForm.dataScope">
            <el-radio v-for="opt in SCOPE_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="scopeForm.dataScope === DATA_SCOPE_DEPT" label="自定义部门">
          <el-tree-select
            v-model="scopeForm.deptIds"
            :data="deptTree"
            multiple
            check-strictly
            collapse-tags
            collapse-tags-tooltip
            placeholder="叠加到本部门范围，可多选"
            style="width: 100%"
          />
          <div class="scope-tip">勾选的部门（含其子部门）会叠加到「本部门」可见范围内。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="scopeDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="scopeSubmitting" @click="handleScopeSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.dept-count {
  margin-left: 8px;
  color: #909399;
  font-size: 12px;
}

.scope-tip {
  width: 100%;
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
  line-height: 1.5;
}
</style>
