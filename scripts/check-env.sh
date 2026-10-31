#!/usr/bin/env bash
#
# 部署环境自检脚本：检查 .env / .acr.env 关键配置是否就位。
# 用法: bash scripts/check-env.sh
# 退出码: 0 = 全部就绪；1 = 存在缺失项
#
# 注意：本脚本只输出“是否已配置”，绝不打印任何密钥/密码明文；
#       请勿使用 bash -x 运行（脚本内部会临时关闭 xtrace 以免泄露）。
#
# 配置来源约定（优先级）：
#   .env     —— docker-compose 使用的配置：ACR_REGISTRY / DB_PASSWORD / SECRET_KEY ...
#   .acr.env —— ACR 登录凭证：ACR_USERNAME / ACR_PASSWORD（权限 600，不入库）
#   ACR_REGISTRY 以 .env 为准，.env 未配置时回退读取 .acr.env
#
set -euo pipefail

# 镜像仓库名与标签（与 docker-compose.yml 的 image 保持一致，可用环境变量覆盖）
BACKEND_IMAGE_REPO="${BACKEND_IMAGE_REPO:-erpa-backend}"
FRONTEND_IMAGE_REPO="${FRONTEND_IMAGE_REPO:-erpa-frontend}"
BACKEND_IMAGE_TAG="${BACKEND_IMAGE_TAG:-latest}"
FRONTEND_IMAGE_TAG="${FRONTEND_IMAGE_TAG:-latest}"

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/.." && pwd)"
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
# 返回码: 0 成功；2 文件不存在；3 文件不可读；4 解析失败
read_value() {
  local file="$1" key="$2" value="" had_x=0
  if [ ! -f "$file" ]; then
    return 2
  fi
  if [ ! -r "$file" ]; then
    return 3
  fi
  case "$-" in *x*) had_x=1 ;; esac
  set +x
  # 用 index() 定位第一个 =，保证值中含 = 时不被截断
  if ! value="$(awk -v k="$key" '
    {
      line = $0
      sub(/^[ \t]*export[ \t]+/, "", line)
      idx = index(line, "=")
      if (idx == 0) next
      name = substr(line, 1, idx - 1)
      gsub(/[ \t]/, "", name)
      if (name != k) next
      val = substr(line, idx + 1)
      # 行内注释仅对未加引号的值生效；含 # 的密钥请用引号包裹
      q = substr(val, 1, 1)
      if (q != "\"" && q != "\'") {
        sub(/[ \t]+#.*$/, "", val)
      }
      print val
      exit
    }' "$file" 2>/dev/null)"; then
    if [ "$had_x" = "1" ]; then set -x; fi
    return 4
  fi
  if [ "$had_x" = "1" ]; then set -x; fi
  value="${value%$'\r'}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  # 仅在首尾成对引号时剥离，避免误伤本身以引号结尾的合法值
  if [ "${#value}" -ge 2 ]; then
    case "$value" in
      \"*\") value="${value:1:${#value}-2}" ;;
      \'*\') value="${value:1:${#value}-2}" ;;
    esac
  fi
  printf '%s' "$value"
}

# 调用 read_value 时统一兜底，避免内部命令异常触发 set -e 直接退出
get_value() {
  read_value "$1" "$2" || true
}

# 格式校验器：避免「配了但配错」
valid_registry() {
  printf '%s' "$1" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?(/[A-Za-z0-9._-]+)?$'
}
valid_secret_key() {
  [ "${#1}" -ge 32 ]
}
valid_password() {
  [ "${#1}" -ge 8 ] && [ "$1" != "change_me" ] && ! printf '%s' "$1" | grep -q '[[:space:]]'
}
# 仍是模板占位值时不算就绪
not_placeholder() {
  [ "$1" != "change_me" ] && [ -n "$1" ]
}

# 检查配置项：file key [validator]
check_file() {
  local file="$1" key="$2" validator="${3:-}" value="" rc=0
  # 注意：read_value 用返回码区分「文件不存在/不可读/解析失败」，必须保留原始退出码
  value="$(read_value "$file" "$key")" && rc=0 || rc=$?
  case "$rc" in
    2) fail "[缺失] $file 不存在（缺少 $key）" ; return 0 ;;
    3) fail "[不可读] $file 读取失败，请检查权限：$key" ; return 0 ;;
    4) fail "[解析失败] $file 解析异常，请检查文件格式：$key" ; return 0 ;;
  esac
  if [ -z "$value" ]; then
    fail "[为空] $file 中 $key 未配置"
    return 0
  fi
  if [ "$value" = "change_me" ]; then
    fail "[占位] $file 中 $key 仍是模板占位值，请执行 bash scripts/gen-env-secrets.sh .env 生成强随机密钥"
    return 0
  fi
  if [ -n "$validator" ] && ! "$validator" "$value"; then
    fail "[格式错误] $file 中 $key 的值格式不合法"
    return 0
  fi
  echo "[就绪] $file 中 $key 已配置"
}

