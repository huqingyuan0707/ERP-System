#!/usr/bin/env bash
#
# ERP 一键部署：准备 .env（含强随机密钥）并构建启动全部服务。
# 用法（在服务器项目根目录执行）：bash scripts/deploy.sh
#
set -euo pipefail

# 1) 没有 .env 时从示例创建
if [ ! -f .env ]; then
  echo "未发现 .env，从 .env.example 复制 ..."
  cp .env.example .env
fi

# 2) 生成强随机密钥（仅覆盖占位值）
bash "$(dirname "$0")/gen-env-secrets.sh" .env

# 3) 构建并后台启动
echo "构建并启动服务 ..."
docker compose up -d --build

# 4) 等待 backend 通过健康检查（依赖 postgres 就绪后自动 init_db 建表+种子）
echo "等待后端健康检查 ..."
for i in $(seq 1 30); do
  if curl -fsS http://localhost/api/v1/health >/dev/null 2>&1; then
    echo "后端就绪 ✅"
    break
  fi
  sleep 3
done

echo "部署完成。浏览器访问 http://<本机公网IP>/ ，默认账号 admin / admin123（请尽快改密码）。"
echo "如需 HTTPS：bash scripts/setup-https.sh your.domain.com"
