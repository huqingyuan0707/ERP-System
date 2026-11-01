#!/usr/bin/env bash
# 在 Docker 容器内运行 pytype（pytype 不支持 Windows，且仅支持 Python 3.8~3.12）。
#
# 用途：本地补跑 pytype 类型检查。CI 直接在 Linux runner 上装 pytype 运行，
#       不走这个脚本；此脚本供 Windows 开发机用 Docker 复现同样的检查。
#
# 用法：
#   bash scripts/ci/run_pytype.sh                    # 全量检查 backend/app
#   bash scripts/ci/run_pytype.sh backend/app/services # 只检查指定目录
#
# 依赖：已安装 Docker；镜像 python:3.12-slim 可拉取（国内可换镜像源）。

set -euo pipefail

# 仓库根目录（本脚本位于 scripts/ci/ 下，往上两级）
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# 待检查路径：默认整个后端应用包
TARGET="${1:-backend/app}"

# 国内网络拉镜像慢时，可改成 docker.1ms.run/library/python:3.12-slim
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"

echo "[pytype] 使用镜像: ${PYTHON_IMAGE}"
echo "[pytype] 检查目标: ${TARGET}"

# --rm 退出即删容器；-v 挂载仓库；-w 指定工作目录为 backend（与 pytype.cfg 的相对路径一致）
docker run --rm \
  -v "${REPO_ROOT}:/workspace" \
  -w /workspace/backend \
  "${PYTHON_IMAGE}" \
  bash -c '
    set -e
    python -m pip install --quiet --upgrade pip
    # pytype 依赖 libclang，必须先装编译依赖，否则 import pytype 会失败
    apt-get update -qq && apt-get install -y -qq clang libclang-dev >/dev/null
    python -m pip install --quiet -r requirements.txt
    python -m pip install --quiet "pytype==2024.10.11"
    echo "[pytype] 开始检查..."
    # --config 显式指定配置文件：pytype 不会自动发现 backend/pytype.cfg
    python -m pytype --config=pytype.cfg "${TARGET}"
  '
