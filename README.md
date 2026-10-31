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

仓库提供 `docker-compose.yml`（PostgreSQL / Redis / MinIO / 后端 / 前端 / Nginx）与一键部署脚本（`scripts/`）。

> 说明：项目后端当前使用本地 `exports/` 目录存储导出文件，未实际接入 MinIO，compose 中的 minio 服务仅作后续扩展保留。

### 一键部署（推荐）

在服务器项目根目录执行：

```bash
bash scripts/deploy.sh
```

`deploy.sh` 会自动：缺 `.env` 时从 `.env.example` 复制 → 调用 `gen-env-secrets.sh` 生成强随机密钥（仅覆盖 `change_me` 占位值）→ `docker compose up -d --build` → 轮询后端健康检查（postgres 就绪后后端自动建表 + 写入种子数据）。

### 手动部署

```bash
cp .env.example .env        # 再手动改 DB_PASSWORD / SECRET_KEY / MINIO_PASSWORD 为强值
docker compose up -d --build
```

启动后访问 `http://<服务器IP>/`，默认管理员 `admin / admin123`（请立即修改）。

### 部署辅助脚本

| 脚本 | 作用 |
|------|------|
| `scripts/deploy.sh` | 一键：准备 `.env` + 强密钥 + 构建启动 + 健康检查 |
| `scripts/gen-env-secrets.sh [env路径]` | 为 `.env` 生成 48 位强随机 `DB_PASSWORD`/`SECRET_KEY`/`MINIO_PASSWORD` |
| `scripts/setup-https.sh <域名>` | certbot 申请 Let's Encrypt 证书并启用 HTTPS（需域名 A 记录指向本机、80 端口放通） |
| `scripts/backup.sh [保留份数]` | 停止服务前自动备份 PostgreSQL 与 Redis 数据，可保留最近 N 份（默认 7） |
| `scripts/check-acr.sh <registry> <用户名> <密码>` | ACR 自检：登录 → 校验命名空间 → 检查 `erpa-backend`/`erpa-frontend` 仓库是否存在 |
| `scripts/check-env.sh` | 部署环境自检：`.env`/`.acr.env` 关键配置、ACR 登录与镜像可读性、容器状态、密钥文件权限 |

### 启用 HTTPS

```bash
bash scripts/setup-https.sh your.domain.com
```

脚本会申请证书、写入 HTTPS 版 `nginx/nginx.conf`（80→443 跳转）并热重载；证书 90 天有效，续期后需重新复制到 `nginx/certs` 并 `nginx -s reload`（脚本末尾给出 `--deploy-hook` 写法）。

### 数据持久化与备份

- 数据均在命名卷：`pgdata`（PostgreSQL）、`redisdata`（Redis）、`miniodata`（MinIO），不随容器删除丢失。
- 一键备份：`bash scripts/backup.sh`（默认保留最近 7 份，备份写入 `backups/YYYYMMDD-HHMMSS/`）。
- 恢复示例：解压对应备份目录后 `docker compose exec -T postgres psql -U erp erp < pg.sql`。

---

## 云端自动部署（GitHub Actions + 阿里云 ACR）

`.github/workflows/deploy.yml` 在 `main` 分支推送后自动执行：构建 backend / frontend 镜像 → 推送到阿里云 ACR → SSH 到服务器运行 `scripts/update.sh` 拉取镜像并重启。

### 一、前置条件（阿里云控制台，一次性）

1. 开通**容器镜像服务 ACR**，记下**区域**（如 `cn-hangzhou`）与登录域名（`registry.cn-hangzhou.aliyuncs.com`）。
2. 新建**命名空间**（如 `erp-prod`）。命名空间与区域一一对应，不能跨区域推送。
3. 在命名空间下**手动创建两个镜像仓库**：`erpa-backend`、`erpa-frontend`。
   ACR 不会在 `docker push` 时自动建仓库，仓库不存在时推送直接返回
   `denied: requested access to the resource is denied`。
4. 在 ACR 实例的**访问凭证**里设置固定密码，记下用户名（通常是账号 ID）与密码；
   企业版实例还需在**授权管理**中给对应 RAM 账号授予该命名空间的 push/pull 权限。

### 二、GitHub 仓库 Secrets（Settings → Secrets and variables → Actions）

