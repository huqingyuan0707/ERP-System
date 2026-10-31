# ERP 项目代码风格规则

适用范围：本仓库全部前后端代码。技术栈固定，不随任务变更。

前端：Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus + Axios + ECharts
后端：Python 3.11+ + FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic + PostgreSQL + Redis
部署：Docker + Docker Compose + Nginx

## 1. 总则

1. 前后端分离，接口优先：先定义后端 Pydantic Schema 与 OpenAPI，再生成前端 TypeScript 类型与 API 方法。
2. API JSON 字段一律 camelCase；Python 代码、数据库表名、数据库字段一律 snake_case；两者通过 Pydantic alias 转换。
3. 类型必须明确，禁止 `any`、禁止隐式类型。
4. 业务逻辑只放在 Service 层；Router 只做参数校验与调度；前端组件只做展示与交互。
5. 新增接口必须同步更新：后端 Schema / Service / Router + 前端 types / api / 页面调用。
6. 同一模块禁止混用两种风格；与既有代码冲突时以既有代码为准，小步统一。
7. 输出代码必须完整可运行，不省略关键 import、类型、异常处理。
8. 今后新增或修改代码时，必须自动为每一行有效代码添加中文注释，无需用户再次提醒（规范见第 12 节）；用大白话说明用途、语法要点和必要的业务原因。

## 2. 命名规范

### 2.1 前端

- 组件文件、页面文件：PascalCase（`ProductTable.vue`、`ProductList.vue`）
- 目录：kebab-case（`purchase-order/`）
- 变量、函数：camelCase；常量：UPPER_SNAKE_CASE
- 函数统一使用箭头函数：`const fnName = (args) => {}`，异步用 `const fnName = async (args) => {}`；禁止 `function` 声明（api / utils / stores / directives / 组件内方法一律适用）
- 类型、接口、枚举：PascalCase；枚举成员：UPPER_SNAKE_CASE
- Pinia Store：`useXxxStore`，文件 `xxx.ts`
- API 方法：`getProductList` / `getProductDetail` / `createProduct` / `updateProduct` / `deleteProduct` / `batchDeleteProduct`
- 类型后缀：`Product` / `ProductCreatePayload` / `ProductUpdatePayload` / `ProductQuery` / `PageResult<T>`

### 2.2 后端

- 模块、文件：snake_case（`purchase_order.py`）
- 类：PascalCase（`PurchaseOrderService`）；函数、变量：snake_case；常量：UPPER_SNAKE_CASE
- Pydantic Schema 后缀：`ProductCreate` / `ProductUpdate` / `ProductOut` / `ProductQuery` / `PageResult`
- SQLAlchemy Model：无后缀（`Product`）；Service：`ProductService`；Repository：`ProductRepository`
- 路由函数：`list_products` / `get_product` / `create_product` / `update_product` / `delete_product`

## 3. 目录结构

### 3.1 前端 src/

```text
src/
  api/                # 接口请求，按模块拆分
  types/              # TypeScript 类型
  stores/             # Pinia 全局状态
  views/              # 页面
  components/         # 公共组件
  composables/        # 组合式函数
  utils/              # 工具函数（request.ts 等）
  router/             # 路由
  directives/         # 自定义指令，如 v-permission
  styles/             # 全局样式
```

### 3.2 后端 backend/app/

```text
app/
  main.py             # FastAPI 实例、路由/中间件/异常注册
  core/               # config / security / deps / exceptions
  db/                 # base.py / session.py
  models/             # SQLAlchemy 模型，按模块拆分
  schemas/            # Pydantic v2 模型
  repositories/       # 数据访问层
  services/           # 业务逻辑层
  api/v1/             # router.py + endpoints/
  tasks/              # Celery
  utils/              # 单号生成、Excel、分页、响应包装
  tests/
```

## 4. 分层与依赖

- 依赖方向单向：`api → services → repositories → models`；`schemas` 供 api / services 使用。
- 禁止反向依赖：service 不得 import api；repository 不得 import service；model 不得 import 上层。
- 事务边界在 Service：一次业务操作一个事务，禁止在 Router 或 Repository 里开启业务事务。

## 5. 接口与响应

- 前缀统一 `/api/v1`；成功响应 `{code: 0, message: "success", data: {...}}`。
- 分页响应：`data: {total, page, page_size, items}`；查询参数统一 `page`、`page_size`、`keyword`、`start_date`、`end_date`。
- 认证：`Authorization: Bearer <token>`；权限用依赖 `require_perm("模块:资源:动作")`，如 `pur:order:add`。
- 错误码集中在 `core/exceptions.py`；业务异常抛 `AppException(code, message)`，由全局处理器转换。

## 6. Pydantic v2

- 后缀固定：`Create` / `Update` / `Out` / `Query` / `Page`。
- 统一配置：`model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=to_camel)`。
- 出参一律用 `Out`，禁止直接返回 ORM 对象或数据库原始字段。
- `Update` 模型字段全部 `Optional`，语义为部分更新。

## 7. SQLAlchemy 2.0

