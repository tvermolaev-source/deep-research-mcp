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
| `SEARXNG_CATEGORIES` | `general` | Дефолтные SearXNG-категории (используются, если LLM-планировщик не сработал). Допустимые: `general news science social videos images files music it map` |
| `SEARXNG_ENGINES` | `google,bing,duckduckgo` | Список движков |
| `SEARXNG_SAFESEARCH` | `0` | 0/1/2 |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-совместимый endpoint |
| `LLM_API_KEY` | `ollama` | API-ключ |
| `LLM_MODEL` | `qwen2.5:7b` | Базовая модель (используется обеими ролями, если роли не заданы) |
| `LLM_PLANNER_MODEL` | *(пусто → `LLM_MODEL`)* | **Сильная** модель для планирования источников и финального синтеза |
| `LLM_WORKER_MODEL` | *(пусто → `LLM_MODEL`)* | **Слабая/дешёвая** модель для извлечения фактов из чанков |
| `LLM_WORKER_BASE_URL` | *(пусто → `LLM_BASE_URL`)* | Опц. отдельный endpoint для worker'а (например, локальный Ollama с 3B-моделью) |
| `LLM_WORKER_API_KEY` | *(пусто → `LLM_API_KEY`)* | Опц. отдельный API-ключ для worker'а |
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

### 🧠 LLM-планирование источников (до старта цикла)

Помимо keyword-детектора по тексту, в начале каждого исследования Researcher
выполняет **один отдельный LLM-вызов** (через planner-модель), который
классифицирует запрос и выбирает SearXNG-категории:

| Intent | SearXNG categories | Когда выбирается |
|---|---|---|
| `social` | `social` | «что обсуждают в твиттере / на реддите / в телеграме» |
| `academic` | `science` (+ `general` для подстраховки) | «научные статьи / факт-чек / peer-reviewed / arxiv» |
| `news` | `news` | «последние новости / пресс-релизы» |
| `videos` | `videos` (+ `general`) | «видео на ютубе / обучающие ролики» |
| `general` | `general` | обычный web-поиск |
| `all` | `general + news + science + social + videos` | «ищи всё» |

План стримится в UI как `plan`-событие и сохраняется в `self._source_plan`,
оттуда попадает в `SearXNGClient.search_many(categories=…)` — поиск сразу
идёт по правильным категориям (а не только по «general»).

