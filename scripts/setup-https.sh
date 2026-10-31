#!/usr/bin/env bash
#
# 为已部署的 ERP 站点申请 Let's Encrypt 免费证书并启用 HTTPS。
# 前置条件：
#   1) docker compose 已启动且 nginx 监听 80 端口（docker-compose.yml 已放通 80/443）
#   2) 域名 DNS A 记录已指向本机公网 IP
# 用法：bash scripts/setup-https.sh your.domain.com
#
set -euo pipefail

DOMAIN="${1:-}"
[ -n "$DOMAIN" ] || { echo "用法: bash scripts/setup-https.sh your.domain.com" >&2; exit 1; }

# 1) 安装 certbot
if ! command -v certbot >/dev/null 2>&1; then
  echo "安装 certbot ..."
  apt-get update -y && apt-get install -y certbot
fi

# 2) webroot 方式申请证书（依赖 nginx 根路径可写）
WEBROOT=/usr/share/nginx/html
docker compose exec -T nginx mkdir -p "$WEBROOT/.well-known/acme-challenge"
certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
  --non-interactive --agree-tos -m "admin@$DOMAIN" || {
  echo "证书申请失败：请确认域名 DNS 已指向本机且 80 端口放通。" >&2
  exit 1
}

# 3) 把证书复制到 nginx 挂载目录（compose 已挂载 ./nginx/certs:/etc/nginx/certs:ro）
mkdir -p nginx/certs
cp -L "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" nginx/certs/fullchain.pem
cp -L "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   nginx/certs/privkey.pem

# 4) 写入 HTTPS 版 nginx 配置
cat > nginx/nginx.conf <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /usr/share/nginx/html; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 20m;

    location / {
        proxy_pass http://frontend:80;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# 5) 热重载 nginx（配置在 bind mount 中，reload 即生效）
docker compose exec nginx nginx -s reload

echo "HTTPS 已启用：https://$DOMAIN"
echo "注意：Let's Encrypt 证书 90 天有效。续期后需重新复制证书到 nginx/certs 并 reload，"
echo "可在 certbot 的 --deploy-hook 中加入：cp -L /etc/letsencrypt/live/$DOMAIN/*.pem nginx/certs/ && docker compose exec nginx nginx -s reload"
