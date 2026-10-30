# 企业ERP系统 · FastAPI 后端目录结构（V1.0）

## 目录树
```text
backend/
├─ app/
│  ├─ main.py                 # FastAPI 实例、路由/中间件/异常注册
│  ├─ core/
│  │  ├─ config.py            # pydantic-settings 读取 .env
│  │  ├─ security.py          # 密码加密、JWT 生成校验
│  │  ├─ deps.py              # get_db / get_current_user / require_perm
│  │  └─ exceptions.py        # 统一异常与错误码
│  ├─ db/
│  │  ├─ base.py              # DeclarativeBase
│  │  └─ session.py           # async engine / sessionmaker
│  ├─ models/                 # SQLAlchemy 模型(sys/base/purchase/sales/inventory/finance)
│  ├─ schemas/                # Pydantic v2：Create/Update/Query/Out/Page
│  ├─ repositories/           # 数据访问层：仅 CRUD 与查询
│  ├─ services/               # 业务层：状态流转、库存、应收应付
│  ├─ api/v1/
│  │  ├─ router.py            # 汇总各模块路由
│  │  └─ endpoints/           # auth sys base purchase sales inventory finance report
│  ├─ tasks/                  # Celery：导出、通知、定时
│  ├─ utils/                  # 单号生成、Excel、分页、响应包装
│  └─ tests/
├─ alembic/                   # 迁移脚本
├─ pyproject.toml             # ruff/black/pytest
├─ requirements.txt
└─ Dockerfile
```

## 分层职责
| 层 | 职责 | 禁止 |
|---|---|---|
| Router | 参数校验、鉴权、调 Service、统一响应 | 写业务逻辑 |
| Service | 业务规则、事务、状态流转、跨模块协作 | 直接拼 SQL |
| Repository | 数据访问、查询封装 | 写业务规则 |
| Model | 表结构映射 | 引用 Service |

## 关键约定
- 统一响应 `{code,message,data}`；分页 `data:{total,page,page_size,items}`
- 依赖注入：`Depends(get_db)`、`Depends(get_current_user)`、`Depends(require_perm("pur:order:add"))`
- 单据状态机集中在 Service（采购订单：草稿→待审→已审→部分入库→完成→关闭）
- 库存变动统一走 `InventoryService`，同事务写 `inv_stock` 与 `inv_stock_flow`
- 入库/出库确认时由 `FinanceService` 自动生成应收应付
- Alembic 管理迁移，禁止手改生产库

## 启动
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
