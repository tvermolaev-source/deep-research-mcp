"""MCP-сервер Deep Research.

Точка входа: ``python -m deep_research.server``.

Поднимает FastMCP-сервер с инструментами:
  • deep_research(query, mode) — запуск полного пайплайна
  • web_search(query)         — одиночный поиск (для отладки)
  • scrape_url(url)           — одиночный парсинг (для отладки)

Транспорт: streamable-http (MCP 2025-03-26) — Open WebUI подключается
к нему по SSE и видит события прогресса в реальном времени.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from mcp.types import (
    TextContent,
    Tool,
)

from .config import get_config
from .researcher import Researcher, ResearchInput, ResearchOutput
from .streaming import EventBus
from .tools import CrawlClient, SearXNGClient
from .types import SearchMode

logger = logging.getLogger("deep_research")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ─────────────────────────────────────────────────────────────────────
# Описания инструментов для MCP
# ─────────────────────────────────────────────────────────────────────
TOOL_DEEP_RESEARCH = Tool(
    name="deep_research",
    description=(
        "Run a full deep-research pipeline: iterative web search + content "
        "scraping via SearXNG and Crawl4AI, then synthesize a final answer. "
        "Returns a markdown answer with inline citations and a list of sources. "
        "Progress is streamed via notifications while the tool runs."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's research question.",
            },
            "mode": {
                "type": "string",
                "enum": ["speed", "balanced", "quality"],
                "default": "balanced",
                "description": (
                    "Research depth: speed (2 iters), balanced (6), quality (25)."
                ),
            },
            "chat_history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "default": [],
                "description": "Optional last N chat messages for context.",
            },
        },
        "required": ["query"],
    },
)

TOOL_WEB_SEARCH = Tool(
    name="web_search",
    description="One-shot web search via SearXNG. Returns top results.",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    },
)

TOOL_SCRAPE_URL = Tool(
    name="scrape_url",
    description="One-shot URL scraping via Crawl4AI (markdown).",
    inputSchema={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
        },
        "required": ["url"],
    },
)


# ─────────────────────────────────────────────────────────────────────
# FastMCP-сервер
# ─────────────────────────────────────────────────────────────────────
def build_server() -> FastMCP:
    """Создаёт и настраивает FastMCP-сервер с инструментами Deep Research."""
    server = FastMCP(
        "deep-research",
        instructions=(
            "Deep Research MCP server. Provides iterative deep-research tools "
            "backed by SearXNG (search) and Crawl4AI (content scraping). "
            "Use `deep_research` for end-to-end investigations."
        ),
    )

    @server.tool(
        name="deep_research",
        description=TOOL_DEEP_RESEARCH.description,
    )
    async def deep_research_tool(
        query: str,
        mode: str = "balanced",
        chat_history: list[dict[str, str]] | None = None,
    ) -> list[TextContent]:
        """Запускает полный пайплайн исследования и возвращает финальный ответ.

        Параллельно стримит события прогресса через ctx (через
        send_log_message / send_progress). MCP-клиенты (Open WebUI)
        видят их в UI в реальном времени.
        """
        bus = EventBus()
        ctx = server.get_context()
        run_task = asyncio.create_task(
            _run_research(query, mode, chat_history or [], bus, ctx)
        )
        # Параллельно публикуем события в логи MCP, чтобы клиент их видел
        async for event in bus.stream():
            await _emit_event(event, ctx)
            if event.type == "done":
                break
        # Дожидаемся завершения run_task
        result = await run_task
        text = _format_result(result)
        return [TextContent(type="text", text=text)]

    @server.tool(
        name="web_search",
        description=TOOL_WEB_SEARCH.description,
    )
    async def web_search_tool(query: str, max_results: int = 10) -> list[TextContent]:
        cfg = get_config().searxng
        async with SearXNGClient(cfg) as client:
            results = await client.search(query, max_results=max_results)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    [r.to_dict() for r in results], ensure_ascii=False, indent=2
                ),
            )
        ]

    @server.tool(
        name="scrape_url",
        description=TOOL_SCRAPE_URL.description,
    )
    async def scrape_url_tool(url: str) -> list[TextContent]:
        async with CrawlClient(get_config().limits) as client:
            res = await client.crawl_one(url)
        return [
            TextContent(
                type="text",
                text=json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
            )
        ]

    return server


# ─────────────────────────────────────────────────────────────────────
# Запуск исследования и трансляция событий
# ─────────────────────────────────────────────────────────────────────
async def _run_research(
    query: str,
    mode: str,
    chat_history: list[dict[str, str]],
    bus: EventBus,
    ctx: Any,
) -> ResearchOutput:
    inp = ResearchInput(
        query=query,
        mode=mode,  # type: ignore[arg-type]
        chat_history=chat_history,
    )
    # Передаём query, чтобы Researcher мог распознать намерение
    # («ищи в соцсетях», «научный факт-чек» и т.п.) один раз
    # при инициализации.
    researcher = Researcher(event_bus=bus, query=query)
    try:
        out = await researcher.research(inp)
    except Exception as exc:  # noqa: BLE001
        logger.exception("deep_research failed")
        await bus.emit_error(str(exc))
        raise
    finally:
        await bus.close()
    return out


async def _emit_event(event: Any, ctx: Any) -> None:
    """Публикует событие исследования в MCP-логи клиента.

    Open WebUI отображает такие сообщения как ход выполнения задачи
    (searching/crawling/synthesizing). Уровень — info для прогресса,
    error — для ошибок.
    """
    try:
        payload = json.dumps(event.data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        payload = str(event.data)
    level = "error" if event.type == "error" else "info"
    try:
        await ctx.session.send_log_message(level=level, data=payload, logger="deep-research")
    except Exception:  # noqa: BLE001
        # Если клиент не поддерживает — не валим весь запуск
        logger.debug("send_log_message failed for %s", event.type)


def _format_result(out: ResearchOutput) -> str:
    """Markdown-форматирование ответа + источников."""
    parts: list[str] = [out.answer.strip()]
    if out.sources:
        parts.append("\n\n## Sources\n")
        for i, s in enumerate(out.sources, 1):
            title = s.get("title") or s.get("url", "")
            url = s.get("url", "")
            parts.append(f"{i}. [{title}]({url})")
    parts.append(f"\n\n<sub>Iterations: {out.iterations} · Sources: {len(out.sources)}</sub>")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────
class _MCPMiddleware:
    """ASGI-middleware поверх FastMCP-приложения.

    Делает две вещи, нужные для нормальной работы с Open WebUI:

    1. Перехватывает 421 Misdirected Request, которые uvicorn/starlette
       отдают при Host-заголовке вроде ``deep-research:8765`` или
       ``<внешний_IP>:8765``. Переписывает scope['headers'] так, чтобы
       uvicorn думал, что host валиден (любой host принимается).

       Альтернатива — флаг ``host_header_validation=False`` в uvicorn.run,
       но он появился только в uvicorn>=0.32 и не работает в более
       старых версиях. Middleware совместим с любой версией.

    2. Отдаёт минимальный валидный OpenAPI-JSON на ``/openapi.json``,
       ``/mcp/openapi.json`` и ``/docs``, чтобы Open WebUI (и другие
       клиенты) не получали 404 при попытке авто-детекта.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Подменяем Host в заголовках на 127.0.0.1:port, чтобы любой
            # uvicorn-валидатор Host был доволен. Внутренний app всё равно
            # получает оригинальный host через scope['headers'] ниже, если
            # ему это нужно (MCP-host он не использует).
            port = scope.get("server", (None, None))[1] or 8765
            new_host_header = f"127.0.0.1:{port}".encode("latin-1")
            new_headers = []
            for name, value in scope.get("headers", []):
                if name.lower() == b"host":
                    new_headers.append((b"host", new_host_header))
                else:
                    new_headers.append((name, value))
            # Если host-заголовка вообще не было — добавим
            if not any(n.lower() == b"host" for n, _ in new_headers):
                new_headers.append((b"host", new_host_header))
            scope = dict(scope)
            scope["headers"] = new_headers

            # Тихий ответ на openapi.json / docs
            path = scope.get("path", "")
            method = scope.get("method", "GET")
            if method == "GET" and (
                path == "/openapi.json"
                or path == "/docs"
                or path == "/docs/oauth2-redirect"
                or path.endswith("/openapi.json")
            ):
                from starlette.responses import JSONResponse

                resp = JSONResponse(
                    {
                        "openapi": "3.1.0",
                        "info": {"title": "deep-research-mcp", "version": "1.0"},
                        "paths": {},
                    }
                )
                await resp(scope, receive, send)
                return

        await self.inner(scope, receive, send)