echo "==> 检查关键配置 ..."
# ACR_REGISTRY：.env 优先（docker-compose 直接使用），未配置时才看 .acr.env；
# 两者都没有才算缺失，避免同一项在两处重复检查造成误报
registry_value="$(get_value .env ACR_REGISTRY)"
if [ -z "$registry_value" ]; then
  registry_value="$(get_value .acr.env ACR_REGISTRY)"
  if [ -n "$registry_value" ]; then
    warn "[提示] ACR_REGISTRY 仅在 .acr.env 中配置，docker-compose 仍需 .env 中也有（CI 部署时会自动写入 .env）"
    fail "[缺失] .env 中 ACR_REGISTRY 未配置"
  else
    fail "[缺失] 未配置 ACR_REGISTRY（请写入 .env 或 .acr.env，格式 <地址>/<命名空间>）"
  fi
elif ! valid_registry "$registry_value"; then
  fail "[格式错误] ACR_REGISTRY 的值格式不合法（应为 <地址>[/<命名空间>]）"
elif printf '%s' "$registry_value" | grep -q 'your-namespace'; then
  fail "[占位] ACR_REGISTRY 仍是模板占位值 your-namespace，请填真实的 <地址>/<命名空间>"
else
  echo "[就绪] ACR_REGISTRY 已配置（格式合法）"
fi
check_file .env DB_PASSWORD valid_password
check_file .env SECRET_KEY valid_secret_key
check_file .acr.env ACR_USERNAME
check_file .acr.env ACR_PASSWORD

echo "==> 检查 Docker 镜像仓库连通性 ..."
# registry 复用上面已取到的 ACR_REGISTRY（.env 优先、.acr.env 回退），避免两处取值逻辑不一致
registry="$registry_value"
registry="${registry#http://}"
registry="${registry#https://}"
registry="${registry%/}"
# 镜像 tag 优先取 .env（docker-compose 实际使用的值），其次环境变量，最后默认 latest
env_backend_tag="$(get_value .env BACKEND_IMAGE_TAG)"
if [ -n "$env_backend_tag" ]; then
  BACKEND_IMAGE_TAG="$env_backend_tag"
fi
env_frontend_tag="$(get_value .env FRONTEND_IMAGE_TAG)"
if [ -n "$env_frontend_tag" ]; then
  FRONTEND_IMAGE_TAG="$env_frontend_tag"
fi
# 只取 host[:port] 段（如 registry.cn-beijing.aliyuncs.com:5000）；
# 注意：不支持 IPv6 字面量地址（如 [::1]:5000），这类地址请改用域名
host="${registry%%/*}"
namespace="${registry#*/}"
[ "$namespace" = "$registry" ] && namespace=""

acr_user="$(get_value .acr.env ACR_USERNAME)"
acr_pass="$(get_value .acr.env ACR_PASSWORD)"

if ! command -v docker >/dev/null 2>&1; then
  fail "[错误] 未安装 docker"
elif [ -z "$host" ]; then
  fail "[错误] 未解析出 ACR 地址（.env / .acr.env 的 ACR_REGISTRY）"
