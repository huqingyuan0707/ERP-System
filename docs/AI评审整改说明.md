# GitHub AI 评审整改说明

## 1. 文档范围

- 分支：`fix/b01-b05-business-rules`
- 证据基线：2026-11-02 当前工作树；本文不把未提交改动描述为已合并或已部署。
- 整改范围：仅说明 GitHub AI 评审评论的核验、代码现状、补充测试与待验收项；不扩大 B-01～B-05 的业务范围。
- 核验约束：未读取开发 `.env`，未读取 `PGSql.md`，未连接或操作现有 PostgreSQL，未提交代码。

## 2. 评审评论的核验方法

AI 评审适合作为风险线索：它能提示迁移、事务、权限、配置等高影响区域，但路径命中或通用模式本身不等于已确认缺陷。本轮对每条评论采用以下方式核验：

1. 定位评论对应的 Router、Service、Repository、Model、迁移和测试，而不是仅凭 diff 片段判断。
2. 沿实际调用链核对租户条件、权限依赖、事务提交点、回滚路径、唯一约束和返回类型。
3. 区分“真实缺陷并已修复”“当前实现已满足、评论不适用于现状”“实现已有但证据不足，需补测试”“超出本次已确认需求边界”。
4. 对可本地隔离验证的内容运行定向测试；真实 PostgreSQL 并发和迁移由独立 PG16 CI 验证，不用 SQLite 结果替代。

`scripts/ai_review.py` 的路径风险规则现用于提示变更影响面和提高审查强度，不再仅因命中高风险路径就自动认定缺陷或阻断。当前门禁只有在模型给出可定位到文件、包含具体问题描述的 `major` / `critical` 问题，且总体结论为 `HIGH` 时才阻断；只有风险等级、路径命中或泛化建议不会单独阻断。

## 3. 评论逐条核验与整改证据

### 3.1 迁移对重复值、非规范值是否只告警后继续执行

**核验结论：当前实现会阻断，不会带着已知重复或非规范键继续建约束。**

- `backend/alembic/versions/f2b6c8d0e1a3_base_data_safety.py` 的 `_preflight()` 收集活动条码、根分类名称的重复值，以及条码、分类名称的首尾空白等非规范值。
- 发现上述 blocker 时明确 `raise RuntimeError`，错误中列出待人工核对的行 ID。
- `upgrade()` 先执行 PostgreSQL 表锁和 `_preflight()`，之后才调用 `op.add_column()`、`op.create_index()`；因此阻断检查位于 DDL 之前。
- 跨租户历史引用和异常数值属于只报告、不自动改历史数据的审慎策略，不能与重复键／非规范键的阻断逻辑混为一谈。

状态：实现已满足；迁移安全测试继续保留。新导出权限迁移另有 5 项幂等专项测试。

### 3.2 跨租户 RBAC 写入与授权

**核验结论：这是实际安全问题，当前工作树已修复。**

- 用户创建、更新及用户—角色重建会验证用户、角色、部门均属于当前租户。
- 角色数据范围和角色—权限重建会先验证角色属于当前租户；部门关联同时校验部门租户。
- 鉴权查询只接受当前用户租户内、启用且未删除的角色；外租户角色和禁用角色不再授予角色、菜单、权限或更宽数据范围。
- `resolve_scope()` 的角色、部门及递归部门树查询均带当前租户条件。
- 权限定义表本身为全租户共享字典；租户边界落实在角色以及角色关联写入上，而不是给全局权限码虚构 `tenant_id`。

证据：`backend/tests/test_rbac_tenant_security.py`、`backend/tests/test_permissions.py`、`backend/tests/test_data_scope.py` 覆盖跨租户角色／部门／用户关联、禁用角色即时失权和数据范围解析。

### 3.3 库存事务、行锁以及库存流水是否同事务

**核验结论：关于“没有事务／行锁／流水同事务”的泛化判断不适用于当前实现；真实 PostgreSQL 并发证据仍需 CI 闭环。**

- `InventoryRepository` 对仓库、商品、单位和库存行使用 `SELECT ... FOR UPDATE`，并按 `tenant_id` 限定。
- `InventoryService` 在同一个 `AsyncSession` 中修改 `inv_stock`、新增 `inv_stock_flow`，先 `flush()`，再由同一事务提交；异常路径回滚。
- 库存接口在幂等包装中以 `commit=False` 调用库存服务，由 `IdempotencyService.execute()` 将库存、流水、幂等状态和缓存结果一次提交，避免内层提前提交。
- 库存写入强制显式租户级幂等键；库存流水使用账面数量前后值，出库按变更前平均成本结转，锁定／释放不写入出入库流水。
- `inv_stock` 增加数量非负、可用量恒等与平均成本非负 CHECK；升级前发现历史违规行则按 ID 阻断，不自动修数。
- 新增 `backend/tests/test_postgres_concurrency.py`，覆盖并发出库／锁库、同幂等键重叠请求，以及商品删除或换单位与库存写入竞态。

