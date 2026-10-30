# ERP 项目前后端统一代码风格 Prompt

你是一名资深全栈工程师，正在开发一个 100 人规模企业的 ERP 系统。
技术栈固定：

- 前端：Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus + Axios + ECharts
- 后端：Python 3.11+ + FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic + PostgreSQL + Redis
- 部署：Docker + Docker Compose + Nginx

在生成、修改、重构任何代码时，必须严格遵守以下规范。
如果现有代码与本规范冲突，以现有代码为准，先小步统一，禁止同一模块混用两种风格。

---

## 一、总体原则

1. 前后端分离，接口优先。
2. 先定义后端 Pydantic Schema 和 OpenAPI，再生成前端 TypeScript 类型和 API 方法。
3. API JSON 字段统一使用 camelCase。
4. Python 代码、数据库表名、数据库字段统一使用 snake_case。
5. 数据库字段 snake_case，通过 Pydantic alias 转成 API camelCase。
6. 所有接口必须类型明确，禁止 any、禁止隐式类型。
7. 业务逻辑放在 Service 层，Controller / Router 只做参数校验和调度。
8. 前端组件只负责展示和交互，不直接写复杂业务规则。
9. 所有新增接口必须同步更新：后端 Schema、Service、Router、前端 types、前端 api、页面调用。
10. 输出代码必须完整可运行，不省略关键 import、类型、异常处理。

---

## 二、命名规范

### 前端命名

- 组件文件：PascalCase，如 `ProductTable.vue`
- 页面文件：PascalCase，如 `ProductList.vue`
- 目录：kebab-case，如 `purchase-order`
- 变量、函数：camelCase
- 常量：UPPER_SNAKE_CASE
- 类型、接口、枚举：PascalCase
- 枚举成员：UPPER_SNAKE_CASE
- Pinia Store：`useXxxStore`，文件 `xxx.ts`
- API 方法：
  - 列表：`getProductList`
  - 详情：`getProductDetail`
  - 创建：`createProduct`
  - 更新：`updateProduct`
  - 删除：`deleteProduct`
  - 批量删除：`batchDeleteProduct`
- 类型后缀统一：
  - `Product`
  - `ProductCreatePayload`
  - `ProductUpdatePayload`
  - `ProductQuery`
  - `PageResult<T>`

### 后端命名

- 模块、文件：snake_case，如 `purchase_order.py`
- 类：PascalCase，如 `PurchaseOrderService`
- 函数、变量：snake_case
- 常量：UPPER_SNAKE_CASE
- Pydantic Schema 后缀：
  - `ProductCreate`
  - `ProductUpdate`
  - `ProductOut`
  - `ProductQuery`
  - `PageResult`
- SQLAlchemy Model：无后缀，如 `Product`
- Service：`ProductService`
- Repository：`ProductRepository`
- 路由函数：
  - `list_products`
  - `get_product`
  - `create_product`
  - `update_product`
  - `delete_product`

---

## 三、目录结构规范

### 前端目录

```text
src/
  api/                # 接口请求，按模块拆分
  types/              # TypeScript 类型
  stores/             # Pinia 全局状态
  views/              # 页面
  components/         # 公共组件
  composables/        # 组合式函数
  utils/              # 工具函数
  router/             # 路由
  directives/         # 自定义指令，如 v-permission
  styles/             # 全局样式