# Deep Research MCP Server 🔎

MCP-сервер глубокого поиска. Подключается к **Open WebUI** (и любому MCP-клиенту) и
позволяет запускать полноценный deep-research режим: итеративный веб-поиск через
**SearXNG** + парсинг страниц через **Crawl4AI** + синтез финального ответа через LLM.

Архитектура и логика итеративного research-цикла портированы с
[Vane](https://github.com/ItzCrazyKns/Vane) (TypeScript) на Python.

---

## 🏗️ Архитектура

```
┌─────────────────────┐
│   Open WebUI        │ ← фронтенд, видит стрим прогресса
│   (MCP-клиент)      │
└──────────┬──────────┘
           │ MCP (streamable-http / SSE)
           ▼
┌─────────────────────┐
│  Deep Research MCP  │ ← этот сервер
│  server.py          │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
┌────────┐  ┌─────────┐
│SearXNG │  │Crawl4AI │ ← внешние сервисы
│ поиск  │  │ парсинг │
└────────┘  └─────────┘
           │
           ▼
       ┌────────┐
       │  LLM   │ ← OpenAI-совместимый endpoint
       └────────┘   (Ollama / Open WebUI / OpenAI)
```

## ✨ Что умеет

- 🔍 **`deep_research(query, mode)`** — главный тулчейн:
  - итеративный цикл планирование → поиск → парсинг → синтез (как в Vane)
  - режимы `speed` (2 итерации), `balanced` (6), `quality` (25)
  - **стримит в UI все шаги**: план, поисковые запросы, найденные URL, чтение страниц, чанки финального ответа
- 🌐 **`web_search(query)`** — одиночный запрос в SearXNG (для отладки)
- 📄 **`scrape_url(url)`** — одиночный парсинг страницы через Crawl4AI

## 🚀 Быстрый старт

### 1. Локально (для разработки)

```bash
# Клонируем и заходим
cd Deep_Research

# Создаём venv
python3.11 -m venv .venv
source .venv/bin/activate

# Зависимости
pip install -r requirements.txt

# Конфиг
cp .env.example .env
# отредактируй .env: SEARXNG_URL, LLM_BASE_URL, LLM_MODEL, ...

# Запуск
python -m deep_research
```

Сервер поднимется на `http://localhost:8765` (по умолчанию) и начнёт слушать
MCP-транспорт `streamable-http`. Точка входа для клиентов:
- `http://localhost:8765/mcp` — MCP-over-HTTP

### 2. В Docker

```bash
docker compose up --build
```

Поднимаются два контейнера:
- `searxng` — на `http://localhost:8888` (UI) и `http://searxng:8080` (API)
- `deep-research-mcp` — на `http://localhost:8765/mcp`

## 🔌 Подключение к Open WebUI

### Где в UI
1. Кликни по **аватару / имени пользователя** (правый верхний угол) → **Settings**.
2. В левом меню выбери раздел **Tools** (он же «Инструменты» в русской локали).
3. Справа увидишь блок **«+ Add MCP Server»** — жми туда.

В Open WebUI >= 0.5 нативная поддержка MCP встроена, ничего дополнительно ставить не нужно.

### Какую строку вписать в поле URL
Это самое важное — зависит от того, **где крутится Open WebUI** относительно твоего
контейнера `deep-research-mcp`. Конечная точка у нас всегда одна:
```
http://<адрес_контейнера>:8765/mcp
```
Адрес зависит от сценария:

| Сценарий | URL для подключения |
|---|---|
| OW и MCP на **одном хосте, оба в Docker, одна сеть** | `http://deep-research-mcp:8765/mcp` (имя сервиса) |
| OW локально (без Docker), MCP в Docker на той же машине | `http://localhost:8765/mcp` |
| OW в Docker, MCP на хосте (или в отдельном контейнере без общей сети) | `http://host.docker.internal:8765/mcp` |
| **Удалённый сервер/VPS**: OW и MCP на разных машинах | `http://<публичный_IP_или_домен>:8765/mcp` |
| За reverse-proxy с TLS (nginx/Caddy/Traefik) | `https://<домен>/mcp` |

> ⚠️ Порт `8765/tcp` должен быть **открыт в файрволе** на хосте, где крутится MCP.
> В `docker-compose.yml` у нас он уже проброшен: `ports: "8765:8765"`.

### Остальные поля формы

| Поле | Значение |
|---|---|
| **Name** | `Deep Research` (любое понятное имя) |
| **Type / Transport** | `Streamable HTTP` — соответствует нашему MCP-транспорту |
| **Authentication** | `None` — для локального/частного использования |

### Активация
После добавления:
- в списке тулзов должна появиться строка `Deep Research` → поставь галочку **Enable**;
- по желанию включи **«Show in Model Selector»**, чтобы тул был виден в селекторе модели.

### Проверка в чате
Открой новый чат, выбери ту LLM, которая указана у тебя в `.env` как `LLM_MODEL`
(например, `qwen2.5:7b`), и спроси:

> «Используй deep_research и расскажи про квантовые компьютеры в 2026 году»

Если всё ок, в UI будет стрим прогресса:
> 🔍 Plan: «Okay, the user wants to know about …»
> 🌐 Searching for: ["renewable energy 2025", "solar panel efficiency 2025"]
> 📄 Reading: https://example.com/report
> ✍️ Synthesizing answer…

### Если что-то не работает — чеклист
1. **Не резолвится URL.** С машины, где крутится OW, выполни:
   ```bash
   curl -i http://<адрес>:8765/mcp
   ```
   Должен прийти HTTP-ответ (не `Connection refused`, не `timeout`).
2. **Тул не вызывается моделью.** Включи в **Admin Panel → Settings → Models** → выбранная
   модель → раздел **Capabilities** — должна быть галка **Tool Calling**.
3. **Нет стриминга прогресса, только финальный ответ.** Убедись, что в Settings → Tools →
   MCP Servers выбран тип **Streamable HTTP**, а не устаревший `/sse`.
4. **`421 Misdirected Request`.** Мы уже включили middleware в `server.py`, который
   переписывает Host-заголовок — если всё равно возникает, проверь, что контейнер
   запущен из свежего образа (`docker compose pull && docker compose up -d`).
5. **OW видит тул, но без описания.** Проверь раздел Logs в OW — обычно там видно,
   прошёл ли MCP-handshake. На нашей стороне смотри `docker logs deep-research-mcp`.

## ⚙️ Конфигурация (.env)

Все параметры читаются из переменных окружения. **Если переменная не задана,
используются эффективные встроенные дефолты** — так что сервер работает
качественно даже с пустым `.env`.

| Переменная | Дефолт | Описание |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | URL SearXNG |
| `SEARXNG_LANGUAGE` | `ru` | Язык поиска (можно `en`, `en-all`) |
| `SEARXNG_ENGINES` | `google,bing,duckduckgo` | Список движков |
| `SEARXNG_SAFESEARCH` | `0` | 0/1/2 |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-совместимый endpoint |
| `LLM_API_KEY` | `ollama` | API-ключ |
| `LLM_MODEL` | `qwen2.5:7b` | Модель для планирования/суммаризации |
| `MCP_HOST` | `0.0.0.0` | Хост MCP-сервера |
| `MCP_PORT` | `8765` | Порт |
| `MAX_ITERATIONS_SPEED/BALANCED/QUALITY` | `2 / 6 / 25` | Лимиты итераций по режимам |
| `MAX_PARALLEL_CRAWLS` | `5` | Одновременных парсингов |
| `MAX_RESULTS_PER_QUERY` | `10` | Сколько просить у SearXNG на запрос |
| `CRAWL_TIMEOUT_SEC` | `60` | Таймаут парсинга URL |
| `MIN_RESULT_SCORE` | `0.5` | **Фильтр**: отбрасываем URL с SearXNG-score ниже этого |
| `RESULTS_TOP_K_PER_QUERY` | `5` | **Фильтр**: после реранкинга оставляем top-K на запрос |
| `DOMAIN_BOOST_THRESHOLD` | `2` | **Буст**: домен, появившийся ≥2 раза по разным запросам — поднимается в топ |
| `BLOCKED_DOMAINS` | `facebook,vk,instagram,tiktok,...` | **Домены-мусор**, отбрасываются (через запятую) |
| `PRIORITY_DOMAINS` | `ria,tass,rbc,hightech,hse,...` | **Домены-эксперты**, получают +50 к рангу (через запятую) |

### 🔎 Зачем нужна фильтрация выдачи

SearXNG (особенно Bing/DuckDuckGo) в «сыром» виде отдаёт много шума: PDF-фолдеры,
посты соцсетей, записи блогов с плохими метаданными. На длинных англоязычных
запросах только ~10–20% результатов релевантные. Без фильтра ресерчер читает
всё подряд и тратит итерации на мусор.

Мы применяем **5-уровневый фильтр** на каждый запрос:

1. **Глобальная дедупликация по URL** — один и тот же URL не повторяется ни
   в одной итерации.
2. **Блэклист доменов** — `BLOCKED_DOMAINS` выкидываются безусловно.
3. **Min-score фильтр** — URL с `score < MIN_RESULT_SCORE` отбрасываются
   (SearXNG считает score на основе позции в поисковых движках: типично 1.5–5
   для нормальных результатов, ~0.1 для мусора).
4. **Domain-boost** — если один домен всплыл по разным запросам внутри одной
   итерации, его ранг повышается (простой алгоритм «упоминают все → авторитет»).
5. **Top-K на запрос** — после всех фильтров оставляем только
   `RESULTS_TOP_K_PER_QUERY` лучших URL — это то, что реально идёт в LLM/crawl.

Приоритетные домены (`PRIORITY_DOMAINS`) получают +50 к рангу — например,
крупные русскоязычные СМИ и научные порталы по дефолту вылезают наверх на
русскоязычных запросах.

После фильтрации LLM возвращается не только выборка, но и **сколько URL-ов
было отброшено** — модель видит «выдача сокращена» и при необходимости
переформулирует запросы на следующей итерации.

## 🧪 Тесты

```bash
pip install pytest pytest-asyncio
pytest -v
```

Покрытие:
- `test_streaming.py` — EventBus
- `test_searxng_client.py` — клиент SearXNG (мок httpx)
- `test_tools.py` — fallback HTML-парсер
- `test_researcher.py` — полный цикл Researcher (моки LLM/SearXNG/Crawl)
- `test_filtering.py` — фильтрация и реранкинг (blocked, min-score, top-K, boost, dedup)

## 📁 Структура проекта

```
Deep_Research/
├── docker-compose.yml         # SearXNG + MCP-сервер
├── Dockerfile                 # образ MCP-сервера
├── requirements.txt
├── pyproject.toml
├── .env.example
├── src/deep_research/
│   ├── server.py              # FastMCP-сервер (entrypoint)
│   ├── researcher.py          # главный цикл итеративного поиска
│   ├── llm_client.py          # OpenAI-compatible клиент + streaming + tool calls
│   ├── prompts.py             # промпты (портированы с Vane)
│   ├── streaming.py           # EventBus — стрим событий в UI
│   ├── config.py              # конфигурация из .env
│   ├── types.py               # dataclasses + tool-схемы
│   └── tools/
│       ├── searxng_client.py  # HTTP-клиент SearXNG
│       └── crawl_client.py    # Crawl4AI-обёртка + httpx-fallback
└── tests/
    ├── test_streaming.py
    ├── test_searxng_client.py
    ├── test_tools.py
    └── test_researcher.py
```

## 🧬 Портировано с Vane

| Vane (TypeScript) | Deep Research MCP (Python) |
|---|---|
| `lib/agents/search/researcher/index.ts` | `src/deep_research/researcher.py` |
| `…/actions/registry.ts` | inline в researcher.py |
| `…/actions/plan.ts` | промпт `__reasoning_preamble` в `prompts.py` |
| `…/actions/search/webSearch.ts` | `web_search` tool |
| `…/actions/search/baseSearch.ts` | `web_search` tool + дедуп по URL |
| `…/actions/scrapeURL.ts` | `scrape_url` tool + `_extract_facts` |
| `lib/searxng` | `tools/searxng_client.py` |
| `lib/scraper` | `tools/crawl_client.py` (Crawl4AI) |
| `lib/session.emitBlock` | `streaming.EventBus` |

## � CI/CD и деплой

См. **[DEPLOY.md](DEPLOY.md)** — полная инструкция:
- GitHub Actions: тесты + автосборка Docker-образа
- Multi-arch образ (amd64 + arm64) в GHCR
- `./scripts/release.sh patch` → push тега → авторелиз
- `./scripts/deploy.sh v0.1.0 user@server` → деплой одной командой

## �📜 Лицензия

MIT
