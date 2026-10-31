#!/usr/bin/env bash
#
# 部署环境自检脚本：检查 .env / .acr.env 关键配置是否就位。
# 用法: bash scripts/check-env.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> 项目目录：$PROJECT_DIR"

check_file() {
  local file="$1" key="$2"
  if [ ! -f "$file" ]; then
    echo "[缺失] $file 不存在（缺少 $key）"
    return 1
  fi
  local value
  value="$(grep -E "^${key}=" "$file" | head -1 | cut -d= -f2- || true)"
  if [ -z "$value" ]; then
    echo "[为空] $file 中 $key 未配置"
    return 1
  fi
  echo "[就绪] $file 中 $key 已配置"
}

echo "==> 检查关键配置 ..."
check_file .env ACR_REGISTRY
check_file .env DB_PASSWORD
check_file .env SECRET_KEY
check_file .acr.env ACR_USERNAME
check_file .acr.env ACR_PASSWORD

echo "==> 检查 Docker 镜像仓库登录状态 ..."
host="$(grep -E '^ACR_REGISTRY=' .acr.env 2>/dev/null | head -1 | cut -d= -f2- | cut -d/ -f1 || true)"
if [ -n "$host" ]; then
  if docker manifest inspect "${host}/erpa-backend:latest" >/dev/null 2>&1; then
    echo "[就绪] 可拉取 ${host}/erpa-backend:latest"
  else
    echo "[警告] 无法读取 ${host}/erpa-backend:latest（仓库为空或无权限）"
  fi
else
  echo "[警告] 未解析出 ACR 地址"
fi

echo "==> 检查容器状态 ..."
docker compose ps || true
