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
    researcher = Researcher(event_bus=bus)
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
def main() -> None:
    """Запуск MCP-сервера.

    Используется uvicorn напрямую (а не server.run()) — это позволяет
    отключить валидацию Host-заголовка, без которой Open WebUI получает
    421 Misdirected Request при обращении по имени контейнера/IP.
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

    # Берём ASGI-приложение FastMCP, чтобы запустить его через uvicorn
    # с нужными флагами (host_header_validation=False).
    # В разных версиях mcp/FastMCP атрибут называется по-разному:
    #   - app        — ASGI-приложение (новые версии)
    #   - streamable_http_app() — фабрика (mcp >= 1.2)
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
        # Fallback — старый путь (выставит настройки и запустит встроенным)
        server.settings.host = cfg.server.host
        server.settings.port = cfg.server.port
        server.run(transport="streamable-http")
        return

    # ASGI-middleware: гасит 404 на /openapi.json и /docs — некоторые клиенты
    # (например, Open WebUI) сначала пробуют загрузить OpenAPI-схему. MCP-сервер
    # её не отдаёт, но отвечать 404 на эти два пути неприятно — лучше вернуть
    # минимальный валидный JSON, чтобы клиент не сыпал ошибками в UI.
    from starlette.responses import JSONResponse
    from starlette.routing import Match
    from starlette.types import Scope

    class _QuietOpenAPIMiddleware:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope: Scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                method = scope.get("method", "GET")
                if method == "GET" and path in ("/openapi.json", "/docs", "/docs/oauth2-redirect"):
                    resp = JSONResponse(
                        {"openapi": "3.1.0", "info": {"title": "deep-research-mcp", "version": "1.0"}, "paths": {}}
                    )
                    await resp(scope, receive, send)
                    return
                # Проверяем, не идёт ли запрос на /openapi.json с другим префиксом
                # (OWUI иногда опрашивает /mcp/openapi.json)
                if method == "GET" and path.endswith("/openapi.json"):
                    resp = JSONResponse(
                        {"openapi": "3.1.0", "info": {"title": "deep-research-mcp", "version": "1.0"}, "paths": {}}
                    )
                    await resp(scope, receive, send)
                    return
            await self.inner(scope, receive, send)

    wrapped_app = _QuietOpenAPIMiddleware(app)

    uvicorn.run(
        wrapped_app,
        host=cfg.server.host,
        port=cfg.server.port,
        # КРИТИЧНО для Open WebUI: без этого 421 Misdirected Request
        # при Host: deep-research:8765 или другом внешнем имени.
        host_header_validation=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
