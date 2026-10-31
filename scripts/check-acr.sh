#!/usr/bin/env bash
#
# ACR 自检：验证 ACR 地址、命名空间、镜像仓库是否存在，以及登录凭证是否可用。
# 用于在本地先排查「denied: requested access to the resource is denied」，避免反复跑 CI。
#
# 用法：
#   bash scripts/check-acr.sh <registry含命名空间> <username> <password>
#   例：bash scripts/check-acr.sh registry.cn-hangzhou.aliyuncs.com/erp-prod 1234567890 'your-password'
#
set -uo pipefail

REGISTRY="${1:-}"
USERNAME="${2:-}"
PASSWORD="${3:-}"

if [ -z "$REGISTRY" ] || [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
  echo "用法：bash scripts/check-acr.sh <registry含命名空间> <username> <password>" >&2
  exit 2
fi

HOST="${REGISTRY%%/*}"
NAMESPACE="${REGISTRY#*/}"

if [ "$NAMESPACE" = "$HOST" ] || [ -z "$NAMESPACE" ]; then
  echo "ACR 地址需形如 registry.<区域>.aliyuncs.com/<命名空间>，当前：${REGISTRY}" >&2
  exit 2
fi

echo "==> 1/3 登录 ${HOST}"
if ! printf '%s' "$PASSWORD" | docker login "$HOST" --username "$USERNAME" --password-stdin; then
  echo "登录失败：用户名/密码不正确，或该凭证不属于 ${HOST} 这个区域" >&2
  exit 1
fi

echo "==> 2/3 命名空间 ${NAMESPACE}"
echo "    （控制台确认该命名空间存在，且与 ${HOST} 属于同一区域）"

echo "==> 3/3 镜像仓库检查"
MISSING=0
for repo in erp-backend erp-frontend; do
  if docker manifest inspect "${REGISTRY}/${repo}:latest" >/dev/null 2>&1; then
    echo "    [存在] ${repo}"
  else
    echo "    [缺失] ${repo} —— ACR 不会在 docker push 时自动创建仓库，请在控制台手动创建" >&2
    MISSING=1
  fi
done

echo
if [ "$MISSING" -eq 1 ]; then
  echo "结论：镜像仓库缺失是 push 返回 denied 的最常见原因，先在控制台建好仓库再重跑流水线。" >&2
  exit 1
fi

echo "结论：地址、登录与仓库读取均正常；若流水线仍在 push 阶段报 denied，"
echo "      说明当前账号缺少该命名空间的 push 权限（企业版需在 ACR「授权管理」中授权）。"
