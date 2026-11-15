# 采购模块验收约定（P-02 起生效，长期固定）

本文件记录已由需求方确认、**不再重复询问**的采购模块开发约定。
适用范围：`backend/app/{models,schemas,repositories,services,api}/**purchase*`、`backend/app/models/pur.py`、`backend/app/db/init_db.py` 采购相关片段，以及 review 上述代码时。

## 1. 商品 / 供应商停用测试用例

- 暂不补充「商品停用」「供应商停用」的自动化测试用例。
- 代码仍需保留 `_assert_supplier`、`_load_order_items` 中的停用校验逻辑（运行时生效）。
- 将来需要时再单独新增用例，不作为当前迭代的验收缺口。

## 2. 税额取整方式

- 金额与税额统一使用 `Decimal.quantize(Decimal("0.01"))`，即 **ROUND_HALF_EVEN**（银行家舍入）。
- **禁止**改为 `ROUND_HALF_UP`。
- 计算口径：行金额 `round(qty × price, 2)`；税额 `Σ(amount - amount / (1 + tax_rate))`，`tax_rate = 0` 时税额为 0。

## 3. 删除订单权限

- 采购订单删除**复用** `pur:order:edit`。
- **禁止**新增独立权限码 `pur:order:delete`。
- 例外说明：采购申请删除使用独立 `pur:apply:delete`（P-01 已确认，与 edit 分离），两者口径不同，不得互相套用。

## 4. `_ensure_permissions` 权限初始化写法

- 维持**通表驱动**（`procurement_specs` 元组遍历：菜单权限码 → 按钮列表）写法。
- **禁止**回退为「P-01 一段 + P-02 再追加一段」的重复写法。
- 新增采购按钮权限时，只在 `procurement_specs` 对应菜单的元组内追加条目。

## 5. 当前迭代状态（P-02 已闭环）

| 项 | 状态 |
|---|---|
| P-02 采购订单后端 | 已验收闭环 |
| 已知 5 处缺陷 | 已修复 |
| Alembic head | `d1a4f8c2b6e9` |
| 前端采购订单页面 | **未开发**，无指令不启动 |

## 6. 采购模块其它固有余量

- `source_apply_id` / `apply_item_id` 仅作**审计溯源**，不参与业务校验（不拦截一张申请单建多张订单）。
- 转单预填 `GET /purchase/orders/from-apply/{applyId}` 为**只读**：不改申请单状态、不落库、不写操作日志。
- 预填单价取 `bd_goods.cost_price`，前端可修改。
- 采购订单不写库存（`inv_*`）与财务（`fin_*`），入库与应付由后续 P-03/P-04 负责。

## 7. 查询参数 status 类型约定（存量缺陷修复，长期固定）

### 7.1 规则

- Query 类 Schema 的 `status` 一律使用 `int | None` + `Field(ge=..., le=...)`：
  `status: int | None = Field(default=None, ge=0, le=1)`
- **禁止**使用 `Literal[0, 1]`：FastAPI 把 query 的 `"1"` 作为字符串传入，Pydantic v2 的
  `Literal[0, 1]` 不做 int 强转，会导致带 `status` 的请求全部 422。
- 与 `CustomerQuery.status`、`EmployeeQuery.status` 的写法保持一致，不新增第二种风格。
- **出参**（如 `GoodsOut.status`）保持原样，不改动；**前端代码不动**。
- 例外：Create / Update 类 Schema 的 `status` 仍可用 `Literal[0, 1] = 1`（JSON body 传的是真 int，不受影响）。

### 7.2 受影响文件（已修复）

`backend/app/schemas/goods.py` → `GoodsQuery.status`

### 7.3 校验规则（固定）

| 请求 | 期望 |
|---|---|
| `GET /base/goods?status=1` | 200，仅返回启用商品 |
| `GET /base/goods?status=0` | 200，仅返回停用商品 |
| `GET /base/goods?status=2` | 422（`Input should be less than or equal to 1`） |
| `GET /base/goods`（不传 status） | 200，返回全部商品 |

### 7.4 归属与验证结论

- 本次为**存量缺陷修复**，不属于 P-01、P-02 范围。
- 验证结果：pytest **272 条全部通过**；`ruff check app tests` 无问题。

## 8. 项目当前状态基线（长期固定，后续开发前先对齐）

### 8.1 约定生效范围

- 本文件第 1~7 节全部条款**已生效**，后续开发、评审直接遵循，**不再重复确认**。
- 第 7.1 条例外条款保持现状：`GoodsCreate.status` / `GoodsUpdate.status` 继续用 `Literal[0, 1]`，不改。

### 8.2 运行环境

| 项 | 值 |
|---|---|
| 后端 | `http://localhost:8000`，PID 16276 |
| 前端 dev server | `http://localhost:5173`，PID 22340 |
| 数据库 | PostgreSQL `127.0.0.1:5432/erp`，可连 |
| Alembic head | `d1a4f8c2b6e9`（固定，勿回退） |
| 测试基线 | 272 条全部通过 |
| 静态检查 | `ruff check app tests` 无报错 |

### 8.3 待提交变更清单（未经指令**禁止**执行 git 提交）

**已修改（M）**

- `backend/app/models/pur.py`
- `backend/app/api/v1/router.py`
- `backend/app/db/init_db.py`
- `backend/app/schemas/goods.py`
- `backend/app/schemas/auth.py`
- `backend/app/services/auth.py`
- `backend/pyrightconfig.json`
- `backend/tests/test_dept_employee.py`
- `frontend/src/types/auth.ts`

**新增（??）**

- `backend/alembic/versions/d1a4f8c2b6e9_p02_pur_order_source_apply.py`
- `backend/app/api/v1/endpoints/purchase_apply.py`
- `backend/app/api/v1/endpoints/purchase_order.py`
- `backend/app/repositories/purchase_apply.py`
- `backend/app/repositories/purchase_order.py`
- `backend/app/schemas/purchase_apply.py`
- `backend/app/schemas/purchase_order.py`
- `backend/app/services/purchase_apply.py`
- `backend/app/services/purchase_order.py`
- `backend/tests/test_purchase_apply.py`
- `backend/tests/test_purchase_order.py`
- `frontend/src/api/purchaseApply.ts`
- `frontend/src/types/purchaseApply.ts`
- `frontend/src/views/purchase/apply/index.vue`（目录 `frontend/src/views/purchase/`）

### 8.4 缺陷台账

| 编号 | 内容 | 状态 |
|---|---|---|
| D1 | `GoodsQuery.status` 用 `Literal` 导致 query 带 status 一律 422 | **已修复闭环** |
| D2 | 后端未启动导致 `ERR_CONNECTION_REFUSED` | 已启动，维持现状 |
| D3 | 前端采购订单页面未开发 | **无授权不启动** |
| D4 | 部分新文件 black 与 ruff `lines-after-imports` 冲突 | 维持现状，暂不处理 |
| D5 | pytest 存在 1 条 alembic `path_separator` DeprecationWarning | 维持现状，暂不处理 |

### 8.5 工作纪律

- 未接到新指令前，**不自动扩展任务**、不主动重构、不主动提交。
- 新任务到达时，先按 system prompt 输出改动清单，等待确认后再动手。


