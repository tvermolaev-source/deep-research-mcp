# 🚀 Деплой Deep Research MCP на свой сервер

Пошаговая инструкция: от коммита до работающего MCP-сервера на твоём хостинге.

## 1. Подготовка GitHub-репозитория

### 1.1. Создать репо

Зайди на https://github.com/new и создай **публичный** репозиторий:
- **Repository name**: `deep-research-mcp`
- **Description**: `MCP server for deep research backed by SearXNG and Crawl4AI`
- **Public** ✓
- Без README/LICENSE/.gitignore — у нас уже всё есть

### 1.2. Добавить remote и запушить

```bash
cd /Users/ermolaevtv/Работа/Вайбкодинг/Deep_Research

# Если используешь SSH (замени username на свой):
git remote add origin git@github.com:<username>/deep-research-mcp.git

# Или через PAT:
git remote add origin https://<TOKEN>@github.com/<username>/deep-research-mcp.git

# Переименовать ветку и запушить
git branch -M main
git push -u origin main
```

### 1.3. Включить GitHub Actions

На странице репо → **Settings → Actions → General** → разрешить workflows (по умолчанию уже разрешены).

### 1.4. (опционально) Настроить Docker Hub

Если хочешь параллельно публиковать в Docker Hub:
- **Settings → Secrets and variables → Actions → New repository secret**
- `DOCKERHUB_USERNAME` — твой логин
- `DOCKERHUB_TOKEN` — Access Token из https://hub.docker.com/settings/security

Без этого образ всё равно будет литься в **GHCR** (`ghcr.io/<username>/deep-research-mcp`) — для этого токен не нужен.

## 2. CI/CD — что происходит автоматически

### Workflow `CI` (`.github/workflows/ci.yml`)

Запускается на каждый push/PR в `main` и `develop`:
- ✅ Тесты на Python 3.11 + 3.12
- ✅ Syntax check
- ✅ Сборка Docker-образа + smoke-тест

### Workflow `Release` (`.github/workflows/release.yml`)

Запускается на push тега вида `v*.*.*`:
- 🐳 Multi-arch build (linux/amd64 + linux/arm64)
- 📦 Push в GHCR (и Docker Hub если настроен)
- 📝 Создаёт GitHub Release с автогенерацией changelog

### Dependabot (`.github/dependabot.yml`)

Раз в неделю проверяет обновления pip/docker/github-actions и создаёт PR.

## 3. Первый релиз

```bash
# Создаёт тег v0.1.0 и коммит с бампом версии
./scripts/release.sh patch

# Пуш — триггернёт Release workflow
git push origin main --tags
```

Через ~3 минуты образ будет доступен:
```
ghcr.io/<username>/deep-research-mcp:0.1.0
ghcr.io/<username>/deep-research-mcp:latest
```

## 4. Деплой на свой сервер

### Вариант A — одной командой через `deploy.sh`

```bash
DEPLOY_HOST=user@your-server.com ./scripts/deploy.sh v0.1.0
```

Скрипт:
1. Копирует `docker-compose.yml` и `.env.example` на сервер
2. Создаёт `.env` если нет
3. Подтягивает свежий образ
4. Перезапускает контейнер

### Вариант B — вручную на сервере

```bash
# На сервере:
mkdir -p deep-research && cd deep-research
curl -O https://raw.githubusercontent.com/<username>/deep-research-mcp/main/deploy-compose.yml
mv deploy-compose.yml docker-compose.yml
curl -O https://raw.githubusercontent.com/<username>/deep-research-mcp/main/.env.example
cp .env.example .env
$EDITOR .env  # поправь LLM_BASE_URL, LLM_MODEL

# Если образ из приватного GHCR (только твой аккаунт):
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <username> --password-stdin

docker compose pull
docker compose up -d

# Проверить
docker compose ps
docker compose logs -f deep-research-mcp
```

### Вариант C — Docker Hub

Замени `IMAGE` в `.env`:
```
IMAGE=docker.io/<your-dockerhub-user>/deep-research-mcp
```

## 5. Подключение к Open WebUI

В Open WebUI:
- **Settings → Connections → Tools → Add MCP server**
- URL: `http://<your-server>:8765/mcp` (или `http://host.docker.internal:8765/mcp` если Open WebUI локально)

## 6. Обновление

Каждый раз когда пушишь новый тег:
```bash
./scripts/release.sh patch  # или minor/major
git push origin main --tags
```

Потом на сервере:
```bash
cd deep-research && docker compose pull && docker compose up -d
```

Или одной командой с мака:
```bash
DEPLOY_HOST=user@your-server.com ./scripts/deploy.sh v0.1.1
```

## 7. Чек-лист после первого деплоя

- [ ] `curl http://<server>:8765/mcp -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'` — должен вернуть список из 3 тулов
- [ ] SearXNG UI открывается на `http://<server>:8888`
- [ ] В Open WebUI виден инструмент `deep_research`
- [ ] Тестовый запрос через тул `deep_research("what is MCP?", mode="speed")` отдаёт результат за <30 секунд
