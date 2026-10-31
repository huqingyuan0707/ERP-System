# 企业 ERP 系统

面向中小企业的进销存一体化 ERP 系统，前后端分离，覆盖系统管理、基础资料、采购、销售、库存、财务与报表等模块。

当前已完成：**系统管理**（用户 / 角色 / 权限 / 部门 / 字典 / 参数 / 操作日志 / 登录日志）、**基础资料**（商品 / 分类 / 单位）、**商品导入导出**、**登录鉴权与按钮级权限**。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Vue Router + Pinia + Element Plus + Axios + ECharts |
| 后端 | Python 3.11+ + FastAPI + Pydantic v2 + SQLAlchemy 2.0（异步） + Alembic |
| 数据库 | PostgreSQL 16（开发期也可用 SQLite） |
| 缓存 | Redis 7（未配置时自动退化为进程内缓存） |
| 部署 | Docker + Docker Compose + Nginx |

---

## 目录结构

```text
erp系统/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/          # 路由层：router.py + endpoints/
│   │   ├── core/            # 配置、安全、依赖注入、异常
│   │   ├── db/              # 引擎、会话、建表与种子数据
│   │   ├── models/          # SQLAlchemy 模型
│   │   ├── schemas/         # Pydantic 模型（Create/Update/Out/Query）
│   │   ├── repositories/    # 数据访问层
│   │   ├── services/        # 业务逻辑层
│   │   └── utils/           # 分页、单号、Excel 等工具
│   ├── alembic/             # 数据库迁移脚本
│   ├── tests/               # pytest 测试
│   └── requirements.txt
├── frontend/                # Vue 3 前端
│   └── src/
│       ├── api/             # 接口封装
│       ├── types/           # TypeScript 类型
│       ├── stores/          # Pinia 状态
│       ├── views/           # 页面
│       ├── components/      # 公共组件（BaseCard、ExportButton 等）
│       ├── layouts/         # 布局
│       ├── directives/      # v-perm 按钮权限指令
│       └── styles/          # 全局样式
├── docker-compose.yml
└── *.md                     # 需求、表设计、接口清单等项目文档
```

后端分层依赖单向：`api → services → repositories → models`，业务逻辑只写在 Service 层。

---

## 快速开始

### 1. 后端

```powershell
cd backend

# 创建并激活虚拟环境（Windows PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 安装依赖
pip install -r requirements.txt

# 配置环境变量：复制模板后修改
Copy-Item .env.example .env
```

`.env` 关键项：

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 默认 PostgreSQL；本地无 PG 可改为 `sqlite+aiosqlite:///./erp.db` |
| `REDIS_URL` | 留空则使用进程内缓存 |
| `SECRET_KEY` | JWT 签名密钥，**上线前必须替换** |
| `BACKEND_CORS_ORIGINS` | 允许的前端来源（JSON 数组） |

启动：

```powershell
uvicorn app.main:app --reload --port 8000
```

首次启动会自动建表并写入种子数据（幂等）：

- 默认管理员：`admin / admin123`（登录后请立即修改）
- 内置角色：`admin`（超级管理员）、`viewer`（只读）
- 基础菜单与按钮权限、示例商品分类与单位

接口文档：<http://localhost:8000/docs>

### 2. 前端

```powershell
cd frontend

pnpm install
pnpm dev
```

访问 <http://localhost:5173>。开发环境 Vite 已把 `/api` 代理到 `http://localhost:8000`，后端 CORS 也放行了所有 localhost 端口。

---

## 数据库迁移

开发期首次启动会自动 `create_all` 建表；结构变更与生产环境请使用 Alembic：

```powershell
cd backend
alembic upgrade head                                  # 应用迁移
alembic revision --autogenerate -m "描述本次变更"      # 生成新迁移
```

---

## 测试与代码检查

```powershell
# 后端
cd backend
pytest                                                # 单元/接口测试（tests/）
ruff check app tests                                  # 代码检查（行宽 100）

# 前端
cd frontend
npm run type-check                                    # vue-tsc 类型检查
npm run lint                                          # ESLint
```

测试使用独立的 SQLite，`TESTING=true` 时会禁用连接池，避免跨事件循环复用连接。

---

## Docker 部署

仓库提供了 `docker-compose.yml`（PostgreSQL / Redis / MinIO / 后端 / Celery / 前端 / Nginx）。

```powershell
# 根目录准备 .env，至少包含：
# DB_PASSWORD=...
# MINIO_USER=...
# MINIO_PASSWORD=...

docker compose up -d --build
```

> 注意：Compose 中的 Nginx 服务会挂载 `./nginx/nginx.conf` 与 `./nginx/certs`，使用前需先补齐这两个路径；`app/tasks`（Celery 任务）也仍在完善中。

---

## 接口约定

- 统一前缀 `/api/v1`，成功响应 `{ "code": 0, "message": "success", "data": ... }`
- 分页参数 `page` / `page_size`，分页结果 `{ total, page, pageSize, items }`
- JSON 字段对外统一 camelCase，Python 与数据库统一 snake_case，由 Pydantic `alias_generator` 转换
- 认证：`Authorization: Bearer <access_token>`
- 权限：路由依赖 `require_perm("模块:资源:动作")`，前端按钮使用 `v-perm` 指令

---

## 项目文档

| 文档 | 内容 |
|---|---|
| [企业ERP系统简易需求说明书.md](./企业ERP系统简易需求说明书.md) | 功能范围与业务规则 |
| [需求条目拆分.md](./需求条目拆分.md) | 可验收的需求条目 |
| [数据库表设计.md](./数据库表设计.md) | 表结构与字段说明 |
| [API接口清单.md](./API接口清单.md) | 接口列表 |
| [Vue3页面清单.md](./Vue3页面清单.md) | 前端页面规划 |
| [开发任务拆解.md](./开发任务拆解.md) | 任务拆分与进度 |
| [FastAPI项目目录结构.md](./FastAPI项目目录结构.md) | 后端目录约定 |
| [DockerCompose配置.md](./DockerCompose配置.md) | 部署说明 |
| [14天学习计划.md](./14天学习计划.md) | 新人上手路径 |

代码风格约定见 [.trae/rules/code-style.md](./.trae/rules/code-style.md)。
