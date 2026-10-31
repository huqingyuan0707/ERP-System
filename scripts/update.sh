#!/usr/bin/env bash
#
# 服务器端的幂等更新脚本：拉取最新代码 -> 重建启动 -> 健康检查。
# 既可由 CI 通过 SSH 调用，也可手动在服务器执行：bash scripts/update.sh
#
set -euo pipefail

# 优先用脚本所在目录的项目根，找不到时回退到常见路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-${SCRIPT_DIR}/..}"
cd "$PROJECT_DIR"

echo "==> 更新目录：$PROJECT_DIR"

# 1) 同步最新代码
#    用 fetch + reset --hard：仓库以远端为准，避免服务器上手工改动过的文件
#    导致 git pull 报 "Your local changes would be overwritten"；
#    被 .gitignore 忽略的文件（.env / .acr.env）不受影响，会原样保留。
if [ -d .git ]; then
  echo "==> git fetch + reset --hard origin/main ..."
  git fetch origin main
  git reset --hard origin/main
else
  echo "==> 非 git 仓库，跳过同步（代码需自行同步）"
fi

# 2) 重新生成占位密钥（已有真实值则保留，不会覆盖）
if [ -f scripts/gen-env-secrets.sh ]; then
  bash scripts/gen-env-secrets.sh .env || true
fi

# 3) 拉取最新镜像并启动（镜像由 CI 构建推送到 ACR，服务器只拉取运行）
#    私有仓库必须先 docker login，否则 docker compose pull 会报 denied
read_env_value() {
  local key="$1" file="$2" value=""
  if [ -f "$file" ]; then
    value="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  fi
  printf '%s' "$value"
}

ACR_REGISTRY="${ACR_REGISTRY:-$(read_env_value ACR_REGISTRY .acr.env)}"
[ -n "$ACR_REGISTRY" ] || ACR_REGISTRY="$(read_env_value ACR_REGISTRY .env)"
ACR_USERNAME="${ACR_USERNAME:-$(read_env_value ACR_USERNAME .acr.env)}"
ACR_PASSWORD="${ACR_PASSWORD:-$(read_env_value ACR_PASSWORD .acr.env)}"

if [ -n "$ACR_REGISTRY" ] && [ -n "$ACR_USERNAME" ] && [ -n "$ACR_PASSWORD" ]; then
  ACR_HOST="${ACR_REGISTRY%%/*}"
  echo "==> docker login ${ACR_HOST}"
  if ! printf '%s' "$ACR_PASSWORD" | docker login "$ACR_HOST" --username "$ACR_USERNAME" --password-stdin; then
    echo "登录 ACR 失败：${ACR_HOST}，请检查 .acr.env 中的 ACR_USERNAME / ACR_PASSWORD" >&2
    exit 1
  fi
else
  echo "未找到 ACR 凭证（.acr.env），若镜像仓库为公开仓库可忽略本提示"
fi

echo "==> docker compose pull ..."
docker compose pull
echo "==> docker compose up -d ..."
docker compose up -d

# 4) 健康检查（最多等 2 分钟）
echo "==> 等待后端就绪 ..."
for i in $(seq 1 40); do
  if curl -fsS http://localhost/api/v1/health >/dev/null 2>&1; then
    echo "部署成功 ✅ 访问 http://<服务器IP>/"
    exit 0
  fi
  sleep 3
done

echo "健康检查失败，请查看日志：docker compose logs backend" >&2
exit 1
