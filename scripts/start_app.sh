#!/bin/sh
# Inicia o servidor da aplicação.
set -e

if [ "${DJANGO_DEBUG:-false}" = "true" ]; then
    # Desenvolvimento: recarrega automaticamente ao salvar arquivos.
    exec gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers 1 \
        --reload \
        --timeout 180 \
        --access-logfile - \
        --error-logfile -
fi

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --timeout 180 \
    --access-logfile - \
    --error-logfile -