| Secret | 示例 | 说明 |
|--------|------|------|
| `ACR_REGISTRY` | `crpi-xxxx.cn-beijing.personal.cr.aliyuncs.com/erpa` | 镜像前缀（含命名空间）；个人版 ACR 形如 `crpi-xxx.cn-<区域>.personal.cr.aliyuncs.com/<命名空间>`；也可改用 `ACR_HOST` + `ACR_NAMESPACE` 拆分填写 |
| `ACR_USERNAME` | `1234567890123456` | ACR 实例「访问凭证」的用户名，不是 RAM 用户名 |
| `ACR_PASSWORD` | `********` | ACR 固定密码 / 临时令牌 |
| `DEPLOY_HOST` | `1.2.3.4` | 服务器 IP 或域名 |
| `DEPLOY_USER` | `root` | SSH 登录用户 |
| `DEPLOY_SSH_KEY` | `-----BEGIN OPENSSH PRIVATE KEY----- …` | **SSH 私钥全文**（不是 `.pub` 公钥），以「粘贴为纯文本」方式填入；流水线会自动清理 CRLF 换行，并兼容 base64 编码的私钥 |
| `DEPLOY_PASSWORD` | — | 可选，服务器登录密码；配置后改用密码登录，不再需要 `DEPLOY_SSH_KEY` |
| `DEPLOY_PORT` | `22` | 可选，SSH 端口，默认 22 |
| `DEPLOY_REPO_TOKEN` | `github_pat_xxx` | 可选，仓库访问令牌；**仅当服务器上还没有代码目录时**，用于自动 clone 到 `/root/erp`。推荐使用 **fine-grained token**（权限只需 `Contents: Read`），而非 classic `ghp_` 全权限令牌，降低泄露影响面；流水线不会把该 token 写进 clone 的 URL 或命令行参数（改用临时凭据文件，克隆后立即删除） |
| `DEPLOY_REPO` | `huqingyuan0707/ERP-System` | 可选，目标仓库 `owner/repo`；fork 或迁移仓库时改这里，无需改工作流 |

私钥的正确获取方式（在能登录服务器的机器上执行）：

```bash
# 1) 没有专用部署密钥时先生成一把（ED25519，无口令便于 CI 使用）
ssh-keygen -t ed25519 -f ~/.ssh/erp_deploy -N '' -C "erp-ci"

# 2) 把公钥装到服务器的 authorized_keys
cat ~/.ssh/erp_deploy.pub | ssh root@<服务器IP> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# 3) 复制私钥全文填入 Secret（务必是私钥，不是 .pub）
cat ~/.ssh/erp_deploy

# 4) 若 Secret 不便保存多行文本，可改填 base64，流水线会自动解码
base64 -w0 ~/.ssh/erp_deploy
```

> `DEPLOY_SSH_KEY` 填错时，流水线会在「校验部署密钥」步骤直接给出提示（含检测到的内容开头）；
> 若提示 `ssh: no key found`，说明该 Secret 为空或内容不是私钥（常见于误填 `.pub` 公钥）。

流水线会先解析并校验上述配置（缺失时直接给出明确报错），再登录 ACR、构建、推送；推送失败时按命名空间 → 区域 → 仓库是否存在 → 推送权限的顺序给出排查提示。

### 三、服务器端准备

- 项目根目录放好 `.env`（`cp .env.example .env`），并执行 `bash scripts/gen-env-secrets.sh .env` 生成强随机密钥。
- ACR 登录凭证由 CI 以 base64 传输并写入项目根目录的 **`.acr.env`**（权限 600，不入库）；
  `scripts/update.sh` 会在 `docker compose pull` 前自动 `docker login`。手动部署时按同样格式自行创建 `.acr.env`。
- 首次部署前在服务器上手动执行一次 `bash scripts/deploy.sh`，完成镜像构建与数据卷初始化。

### 四、常见报错对照

本地可先跑一次自检脚本，避免反复消耗 CI 时间：

```bash
# 1) 全量环境自检（配置 / ACR 登录 / 镜像可读性 / 容器状态 / 文件权限）
bash scripts/check-env.sh

# 2) 只查 ACR（需手动给出凭证）
bash scripts/check-acr.sh crpi-xxxx.cn-beijing.personal.cr.aliyuncs.com/erpa 1234567890 'your-password'
```

| 报错 | 原因 | 处理 |
|------|------|------|
| `denied: requested access to the resource is denied`（push） | 命名空间或镜像仓库不存在、区域不匹配、凭证无 push 权限 | 按「前置条件」1~4 逐项核对，重点确认 `erpa-backend`/`erpa-frontend` 已创建 |
| `unauthorized: incorrect username or password` | 用户名/密码填错 | 使用 ACR「访问凭证」的用户名与固定密码 |
| `denied`（pull 阶段） | 服务器未登录 ACR | 确认 `.acr.env` 存在且 `scripts/update.sh` 已执行登录 |
| `manifest unknown` | 仓库里还没有该 tag | 先成功 push，再拉取；或手动触发一次流水线 |
| `net/http: TLS handshake timeout`（login/push 阶段） | runner 到 ACR 的网络不通或抖动，daemon 的 TLS 握手超时 | 流水线已内置重试；仍失败则换更近 ACR 区域的 runner（自托管/国内节点）或为 runner 配置 `HTTPS_PROXY`；同时确认 ACR 域名与凭证区域一致 |

### 五、手动触发与自定义 tag

在 Actions 页面选择 **Deploy ERP → Run workflow**，可填入 `tag`（默认 `latest`），用于灰度验证。

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