- 使用 `Mapped[...] + mapped_column(...)` 全类型注解。
- 公共字段用 Mixin：`TimestampMixin`(created_at/updated_at)、`UserMixin`(created_by/updated_by)、`SoftDeleteMixin`(is_deleted)、`VersionMixin`(version)。
- 表名 snake_case + 模块前缀：`sys_` / `bd_` / `pur_` / `sal_` / `inv_` / `fin_`。
- 关系用 `relationship(back_populates=...)`；异步 session，查询统一 async。
- 复杂查询封装在 Repository，禁止在 Service 直接拼 SQL。

## 8. 业务一致性规则

- 库存变动只走 `InventoryService`，同一事务内写 `inv_stock` 与 `inv_stock_flow`。
- 单据确认（采购入库 / 销售出库 / 调拨 / 盘点）必须事务 + 行锁。
- 入库确认生成应付、出库确认生成应收，由 `FinanceService` 负责。
- 单据编号由 `utils` 统一生成，禁止手工拼接编号。

## 9. 前端规范

- 统一 `<script setup lang="ts">` 组合式 API，禁止与 Options API 混用。
- `utils/request.ts` 统一封装 Axios：注入 token、统一错误提示、Loading、401 跳登录。
- 每个模块包含：`types/xxx.ts`、`api/xxx.ts`、`views/模块/`，全局状态才进 `stores/`。
- 按钮级权限用 `v-permission` 指令；字典类字段走 Pinia 缓存，避免重复请求。
- 列表页统一结构：查询表单 + 分页表格 + 导出 Excel + 按钮权限。
- 模板事件绑定中禁止使用 `void` 表达式（如 `@success="void fetchList"`）：会触发 vue-tsc 内部崩溃，导致 `type-check` 无法完成；直接写方法名 `@success="fetchList"`。
- 组件 props 默认值为对象/数组时必须用工厂函数（`params: () => ({})`）；`v-model` 不得直接绑定只读 prop，需用 `computed` 代理并通过 `emit` 回写。

## 10. import 顺序

- Python：标准库 → 第三方 → 本地模块，组内按字母序（ruff/isort 管理）。
- 前端：vue / vue-router → 第三方 → `@/` 别名 → 相对路径 → 样式。
- 使用 `@/` 路径别名，禁止超过两层的 `../../`。

## 11. 错误处理与日志

- 不吞异常；由全局异常处理器统一转为 `{code, message}`。
- 日志使用结构化 logger，禁止 `print`；关键操作写 `sys_oper_log`。
- 生产环境禁止向前端返回堆栈信息。

## 12. 代码注释

- 强制逐行注释：今后编写代码时，默认自动为每一行有效代码添加中文注释，无需用户再次提醒；适用于本仓库全部前后端代码。
- 新增文件覆盖全部有效代码行；修改文件覆盖本次新增或修改的代码行，不为补注释擅自改动无关代码。空行、已有注释和说明性 docstring 无需重复添加注释。
- 注释面向初学者，用大白话说明每行的用途、字段含义、默认值、校验规则或语法要点；必要时解释业务原因，不使用「赋值」「循环」等没有实际信息的描述。
- 优先使用行尾注释，过长时放在对应代码行上方。必须使用当前语言支持的注释语法；无法合法插入行内注释的多行标签、字符串或不支持注释的格式，应在合法的相邻位置或随附说明中按行解释，不得破坏语法、数据内容或运行行为。
- 新增或修改的模块、类、函数、接口仍须保留用途说明，逐行注释不能替代以下文档注释。
- Python：模块头 docstring、类 docstring、函数 docstring（参数与返回值语义、可能抛出的业务异常）。
- 前端：文件头块注释（`/** ... */`）、组件 `<script setup>` 顶部用途说明、类型/接口字段含义、复杂逻辑与权限分支就地注释。
- 注释使用中文，与代码库语言保持一致；注释中出现的编码、字段名、路径、表名必须与实际一致。
- 在说明每行用途和语法的基础上，优先补充「为什么」和规则来源，例如「重置密码后递增 `token_version`，使旧 Token 立即失效」，避免机械复述代码。
- 改动既有代码时同步更新过期注释；注释与实现不一致时以实现为准，并立即修正注释。
- 添加注释不得改变业务逻辑，仍须遵守行宽、格式化和 lint 规则。
- 禁止无意义注释（`# 给变量赋值`、`# 循环`）、禁止提交被注释掉的死代码、禁止留下无责任人的 `TODO`。

## 13. 代码质量工具

- 后端：ruff + black + isort + pytest(httpx)。
- 前端：ESLint + Prettier + Vitest + Playwright。
- 提交前必须通过 lint 与类型检查。

## 14. Git 提交规范

- 格式：`type(scope): subject`，type ∈ feat / fix / refactor / docs / test / chore / perf。
- 一个提交只做一件事；提交信息语言保持一致。
- 禁止提交密钥、`.env` 真实值、临时产物。

## 15. 红线（禁止事项）

- 禁止 `any` / `@ts-ignore` / 滥用类型断言。
- 禁止在 Router 写业务逻辑、在组件写业务规则。
- 禁止绕过 Service 直接改库存、应收应付表。
- 禁止手工修改生产库结构（一律走 Alembic）。
- 禁止同一模块两种风格并存。
- 禁止提交未按第 12 节逐行注释要求处理的新增或修改代码；禁止保留与实现不一致的过期注释。