def main() -> None:
    """Запуск MCP-сервера.

    Используется uvicorn напрямую (а не server.run()) — это позволяет
    обернуть ASGI-приложение в middleware, который:
      * отключает Host-валидацию (без этого Open WebUI получает
        421 Misdirected Request при обращении по имени контейнера/IP);
      * отдаёт валидный JSON на /openapi.json.
    Подход через middleware не зависит от версии uvicorn и работает
    в том числе со старыми версиями, где нет kwarg host_header_validation.
    """
    import uvicorn

    cfg = get_config()
    server = build_server()
    logger.info(
        "Starting Deep Research MCP server on %s:%d (SearXNG=%s, LLM=%s)",
        cfg.server.host,
        cfg.server.port,
        cfg.searxng.url,
        cfg.llm.base_url,
    )

    # Берём ASGI-приложение FastMCP. В разных версиях mcp/FastMCP атрибут
    # называется по-разному:
    #   - app                          — ASGI-приложение (новые версии)
    #   - streamable_http_app()        — фабрика (mcp >= 1.2)
    #   - sse_app()                    — legacy SSE-транспорт
    app = None
    for getter in (
        lambda: getattr(server, "app", None),
        lambda: getattr(server, "streamable_http_app", None),
        lambda: getattr(server, "sse_app", None),
    ):
        cand = getter()
        if cand is None:
            continue
        app = cand() if callable(cand) else cand
        if app is not None:
            break

    if app is None:
        # Fallback — старый путь (выставит настройки и запустит встроенным).
        # Хост-валидация в этом случае останется как есть, но MCP будет
        # доступен по localhost.
        server.settings.host = cfg.server.host
        server.settings.port = cfg.server.port
        server.run(transport="streamable-http")
        return

    wrapped_app = _MCPMiddleware(app)

    uvicorn.run(
        wrapped_app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