状态：实现链路和本地非 PG 测试已核验；真实 PG16 多连接测试由 CI 待跑。本地 Docker 不可用，因此本文不把它写成已通过。

### 3.4 幂等是否只是进程内状态，是否缺少唯一约束或原子事务

**核验结论：当前实现为数据库持久化幂等，不是进程内字典。**

- `idem_idempotency_key` 由 Alembic 历史迁移创建，`key` 有数据库唯一索引 `uq_idempotency_key`。
- 记录持久化请求哈希、状态、响应体和过期时间；同键异参返回冲突，成功重放返回缓存结果，失败或过期可按规则重试。
- PostgreSQL 路径使用事务级 advisory lock，数据库唯一约束同时作为并发兜底。
- 业务操作以 `commit=False` 执行，成功状态／响应缓存与业务数据同一事务提交；任一步失败整体回滚。

证据：`backend/tests/test_goods_rules.py` 已覆盖重放、异参冲突、失败回滚、重试、过期和唯一冲突；真实同键并发另由 PG16 CI 用例验证。

### 3.5 导出权限、租户与 owner；`data_scope` 的适用边界

**核验结论：确定的权限缺陷已修；基础资料／当前库存是否按创建人或部门裁剪属于需先确认的数据归属需求，不按泛化告警直接改变业务可见性。**

- 商品、库存、通用报表、对账单四类入口分别要求明确权限；对账单已从误用 `report:export` 修正为 `fin:statement:export`。
- 新迁移 `d4e9a1b7c3f5_export_permissions.py` 幂等补齐 `inv:stock:export`、`report:export`、`fin:statement:export`，并给每个租户中启用、未删除的 `admin` 角色补授权，不覆盖历史权限行。
- 异步任务查询按租户过滤；下载还要求任务 owner，只有 `*` 可在同租户内下载他人文件。`sys:task:view` 只扩大任务可见范围，不隐式授予他人文件下载权。
- 商品和库存导出查询对商品、分类、单位、仓库和库存分别带 `tenant_id` 条件。
- 当前 B-01～B-05 已确认边界要求基础资料和当前库存租户隔离、接口权限及导出 owner 校验；没有已确认规则要求把共享主数据或当前库存按“创建人／创建人部门”隐藏。机械套用单据型 `data_scope` 会改变业务语义。若后续确认需要部门级库存或基础资料可见范围，应先定义仓库／资料的归属字段和跨部门共享规则，再作为跨模块需求验收。

证据：`backend/tests/test_goods_transfer.py` 覆盖四类导出精确权限、租户与 owner 下载、完整导出及关联表租户隔离；`backend/tests/test_export_permission_migration.py` 覆盖权限迁移幂等与多租户 admin 授权。

### 3.6 Excel 是否返回 ORM、Decimal 是否丢精度

**核验结论：外部响应不返回 ORM，Decimal 以文本写入 Excel。**

- 导出服务可以在内部查询 ORM 行，但逐列提取编码、名称、数量等标量后交给 `ExcelWriter`，不会把 ORM 实例写入工作簿或返回给接口。
- HTTP 接口返回 `TaskOut`，不是 SQLAlchemy 对象。
- 价格、数量、成本等 `Decimal` 使用 `str(...)` 写入；`ExcelWriter` 把字符串显式标记为文本，避免经过 Excel 约 15 位数值精度或被解释为公式。

状态：当前实现满足；已有大数据完整导出、租户关联和资源上限专项测试。

### 3.7 `init_db` 是否用 `stamp` 假装迁移，权限补种是否幂等

**核验结论：空库执行真实迁移，已有库不自动 `stamp`。**

- 完全空库通过 `alembic upgrade head` 执行真实迁移，再核对当前 revision 是否到达唯一 head。
- 已有未纳管库、版本落后、多个 revision 或迁移失败均 fail-fast；不会用 `create_all` 或 `stamp` 掩盖差异。
- 应用补种和导出权限迁移均先查存在性再插入；管理员授权按所有租户的有效 `admin` 角色补齐，并避免重复关联。
- CI 的数据库迁移任务在全新 PG16 上执行初始化、再次初始化、`alembic check` 及最新 revision 降级／升级往返。

状态：本地只做隔离测试；真实 PostgreSQL 迁移链由 CI 验证。

