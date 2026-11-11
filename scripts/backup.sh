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

# Cópia offsite opcional (BACKUP_RSYNC_TARGET): leva dumps e mídia para
# fora desta máquina — outro servidor via SSH ou outro disco local.
#   BACKUP_RSYNC_TARGET=user@servidor:/var/backups/cremepe
#   BACKUP_RSYNC_TARGET=/mnt/disco-externo/cremepe
# Uma falha aqui encerra o script com erro (o backup local já está feito),
# para o cron sinalizar o problema.
if [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then
    if command -v rsync >/dev/null 2>&1; then
        echo "[backup] copia offsite -> ${BACKUP_RSYNC_TARGET}"
        rsync -a "$BACKUP_DIR/db-$STAMP.dump" "${BACKUP_RSYNC_TARGET%/}/dumps/"
        rsync -a --delete "$BACKUP_DIR/media/" "${BACKUP_RSYNC_TARGET%/}/media/"
    else
        echo "[backup] AVISO: rsync ausente; copia offsite pulada." >&2
    fi
fi

echo "[backup] removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" -maxdepth 1 -name 'db-*.dump' -mtime "+$RETENTION_DAYS" -delete

ls -lh "$BACKUP_DIR" | tail -n 5
echo "[backup] concluido em $STAMP"
