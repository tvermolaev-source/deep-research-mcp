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

В Open WebUI (>= 0.5):
1. Открой **Settings → Connections → Tools**
2. Добавь **MCP server**:
   - Name: `Deep Research`
   - URL: `http://host.docker.internal:8765/mcp` (если Open WebUI в Docker)
     или `http://localhost:8765/mcp` (если Open WebUI локально)
3. Включи его в чате — теперь у LLM появится тул `deep_research`.

При вызове тул в UI будет виден прогресс:
> 🔍 Plan: «Okay, the user wants to know about …»
> 🌐 Searching for: ["renewable energy 2025", "solar panel efficiency 2025"]
> 📄 Reading: https://example.com/report
> ✍️ Synthesizing answer…

## ⚙️ Конфигурация (.env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | URL SearXNG (внутренний, в Docker-сети) |
| `SEARXNG_LANGUAGE` | `ru` | Язык поиска |
| `SEARXNG_ENGINES` | `google,bing,duckduckgo` | Список движков |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-совместимый endpoint |
| `LLM_API_KEY` | `ollama` | API-ключ |
| `LLM_MODEL` | `qwen2.5:7b` | Модель для планирования/суммаризации |
| `MCP_HOST` | `0.0.0.0` | Хост MCP-сервера |
| `MCP_PORT` | `8765` | Порт |
| `MAX_ITERATIONS_BALANCED` | `6` | Лимит итераций в режиме balanced |
| `MAX_PARALLEL_CRAWLS` | `5` | Одновременных парсингов |

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

## 📜 Лицензия

MIT
