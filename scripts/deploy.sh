#!/usr/bin/env bash
#
# ERP 一键部署：准备 .env（含强随机密钥）并构建启动全部服务。
# 用法（在服务器项目根目录执行）：bash scripts/deploy.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# 1) 没有 .env 时从示例创建
if [ ! -f .env ]; then
  echo "未发现 .env，从 .env.example 复制 ..."
  cp .env.example .env
fi

# 明确标记生产环境；应用启动时会再次执行同等安全校验。
if grep -qE '^[[:space:]]*ENVIRONMENT=' .env; then
  sed -i -E 's|^[[:space:]]*ENVIRONMENT=.*|ENVIRONMENT=production|' .env
else
  printf '\nENVIRONMENT=production\n' >> .env
fi

# 2) 生成强随机密钥（仅覆盖占位值）；失败立即终止部署。
bash "${SCRIPT_DIR}/gen-env-secrets.sh" .env

# 3) 启动前强制通过环境自检；任何生产关键配置错误都会阻断部署。
SKIP_RUNTIME_CHECKS=1 bash "${SCRIPT_DIR}/check-env.sh"

# 4) 构建并后台启动
echo "构建并启动服务 ..."
docker compose up -d --build

# 5) 等待 backend 通过健康检查（依赖 postgres 就绪后自动 init_db 建表+种子）
echo "等待后端健康检查 ..."
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://localhost/api/v1/health >/dev/null 2>&1; then
    healthy=1
    echo "后端就绪"
    break
  fi
  sleep 3
done

if [ "$healthy" -ne 1 ]; then
  echo "后端健康检查失败，请查看日志：docker compose logs backend" >&2
  exit 1
fi

echo "部署完成。浏览器访问 http://<本机公网IP>/ 。"
echo "首次管理员口令必须通过安全配置或登录后立即修改，禁止保留默认口令。"
echo "如需 HTTPS：bash scripts/setup-https.sh your.domain.com"
