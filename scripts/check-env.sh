#!/usr/bin/env bash
#
# 部署环境自检脚本：检查 .env / .acr.env 关键配置是否就位。
# 用法: bash scripts/check-env.sh
# 退出码: 0 = 全部就绪；1 = 存在缺失项
#
# 注意：本脚本只输出“是否已配置”，绝不打印任何密钥/密码明文。
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

FAILED=0

fail() {
  echo "$1"
  FAILED=1
}

# 仅提示，不影响退出码
warn() {
  echo "$1"
}

echo "==> 项目目录：$PROJECT_DIR"

if [ ! -f "${PROJECT_DIR}/docker-compose.yml" ]; then
  echo "[错误] 当前目录不是项目根目录（缺少 docker-compose.yml）"
  exit 1
fi

# 读取 key 的值（去掉 export 前缀、行内注释、首尾空白与引号；不输出值本身）
read_value() {
  local file="$1" key="$2" value=""
  [ -f "$file" ] || return 0
  # 用 index() 定位第一个 =，保证值中含 = 时不被截断
  value="$(awk -v k="$key" '
    {
      line = $0
      sub(/^[ \t]*export[ \t]+/, "", line)
      idx = index(line, "=")
      if (idx == 0) next
      name = substr(line, 1, idx - 1)
      gsub(/[ \t]/, "", name)
      if (name != k) next
      val = substr(line, idx + 1)
      sub(/[ \t]+#.*$/, "", val)      # 去行内注释
      print val
      exit
    }' "$file" 2>/dev/null || true)"
  value="${value%$'\r'}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  printf '%s' "$value"
}

check_file() {
  local file="$1" key="$2"
  if [ ! -f "$file" ]; then
    fail "[缺失] $file 不存在（缺少 $key）"
    return 0
  fi
  local value
  value="$(read_value "$file" "$key")"
  if [ -z "$value" ]; then
    fail "[为空] $file 中 $key 未配置"
  else
    echo "[就绪] $file 中 $key 已配置"
  fi
}

echo "==> 检查关键配置 ..."
check_file .env ACR_REGISTRY
check_file .env DB_PASSWORD
check_file .env SECRET_KEY
check_file .acr.env ACR_USERNAME
check_file .acr.env ACR_PASSWORD

echo "==> 检查 Docker 镜像仓库连通性 ..."
registry="$(read_value .env ACR_REGISTRY)"
[ -n "$registry" ] || registry="$(read_value .acr.env ACR_REGISTRY)"
# 去掉协议前缀与结尾斜杠，只取 host 段
host="$(printf '%s' "$registry" | sed -e 's#^https\?://##' -e 's#/$##' -e 's#/.*##')"

if ! command -v docker >/dev/null 2>&1; then
  fail "[错误] 未安装 docker"
elif [ -z "$host" ]; then
  fail "[警告] 未解析出 ACR 地址"
elif docker manifest inspect "${host}/erpa-backend:latest" >/dev/null 2>&1; then
  echo "[就绪] 可拉取 ${host}/erpa-backend:latest"
else
  # 该检查依赖本机已登录 registry，未登录时会误报，故仅告警
  warn "[提示] 未读取到 ${host}/erpa-backend:latest（仓库为空、本机未 docker login 或无权限）"
fi

echo "==> 检查容器状态 ..."
if ! docker info >/dev/null 2>&1; then
  fail "[错误] Docker daemon 未运行"
elif running="$(docker compose ps --status running 2>&1)"; then
  if [ -z "$running" ]; then
    warn "[提示] 没有处于 running 状态的容器"
  else
    printf '%s\n' "$running"
  fi
else
  fail "[错误] docker compose ps 执行失败：$running"
fi

if [ "$FAILED" -eq 0 ]; then
  echo "==> 自检通过 ✅"
else
  echo "==> 自检发现问题，请检查上述提示 ❌"
fi
exit "$FAILED"
