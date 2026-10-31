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

# 1) 拉取最新代码（仅 fast-forward；本地有改动请先提交或 stash）
if [ -d .git ]; then
  echo "==> git pull ..."
  git pull --ff-only
else
  echo "==> 非 git 仓库，跳过 pull（代码需自行同步）"
fi

# 2) 重新生成占位密钥（已有真实值则保留，不会覆盖）
if [ -f scripts/gen-env-secrets.sh ]; then
  bash scripts/gen-env-secrets.sh .env || true
fi

# 3) 拉取最新镜像并启动（镜像由 CI 构建推送到 ACR，服务器只拉取运行）
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
