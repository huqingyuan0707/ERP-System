#!/usr/bin/env bash
#
# 在线备份 ERP 数据（PostgreSQL + Redis），可保留最近 N 份，可选备份后停机。
# 用法：
#   bash scripts/backup.sh            # 仅备份，保留最近 7 份
#   bash scripts/backup.sh 14         # 保留最近 14 份
#   bash scripts/backup.sh 7 --down   # 备份后执行 docker compose down
#
set -euo pipefail

KEEP="${1:-7}"
DOWN_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --down) DOWN_FLAG=1 ;;
    [0-9]*) KEEP="$arg" ;;
  esac
done

BACKUP_ROOT="backups"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_ROOT/$TS"
mkdir -p "$DEST"

echo "==> 备份 PostgreSQL ..."
docker compose exec -T postgres pg_dump -U erp erp > "$DEST/pg.sql"
echo "    $DEST/pg.sql ($(du -h "$DEST/pg.sql" | cut -f1))"

echo "==> 备份 Redis ..."
docker compose exec -T redis redis-cli --rdb - > "$DEST/redis_dump.rdb"
echo "    $DEST/redis_dump.rdb ($(du -h "$DEST/redis_dump.rdb" | cut -f1))"

# 可选：备份 MinIO 数据卷（当前后端未接入，默认跳过）
# docker compose run --rm -v miniodata:/data -v "$PWD/$DEST":/backup alpine \
#   sh -c 'apk add --no-cache tar && tar czf /backup/minio.tar.gz -C /data .'

echo "==> 备份完成：$DEST"

# 清理旧备份，仅保留最近 KEEP 份
if [ "$KEEP" -gt 0 ]; then
  echo "==> 保留最近 $KEEP 份，清理更早的备份 ..."
  ls -1 "$BACKUP_ROOT" | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
    rm -rf "$BACKUP_ROOT/$old"
    echo "    已删除 $BACKUP_ROOT/$old"
  done
fi

if [ -n "$DOWN_FLAG" ]; then
  echo "==> 执行 docker compose down ..."
  docker compose down
  echo "    服务已停止（数据卷 pgdata/redisdata 仍在，可随时 docker compose up -d 恢复）"
fi