else
  # manifest inspect 依赖已登录凭据：先用 .acr.env 的凭证登录一次（不回显密码）
  if [ -n "$acr_user" ] && [ -n "$acr_pass" ]; then
    if printf '%s' "$acr_pass" | docker login "$host" --username "$acr_user" --password-stdin >/dev/null 2>&1; then
      echo "[就绪] 已使用 .acr.env 凭据登录 ${host}"
    else
      warn "[提示] 使用 .acr.env 凭据登录 ${host} 失败（用户名/密码不匹配或区域不符）"
    fi
  else
    warn "[提示] .acr.env 中没有 ACR_USERNAME / ACR_PASSWORD，跳过 docker login"
  fi

  if [ -z "$namespace" ]; then
    warn "[提示] ACR_REGISTRY 未包含命名空间，跳过镜像可读性检查"
  else
    # 镜像仓库名需与 docker-compose.yml 中的 image 保持一致，可通过环境变量覆盖；
    # backend 与 frontend 的 tag 各自独立，避免前端镜像被按后端 tag 去查
    for pair in "${BACKEND_IMAGE_REPO%%:*}:${BACKEND_IMAGE_TAG%%:*}" "${FRONTEND_IMAGE_REPO%%:*}:${FRONTEND_IMAGE_TAG%%:*}"; do
      repo="${pair%%:*}"
      tag="${pair#*:}"
      ref="${host}/${namespace}/${repo}:${tag}"
      # 只取 stderr 首行做脱敏提示（不含凭据），便于排障；加超时避免离线环境卡死
      if command -v timeout >/dev/null 2>&1; then
        err="$(timeout 10 docker manifest inspect "$ref" 2>&1 >/dev/null || true)"
      else
        err="$(docker manifest inspect "$ref" 2>&1 >/dev/null || true)"
      fi
      if [ -z "$err" ]; then
        echo "[就绪] 可读取 ${ref}"
      else
        first="$(printf '%s' "$err" | tr '\n' ' ' | cut -c1-160)"
        warn "[提示] 未读取到 ${ref}：${first}"
      fi
    done
  fi
fi

echo "==> 检查容器状态 ..."
# 兼容 docker compose (v2) 与 docker-compose (v1)：仅使用两者都支持的 ps -q
compose_cmd=""
if docker compose version >/dev/null 2>&1; then
  compose_cmd="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd="docker-compose"
fi

if ! docker info >/dev/null 2>&1; then
  fail "[错误] Docker daemon 未运行"
elif [ -z "$compose_cmd" ]; then
  fail "[错误] 未找到 docker compose 命令"
else
  # stderr 与 stdout 分开捕获，失败时给出真实原因
  if ids="$($compose_cmd ps -q 2>/tmp/check-env-compose.err)"; then
    count="$(printf '%s' "$ids" | grep -c . || true)"
    if [ "${count:-0}" -gt 0 ]; then
      echo "[就绪] 共有 ${count} 个容器处于运行状态"
    else
      warn "[提示] 没有正在运行的容器"
    fi
  else
    fail "[错误] ${compose_cmd} ps 执行失败：$(head -1 /tmp/check-env-compose.err 2>/dev/null)"
  fi
  rm -f /tmp/check-env-compose.err
fi

echo "==> 检查密钥文件权限与 git 跟踪 ..."
for secret_file in .env .acr.env; do
  [ -f "$secret_file" ] || continue
  mode="$(stat -c '%a' "$secret_file" 2>/dev/null || stat -f '%Lp' "$secret_file" 2>/dev/null || echo "")"
  if [ -n "$mode" ] && [ "$mode" != "600" ]; then
    warn "[提示] ${secret_file} 权限为 ${mode}，建议执行 chmod 600 ${secret_file}"
  fi
  if git rev-parse --git-dir >/dev/null 2>&1 && git ls-files --error-unmatch "$secret_file" >/dev/null 2>&1; then
    warn "[警告] ${secret_file} 已被 git 跟踪，可能泄露密钥，请执行 git rm --cached ${secret_file}"
  fi
done

if [ "$FAILED" -eq 0 ]; then
  echo "==> 自检通过 ✅"
else
  echo "==> 自检发现问题，请检查上述提示 ❌"
fi
exit "$FAILED"