При любой ошибке LLM (нет endpoint'а, битый JSON) — fallback на `SourcePlan.default()`
с категорией `general`. Пайплайн не падает.

### 🧬 Модельный роутинг: planner (сильная) / worker (слабая)

В Researcher'е используются **две роли LLM** через `LLMFactory`:

| Роль | Что делает | Требования | Пример модели |
|---|---|---|---|
| **Planner** | • `_plan_sources()` — выбор источника перед циклом<br>• Главный цикл `research()` — каждый ход (preamble + tools)<br>• `_synthesize()` — финальный ответ | Логика, JSON, следование инструкциям | `qwen2.5:14b/32b`, `llama3.1:70b`, `gpt-4o`, `claude-sonnet` |
| **Worker** | • `_extract_facts()` — извлечение фактов из скрапленного контента (chunked) | Быстро, дёшево, JSON | `qwen2.5:3b`, `llama3.2:3b`, `phi3:mini`, `gpt-4o-mini` |

Если роли не заданы в `.env` — обе используют базовый `LLM_MODEL`
(полная обратная совместимость).

Пример конфигурации:

```bash
# Сильная модель — планирование и синтез
LLM_PLANNER_MODEL=qwen2.5:14b
# Слабая модель — извлечение фактов
LLM_WORKER_MODEL=qwen2.5:3b
# Опционально: worker на отдельном endpoint
LLM_WORKER_BASE_URL=http://localhost:11434/v1
LLM_WORKER_API_KEY=ollama
```

`LLMFactory` сам решает — открывать два независимых HTTP-клиента
или переиспользовать один, если конфиги planner и worker совпадают.

### 📜 Полный flow одного исследования (sequence diagram)

Вот что происходит от момента, как ты отправил запрос в Open WebUI,
до момента, как ты увидел финальный ответ со ссылками:

```
Пользователь       Open WebUI           MCP-сервер              LLM (planner)       SearXNG          LLM (worker)       Crawl4AI
     │                  │                     │                       │                 │                  │                │
     │ "расскажи про    │                     │                       │                 │                  │                │
     │  квантовые        │                     │                       │                 │                  │                │
     │  компьютеры 2026" │                     │                       │                 │                  │                │
     ├─────────────────►│ deep_research(      │                       │                 │                  │                │
     │                  │   query, mode)      │                       │                 │                  │                │
     │                  ├────────────────────►│                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ 0. _plan_sources()    │                 │                  │                │
     │                  │                     ├──────────────────────►│                 │                  │                │
     │                  │                     │ SOURCE_PLANNER_PROMPT │                 │                  │                │
     │                  │                     │   + query             │                 │                  │                │
     │                  │                     │                       │ JSON: intent,   │                  │                │
     │                  │                     │                       │   categories    │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │ self._source_plan =   │                 │                  │                │
     │                  │                     │   SourcePlan(...)     │                 │                  │                │
     │                  │                     │ emit_plan("academic") │                 │                  │                │
     │                  │◄───── log ──────────│                       │                 │                  │                │
     │                  │  "plan: academic    │                       │                 │                  │                │
     │                  │   [science]"        │                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ 1. Главный цикл       │                 │                  │                │
     │                  │                     │ tools=[preamble,      │                 │                  │                │
     │                  │                     │         web_search,   │                 │                  │                │
     │                  │                     │         scrape_url,   │                 │                  │                │
     │                  │                     │         done]         │                 │                  │                │
     │                  │                     ├──────────────────────►│                 │                  │                │
     │                  │                     │ system + history      │                 │                  │                │
     │                  │                     │                       │ tool_call(      │                  │                │
     │                  │                     │                       │   preamble)     │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │ emit_plan("Plan:…")   │                 │                  │                │
     │                  │◄───── log ──────────│                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ tool_call(            │                 │                  │                │
     │                  │                     │   web_search)         │                 │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │ search_many(          │                 │                  │                │
     │                  │                     │   queries=[...],      │                 │                  │                │
     │                  │                     │   categories=["science"]              │                  │                │
     │                  │                     ├───────────────────────┼────────────────►│                 │                │
     │                  │                     │                       │                 │ JSON results     │                │
     │                  │                     │◄──────────────────────┼─────────────────┤                  │                │
     │                  │                     │ rank_score + should_drop (FilterPolicy) │                  │                │
     │                  │                     │ emit_search_results   │                 │                  │                │
     │                  │◄───── log ──────────│                       │                 │                  │                │
     │                  │  "search_result:    │                       │                 │                  │                │
     │                  │   5 URLs"           │                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ tool_call(            │                 │                  │                │
     │                  │                     │   scrape_url)         │                 │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │ crawl_many(urls)      │                 │                  │                │
     │                  │                     ├───────────────────────┼─────────────────┼──────────────────┼───────────────►│
     │                  │                     │                       │                 │                  │                │ markdown
     │                  │                     │◄──────────────────────┼─────────────────┼──────────────────┼────────────────┤
     │                  │                     │ _extract_facts() ──► WORKER LLM        │                  │                │
     │                  │                     │                       │                 │   chunked JSON   │                │
     │                  │                     │                       │                 │◄─────────────────┤                │
     │                  │                     │ emit_read_done(url, facts)             │                  │                │
     │                  │◄───── log ──────────│                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ tool_call(done)       │                 │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ 2. _synthesize() ──► PLANNER LLM      │                  │                │
     │                  │                     │                       │ stream=markdown │                  │                │
     │                  │                     │◄──────────────────────┤                 │                  │                │
     │                  │                     │ emit_synthesis_chunk  │                 │                  │                │
     │                  │◄───── log ──────────│ (стрим чанков ответа) │                 │                  │                │
     │                  │   "## Квантовые…"   │                       │                 │                  │                │
     │                  │◄────────────────────│                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │                     │ emit_done(answer,     │                 │                  │                │
     │                  │                     │          sources)     │                 │                  │                │
     │                  │◄───── log ──────────│                       │                 │                  │                │
     │                  │                     │                       │                 │                  │                │
     │                  │ TextContent(markdown+│                       │                 │                  │                │
     │                  │   sources)          │                       │                 │                  │                │
     │                  │◄────────────────────│                       │                 │                  │                │
     │   видит финал    │                     │                       │                 │                  │                │
     │◄─────────────────┤                     │                       │                 │                  │                │
```

### 🔍 Куда какой LLM ходит (всё в одном месте)

| Этап | LLM-роль | Модель (если задана) | Что делает |
|---|---|---|---|
| 0. `_plan_sources()` | **Planner** | `LLM_PLANNER_MODEL` | Классифицирует запрос, выбирает SearXNG-категории |
| 1. Главный цикл `research()` | **Planner** | `LLM_PLANNER_MODEL` | Каждый ход: preamble + выбор tools + JSON-валидация |
| 2. `scrape_url` → `_extract_facts` | **Worker** | `LLM_WORKER_MODEL` | Извлечение фактов из чанков (механическая работа) |
| 3. `_synthesize()` | **Planner** | `LLM_PLANNER_MODEL` | Финальный markdown-ответ со ссылками |

Если роли не заданы — **все 4 этапа** идут через `LLM_MODEL` (обратная совместимость).

### 📊 Что увидит пользователь в Open WebUI

После запуска `deep_research` в UI приходит стрим событий (через `ctx.session.send_log_message`):

1. `plan`: *"source plan: academic (science, general) — нужны научные источники"*
2. `plan`: *"Okay, the user wants to know about quantum computers in 2026…"*
3. `search_start`: *queries=["quantum computing 2026", "quantum supremacy recent"]*
4. `search_result`: 5-10 URL с заголовками
5. `read_start`: *urls=[…]*
6. `read_done`: *url + первые 500 символов extracted_facts*
7. `synthesis_chunk`: чанки markdown-ответа (печатаются как пишутся)
8. `done`: финальный ответ + список источников

Все эти шаги прокидываются через MCP-шину (`EventBus` → `send_log_message`), Open WebUI рисует их как «task steps» в чате.

### 🛡️ Поведение при ошибках

| Сценарий | Что произойдёт |
|---|---|
| LLM-планировщик недоступен (endpoint не отвечает) | `_plan_sources()` ловит исключение, `self._source_plan` остаётся дефолтным (`categories=["general"]`). Цикл продолжается как раньше. |
| LLM вернул битый JSON / текст без JSON | `_parse_source_plan()` возвращает `None` → план остаётся дефолтным. |
| LLM вернул невалидные категории/intent | Whitelist-фильтр, fallback на `general` + `categories=["general"]`. |
| Worker-LLM недоступен во время `_extract_facts` | Возвращается исходный chunk текста без извлечения фактов (логируется warning). |
| Planner и worker идентичны по конфигу | `LLMFactory` создаёт **один** HTTP-клиент и переиспользует для обеих ролей — никакого overhead'а. |
| `LLM_PLANNER_MODEL` / `LLM_WORKER_MODEL` пустые | Обе роли используют базовый `LLM_MODEL`. Полная обратная совместимость. |

### 🧪 Что покрыто тестами (73 теста, все зелёные)

```
tests/test_researcher.py       — 1 e2e-тест: цикл plan→search→scrape→done→synthesis
tests/test_llm_factory.py      — 13 тестов: factory shared/distinct, ENV-overrides,
                                      SourcePlan parser (strict JSON / markdown fence /
                                      garbage recovery / invalid categories / intent),
                                      worker_endpoint
tests/test_streaming.py        — pub/sub EventBus, close-unblocks-subscribers
tests/test_filtering.py        — intent detection, FilterPolicy, rank_score, should_drop
tests/test_searxng_client.py   — SearXNGClient search/search_many с categories
tests/test_tools.py            — CrawlClient
```

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
│   ├── intent.py              # детектор намерений по тексту запроса (RU+EN)
│   ├── filter_policy.py       # политики фильтрации/реранкинга по intent
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
    ├── test_researcher.py
    └── test_filtering.py      # intent-детектор + политики + реранкинг (52 теста)
```

## 🆕 Что нового в v0.3.0

- **Адаптивная фильтрация по доменам** — Researcher распознаёт намерение пользователя
  по тексту запроса (`social` / `academic` / `news` / `all` / `neutral`) и мягко
  поднимает нужный тип источников в топ. Никаких автоблоков — другие источники не
  отсекаются, только получают меньший ранг. Детектор использует RU+EN ключевые слова,
  расширяемые через `INTENT_KEYWORDS_*`.
- **Предзаполненные наборы доменов мирового уровня** —
  `SOCIAL_DOMAINS` (12), `ACADEMIC_DOMAINS` (29), `NEWS_DOMAINS` (36). Подобраны
  по скорости поступления информации, качеству журналистики/peer-review, охвату и
  доверию аудитории. Переопределяются через `.env` целиком (без слияния с дефолтом).
- **Никаких жёстких блокировок по доменам** — убрали авто-блоклист соцсетей.
  Хотите отсечь конкретный домен — задайте `BLOCKED_DOMAINS=…` (opt-in).
- **Новые модули**:
  * `src/deep_research/intent.py` — детектор намерений по тексту запроса.
  * `src/deep_research/filter_policy.py` — политики реранкинга под каждый intent
    (`make_policy`, `matches_domain`, `rank_score`, `should_drop`).
- **Расширенные тесты** — `tests/test_filtering.py` покрывает детектор, политики,
  матчинг доменов (включая `.edu`-TLD) и интеграцию с Researcher. **52 теста,
  все зелёные.**
- **API для MCP/UI** — результат `web_search` теперь содержит поле `policy`
  (`intent`, `priority_count`, `blocked_count`), чтобы клиентский UI мог
  показать, в каком режиме выполнен поиск.

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
