#!/usr/bin/env bash
#
# 为 .env 生成强随机密钥（仅填充仍为占位符 change_me 或空值的项）。
# 用法：bash scripts/gen-env-secrets.sh [env文件路径，默认 .env]
#
set -euo pipefail

ENV_FILE="${1:-.env}"

# 生成 48 位字母数字随机串
gen() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 48; }

if [ ! -f "$ENV_FILE" ]; then
  echo "未找到 $ENV_FILE，请先执行：cp .env.example .env" >&2
  exit 1
fi

# 仅当该项已是占位值或空时才覆盖，已有真实值则保留
set_var() {
  local key="$1" value="$2"
  if grep -qE "^#?[[:space:]]*${key}=" "$ENV_FILE"; then
    if grep -qE "^[[:space:]]*${key}=(change_me)?[[:space:]]*$" "$ENV_FILE"; then
      sed -i -E "s|^#?[[:space:]]*${key}=.*|${key}=${value}|" "$ENV_FILE"
      echo "已更新 $key"
    else
      echo "保留现有 $key（非占位值）"
    fi
  else
    echo "${key}=${value}" >> "$ENV_FILE"
    echo "已追加 $key"
  fi
}

set_var DB_PASSWORD   "$(gen)"
set_var SECRET_KEY     "$(gen)"
set_var MINIO_PASSWORD "$(gen)"

echo "完成。请重新部署使新密钥生效：docker compose up -d --build"
