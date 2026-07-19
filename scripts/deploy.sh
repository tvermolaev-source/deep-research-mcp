#!/usr/bin/env bash
# Деплой на свой сервер.
# Использование:
#   DEPLOY_HOST=user@server ./scripts/deploy.sh v0.1.0
#
# Требует:
#   • SSH-доступ к серверу
#   • Docker + docker compose plugin на сервере
#   • SearXNG-контейнер уже запущен на сервере (или будет поднят этим скриптом)

set -euo pipefail

VERSION="${1:-latest}"
IMAGE="${IMAGE:-ghcr.io/ermolaevtv/deep-research-mcp}"
HOST="${DEPLOY_HOST:-}"
REMOTE_DIR="${REMOTE_DIR:-deep-research}"

if [[ -z "$HOST" ]]; then
  echo "❌ Set DEPLOY_HOST=user@server" >&2
  exit 1
fi

echo "🚀 Deploying $IMAGE:$VERSION to $HOST:$REMOTE_DIR"

ssh "$HOST" "mkdir -p $REMOTE_DIR"

# Копируем актуальный compose
scp docker-compose.yml "$HOST:$REMOTE_DIR/docker-compose.yml"
scp .env.example "$HOST:$REMOTE_DIR/.env.example"

ssh "$HOST" "cd $REMOTE_DIR && \
  if [[ ! -f .env ]]; then cp .env.example .env; fi && \
  docker compose pull deep-research-mcp 2>/dev/null || true; \
  IMAGE=$IMAGE VERSION=$VERSION docker compose up -d deep-research-mcp"

echo "✅ Done. Check: ssh $HOST 'docker logs -f \${REMOTE_DIR}-deep-research-mcp-1'"
