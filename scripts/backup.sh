#!/bin/sh
# Backup do banco + da midia. Executar no host (o compose usa bind mounts).
# Uso: ./scripts/backup.sh   |   cron: 30 2 * * * /caminho/cremepe-esem/scripts/backup.sh
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
[ -f .env ] && . ./.env

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

echo "[backup] dumping do banco..."
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-cremepe}" -d "${POSTGRES_DB:-cremepe}" -Fc > "$BACKUP_DIR/db-$STAMP.dump"

echo "[backup] copiando midia (HLS + originais)..."
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete media/ "$BACKUP_DIR/media/"
else
    rm -rf "$BACKUP_DIR/media"
    cp -a media "$BACKUP_DIR/media"
fi

echo "[backup] removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" -maxdepth 1 -name 'db-*.dump' -mtime "+$RETENTION_DAYS" -delete

ls -lh "$BACKUP_DIR" | tail -n 5
echo "[backup] concluido em $STAMP"
