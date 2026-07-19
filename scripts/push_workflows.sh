#!/usr/bin/env bash
# Загружает workflow-файлы через GitHub Contents API (не требует workflow scope).
# Использование:
#   GITHUB_TOKEN=ghp_... ./scripts/push_workflows.sh

set -euo pipefail

TOKEN="${GITHUB_TOKEN:-}"
REPO="${REPO:-tvermolaev-source/deep-research-mcp}"
BRANCH="${BRANCH:-main}"
API="https://api.github.com/repos/${REPO}/contents"

if [[ -z "$TOKEN" ]]; then
  echo "Set GITHUB_TOKEN=ghp_..." >&2
  exit 1
fi

upload_file() {
  local path="$1"
  local b64
  b64=$(base64 -i "$path" | tr -d '\n')

  # Получаем sha существующего файла (если есть)
  local existing_sha=""
  existing_sha=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "$API/$path?ref=$BRANCH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('sha','')) if isinstance(d, dict) else print('')" 2>/dev/null || echo "")

  local payload
  if [[ -n "$existing_sha" ]]; then
    payload=$(python3 -c "import json; print(json.dumps({'message':'ci: add/update $path','content':'$b64','sha':'$existing_sha','branch':'$BRANCH'}))")
  else
    payload=$(python3 -c "import json; print(json.dumps({'message':'ci: add $path','content':'$b64','branch':'$BRANCH'}))")
  fi

  echo "Uploading $path..."
  curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "$payload" "$API/$path" | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK' if d.get('content') else d)"
}

upload_file ".github/workflows/ci.yml"
upload_file ".github/workflows/release.yml"
echo "Done. Verify: https://github.com/${REPO}/actions"
