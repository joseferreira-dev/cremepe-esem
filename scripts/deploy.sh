#!/bin/sh
# Publica/atualiza a aplicacao a partir do GitHub. Executar NO SERVIDOR:
#
#   ./scripts/deploy.sh          # publica a main
#   ./scripts/deploy.sh <branch> # publica outra branch (uso raro)
#
# O que faz: git pull fast-forward (recusa alteracoes locais) ->
# docker compose up -d --build (migrations + collectstatic rodam sozinhos
# na subida) -> espera o /healthz responder -> remove imagens antigas.
# Nao ha deploy automatico: cada atualizacao e este comando.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BRANCH="${1:-main}"

if [ ! -f .env ]; then
    echo "[deploy] ERRO: .env ausente. Copie .env.example e configure-o (ver README)." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "[deploy] ERRO: docker nao encontrado (rodar como root ou usuario do grupo docker)." >&2
    exit 1
fi

echo "[deploy] atualizando codigo da branch ${BRANCH}..."
git fetch origin "${BRANCH}"

if [ -n "$(git status --porcelain)" ]; then
    echo "[deploy] ERRO: ha alteracoes locais nao commitadas; commitar ou descartar antes de publicar:" >&2
    git status --short >&2
    exit 1
fi

git merge --ff-only "origin/${BRANCH}"
echo "[deploy] codigo: $(git describe --tags --always) ($(git rev-parse --short HEAD))"

echo "[deploy] reconstruindo e reiniciando os containers..."
docker compose up -d --build

# O nginx so resolve o DNS do app na subida: se o container do app foi
# recriado com outro IP, reiniciar o nginx evita 502 com o IP antigo.
docker compose restart nginx

# Porta publicada pelo nginx (HTTP_PORT no .env; padrao 8080).
PORTA="$(grep -E '^HTTP_PORT=' .env | head -n 1 | cut -d= -f2- | tr -d ' \r"')"
PORTA="${PORTA:-8080}"

# 600s: o collectstatic --clear na subida pode demorar mais que 3 min.
echo "[deploy] aguardando http://localhost:${PORTA}/healthz ..."
TENTATIVA=0
while [ "${TENTATIVA}" -lt 300 ]; do
    if curl -fsS "http://localhost:${PORTA}/healthz" >/dev/null 2>&1; then
        echo "[deploy] aplicacao saudavel; removendo imagens antigas..."
        docker image prune -f >/dev/null 2>&1 || true
        echo "[deploy] concluido: $(git rev-parse --short HEAD) publicado com sucesso."
        exit 0
    fi
    TENTATIVA=$((TENTATIVA + 1))
    sleep 2
done

echo "[deploy] ERRO: sem resposta apos 600s. Diagnostico:" >&2
echo "  docker compose logs --tail=100 app" >&2
echo "  docker compose ps" >&2
exit 1
