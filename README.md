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
| `MIN_RESULT_SCORE` | `0.0` | Фильтр по SearXNG-score. 0 = не фильтровать |
| `RESULTS_TOP_K_PER_QUERY` | `10` | Сколько URL оставлять на запрос после реранкинга |
| `DOMAIN_BOOST_THRESHOLD` | `2` | Буст домена, если он встретился по ≥N запросам |
| `BLOCKED_DOMAINS` | *(пусто)* | Список доменов под безусловный отсев (opt-in) |
| `PRIORITY_DOMAINS` | *(пусто)* | Список доменов-экспертов, +100 к рангу (opt-in) |
| `SOCIAL_DOMAINS` | 12 источников | Домены соцсетей — поднимаются в режиме «ищи в социальных сетях» |
| `ACADEMIC_DOMAINS` | 29 источников | Домены научных/академических источников — для режима «факт-чек» |
| `NEWS_DOMAINS` | 36 источников | Домены мировых СМИ — для режима «новости/статьи» |
| `INTENT_DETECTION` | `true` | Включён ли детектор намерений в запросе пользователя |

### 🧭 Адаптивные режимы поиска

Researcher автоматически распознаёт намерение пользователя по тексту запроса
и переключает режим реранкинга. Никаких жёстких блокировок «из коробки» — только
**мягкие приоритеты**: попавшие в приоритет домены поднимаются в топ, остальные
не отрезаются.

| Что пишет пользователь | Распознанный режим | Что происходит |
|---|---|---|
| «ищи в социальных сетях / vk / reddit / twitter» | `social` | Соцсети поднимаются в топ |
| «научное подтверждение / факт-чек / peer-reviewed / arxiv» | `academic` | Академические домены в топе |
| «новости / статьи в СМИ / press» | `news` | СМИ в топе |
| «ищи всё / включи все / без фильтров» | `all` | Никаких приоритетов и блокировок |
| обычный запрос без подсказок | `neutral` | Только то, что задано в `PRIORITY_DOMAINS`/`BLOCKED_DOMAINS` |

Детектор использует встроенные RU+EN-ключевики (расширяются через
`INTENT_KEYWORDS_SOCIAL`, `INTENT_KEYWORDS_ACADEMIC` и т.п.).
Если `INTENT_DETECTION=false` — режим всегда `neutral`.

### 🔎 Как работает реранкинг

Мы не блокируем домены по умолчанию. Что работает из коробки:

1. **Глобальная дедупликация по URL** — один URL не повторяется между запросами и итерациями.
2. **Min-score фильтр** (опционально) — только если задать `MIN_RESULT_SCORE > 0`.
3. **Domain-boost** — домен, встретившийся по ≥`DOMAIN_BOOST_THRESHOLD` разным запросам,
   получает +10·hits к рангу.
4. **Тонкая настройка через ENV** — `BLOCKED_DOMAINS` и `PRIORITY_DOMAINS` (opt-in).
5. **Top-K на запрос** — после реранкинга оставляем `RESULTS_TOP_K_PER_QUERY` URL.
6. **Адаптивный режим** — если пользователь сказал «ищи в X», соответствующие
   домены получают +100 к рангу (остальные не трогаются).

Адаптивные списки **`SOCIAL_DOMAINS` / `ACADEMIC_DOMAINS` / `NEWS_DOMAINS`
уже предзаполнены** авторитетными источниками мирового уровня (12 / 29 / 36
доменов соответственно) — см. `src/deep_research/config.py`.

Их критерии: скорость поступления информации, качество журналистики /
peer-review, охват и доверие аудитории. Например:

* `SOCIAL_DOMAINS` — Twitter/X, Reddit, Facebook, Instagram, LinkedIn,
  TikTok, Threads, Mastodon, YouTube, VK, Telegram.
* `ACADEMIC_DOMAINS` — `.edu` (все университеты мира), `arxiv.org`,
  `biorxiv.org`, `scholar.google.com`, `nature.com`, `science.org`,
  `cell.com`, `thelancet.com`, `nejm.org`, `sciencedirect.com`,
  Springer, Wiley, JSTOR, PLOS, Frontiers, MDPI, IEEE, ACM, …
* `NEWS_DOMAINS` — Reuters, AP, AFP, BBC, Guardian, NYT, WaPo, WSJ,
  FT, Bloomberg, CNN, Al Jazeera, DW, France 24, Le Monde, El País,
  Spiegel, Asahi, SCMP, Straits Times + научпоп-порталы (Nature,
  Scientific American, New Scientist, TechCrunch, The Verge, Wired)
  + ведущие русскоязычные СМИ (РИА, ТАСС, РБК, Ведомости, Коммерсантъ,
  Интерфакс, Лента, Газета).

Чтобы **заменить** дефолтный набор — просто задайте переменную в `.env`,
она переопределит встроенный список (но **не** сольётся с ним).

Все домены указаны как **суффиксы** (.com/....org) или TLD-префиксы (.edu),
поэтому внутренние поддомены (`m.twitter.com`, `cs.mit.edu`, …) тоже матчатся.

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
