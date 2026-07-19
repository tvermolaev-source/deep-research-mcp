#!/usr/bin/env bash
# Деплой deep-research-mcp на сервер.
# SearXNG/Open WebUI предполагаются уже поднятыми — здесь только MCP.
#
# Использование:
#   DEPLOY_HOST=user@server ./scripts/deploy.sh v0.1.2

set -euo pipefail

VERSION="${1:-latest}"
IMAGE="${IMAGE:-ghcr.io/tvermolaev-source/deep-research-mcp}"
HOST="${DEPLOY_HOST:-}"
REMOTE_DIR="${REMOTE_DIR:-deep-research}"

if [[ -z "$HOST" ]]; then
  echo "❌ Set DEPLOY_HOST=user@server" >&2
  exit 1
fi

echo "🚀 Deploying $IMAGE:$VERSION to $HOST:$REMOTE_DIR"

ssh "$HOST" "mkdir -p $REMOTE_DIR"

# Копируем минимальный набор
scp docker-compose.yml "$HOST:$REMOTE_DIR/docker-compose.yml"
scp .env.example "$HOST:$REMOTE_DIR/.env.example"

ssh "$HOST" "cd $REMOTE_DIR && \
  if [[ ! -f .env ]]; then cp .env.example .env; fi && \
  IMAGE=$IMAGE VERSION=$VERSION docker compose pull deep-research-mcp && \
  IMAGE=$IMAGE VERSION=$VERSION docker compose up -d deep-research-mcp"

echo "✅ Done. Logs: ssh $HOST 'docker logs -f deep-research-mcp'"
echo "✅ Health: ssh $HOST 'docker inspect --format=\"{{.State.Health.Status}}\" deep-research-mcp'"
