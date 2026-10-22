#!/bin/sh
set -e

echo "[entrypoint] aguardando o banco de dados..."
python - <<'PY'
import os
import sys
import time

import psycopg

dsn = (
    f"host={os.environ.get('POSTGRES_HOST', 'db')} "
    f"port={os.environ.get('POSTGRES_PORT', '5432')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'cremepe')} "
    f"user={os.environ.get('POSTGRES_USER', 'cremepe')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', '')}"
)

for attempt in range(60):
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
        print("[entrypoint] banco disponível")
        break
    except Exception as exc:  # noqa: BLE001
        last = exc
        time.sleep(2)
else:
    sys.exit(f"[entrypoint] banco indisponivel: {last}")
PY

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[entrypoint] aplicando migrations..."
    python manage.py migrate --noinput
    echo "[entrypoint] coletando arquivos estaticos..."
    python manage.py collectstatic --noinput --clear
else
    echo "[entrypoint] aguardando as migrations..."
    until python manage.py migrate --noinput --check >/dev/null 2>&1; do
        sleep 2
    done
    echo "[entrypoint] migrations aplicadas"
fi

exec "$@"