### 3.8 CI 是否覆盖 PostgreSQL、Ruff 和前端检查

**核验结论：当前工作流已补齐对应 job。**

- SQLite pytest：Python 3.11、3.12，显式排除 `postgres` marker。
- PostgreSQL 并发：PG16 服务、真实迁移到 head、运行专用多连接并发用例。
- PostgreSQL 迁移：空库真实初始化与种子幂等、`alembic check`、最新 revision 往返。
- 后端 Ruff：`app` 与 `tests` 全量阻断检查；本轮已清理既有基线。
- 前端：锁文件安装、`vue-tsc`、ESLint、生产构建。

状态：工作流配置已存在；远端 CI 结果仍以 GitHub 实际 run 为准。

### 3.9 生产配置是否 fail-fast

**核验结论：生产模式已增加启动期安全校验。**

- `ENVIRONMENT=production` 时拒绝空值、常见占位或不足 32 字符的 `SECRET_KEY`。
- 拒绝空连接、包含 `change_me` 或 SQLite 的生产 `DATABASE_URL`。
- 要求显式配置至少 8 位且同时含字母和数字的初始管理员密码。
- 开发／隔离测试仍保留兼容默认值，避免把生产约束错误施加到本地测试。

证据：`backend/tests/test_config_security.py` 21 项通过。

### 3.10 `ExportButton` 中的 `void` 是否违反前端约束

**核验结论：当前用法是合法 TypeScript，未使用会触发既有问题的模板 `void` 表达式。**

- 项目约束禁止的是模板事件中的形式，例如 `@success="void fetchList"`。
- 当前模板绑定为 `@click="handleClick"`；`void submitExport()`、`void pollTask(...)` 位于 `<script setup lang="ts">`，用于显式忽略 Promise 返回值，是合法且常见的 TypeScript 用法。
- 前端 type-check、ESLint 和生产构建已有通过记录。

状态：无需为该评论改代码。

### 3.11 “每行有效代码都写中文注释”告警

**核验结论：该文字要求不是当前工具链可执行、可客观判定的质量门禁，本轮遵循相邻代码风格和可维护性原则。**

- 当前 Ruff、ESLint、TypeScript 和 CI 均没有可验证“每行中文注释语义正确”的规则。
- 本轮保留模块／类／函数用途说明，并在事务、租户、并发和权限等非显然分支写原因注释；不为满足泛化告警批量添加机械注释或改动无关代码。
- 若项目后续决定把该要求升级为强制验收项，应先定义可执行检查、例外和适用范围，再单独治理；不应仅凭通用评论把当前功能整改判为失败。

## 4. 当前测试与检查记录

以下是阶段性、可追溯的独立批次，测试集合可能重叠，不能把数字直接相加成“当前完整回归总数”。

| 批次 | 当前记录 | 口径 |
|---|---:|---|
| Python 3.12 最终非 PostgreSQL 完整回归 | `254 passed` | `-m "not postgres"`，包含本轮全部安全与库存约束用例 |
| 生产配置专项 | `21 passed` | 已包含在完整回归中 |
| 导出权限迁移专项 | `5 passed` | 已包含在完整回归中 |
| 前端 type-check | 通过 | 覆盖 `ExportButton` 当前 `void` 用法 |
| 前端 ESLint | 通过 | 不修改文件 |
| 前端生产构建 | 通过 | 仍有既有大 chunk 提示 |
| 本轮全部 Python Ruff | 通过 | CI 对 `app` 与 `tests` 全量阻断 |
| `git diff --check` | 通过 | 本次文档写入后重跑 |

当前新增安全与库存约束用例已纳入同一次完整 SQLite 回归，不再把阶段性数字相加。真实 PostgreSQL 用例仍由独立 CI 验收。

## 5. 尚待闭环

- 真实 PostgreSQL 空库迁移、revision 往返、`alembic check` 和多连接并发仍以远端 CI 结果为准。
- GUI 交互仍未验收通过。
- 采购／销售单据中的停用限制与默认仓、未结单据引用链、销售审核信用校验、出入库生成应收应付、B-06 员工关联，以及真实报表／对账单数据接入仍属于跨模块待验收项。

## 6. 结论

本轮没有把 AI 评论简单归为“对”或“错”：真实的跨租户 RBAC、对账单权限、生产配置和 CI 证据缺口已落实整改；对迁移、库存事务、幂等、Excel 和前端 `void` 等泛化告警则通过实际调用链与测试逐项核验。仍依赖真实 PostgreSQL 或 GUI 的项目明确保持未验收状态，不以本地 SQLite 或静态阅读替代最终结果。
