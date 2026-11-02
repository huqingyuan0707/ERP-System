#!/usr/bin/env bash
#
# 为 .env 生成强随机密钥与首次管理员密码（仅填充占位符 change_me 或空值）。
# 用法：bash scripts/gen-env-secrets.sh [env文件路径，默认 .env]
#
set -euo pipefail

ENV_FILE="${1:-.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "未找到 $ENV_FILE，请先执行：cp .env.example .env" >&2
  exit 1
fi
if [ ! -w "$ENV_FILE" ]; then
  echo "$ENV_FILE 不可写，请检查文件权限" >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "未找到 openssl，无法安全生成部署密钥" >&2
  exit 1
fi

# 生成 256 bit 十六进制随机值；字符可安全用于 URL 和 .env。
gen() {
  local value
  value="$(openssl rand -hex 32)"
  if [ "${#value}" -ne 64 ] || ! printf '%s' "$value" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "openssl 未能生成有效的强随机值" >&2
    return 1
  fi
  printf '%s' "$value"
}

# 仅当该项已是占位值或空时才覆盖，已有真实值则保留。
set_var() {
  local key="$1" value="$2"
  if grep -qE "^#?[[:space:]]*${key}=" "$ENV_FILE"; then
    if grep -qE "^[[:space:]]*${key}=[[:space:]]*(change_me)?[[:space:]]*$" "$ENV_FILE"; then
      sed -i -E "s|^#?[[:space:]]*${key}=.*|${key}=${value}|" "$ENV_FILE"
      echo "已更新 $key"
    else
      echo "保留现有 $key（非占位值）"
    fi
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    echo "已追加 $key"
  fi
}

# 分开赋值，任何一次 openssl 失败都会在写文件前终止，不能被函数参数展开吞掉。
DB_PASSWORD="$(gen)"
SECRET_KEY="$(gen)"
MINIO_PASSWORD="$(gen)"
# 固定前缀只保证复杂度规则；其后仍保留完整 256 bit 随机熵。
INITIAL_ADMIN_PASSWORD="Aa1$(gen)"

set_var DB_PASSWORD "$DB_PASSWORD"
set_var SECRET_KEY "$SECRET_KEY"
set_var MINIO_PASSWORD "$MINIO_PASSWORD"
set_var INITIAL_ADMIN_PASSWORD "$INITIAL_ADMIN_PASSWORD"

# 把最终 DB_PASSWORD 同步进 DATABASE_URL；否则 postgres 容器密码与后端连接串不一致。
DBPW="$(awk -F= '
  /^[[:space:]]*DB_PASSWORD=/ {
    sub(/^[^=]*=/, "")
    sub(/\r$/, "")
    print
    exit
  }
' "$ENV_FILE")"
if [ -n "$DBPW" ] && grep -qE '^DATABASE_URL=' "$ENV_FILE"; then
  escaped_dbpw="$(printf '%s' "$DBPW" | sed 's/[\\&|]/\\&/g')"
  sed -i -E "s|(postgresql\+asyncpg://[^:/]+:)[^@]+(@)|\1${escaped_dbpw}\2|" "$ENV_FILE"
  echo "已同步 DATABASE_URL 中的数据库密码"
fi

# 部署配置默认仅允许当前用户读取；不支持权限位的平台会由 check-env 给出提示。
chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "完成。请执行 bash scripts/check-env.sh 验证后再部署。"
