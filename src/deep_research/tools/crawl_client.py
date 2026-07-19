"""Обёртка вокруг Crawl4AI для парсинга страниц.

В проекте решено использовать Crawl4AI как библиотеку (без отдельного
сервера). Чтобы быть устойчивым к окружениям, где crawl4ai по каким-то
причинам не работает (например, нет Chromium), предусмотрен fallback
на простой httpx + базовую экстракцию текста.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import LimitsConfig

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    url: str
    title: str = ""
    content: str = ""  # markdown/plain text, ready для LLM
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title, "content": self.content}


class CrawlClient:
    """Параллельный crawler с семафором и таймаутом.

    Использует crawl4ai.AsyncWebCrawler, если доступен; иначе —
    fallback на httpx + простое извлечение article/main/p.
    """

    def __init__(self, limits: LimitsConfig | None = None) -> None:
        self.limits = limits or LimitsConfig()
        self._semaphore = asyncio.Semaphore(self.limits.max_parallel_crawls)
        self._use_crawl4ai: bool | None = None  # lazy-detect

    async def _try_import_crawl4ai(self) -> bool:
        if self._use_crawl4ai is not None:
            return self._use_crawl4ai
        try:
            import crawl4ai  # noqa: F401
            self._use_crawl4ai = True
            logger.info("crawl4ai detected — using as crawler backend")
        except Exception as exc:  # noqa: BLE001
            logger.warning("crawl4ai not available (%s) — fallback to httpx", exc)
            self._use_crawl4ai = False
        return self._use_crawl4ai

    async def crawl_one(self, url: str) -> CrawlResult:
        async with self._semaphore:
            try:
                if await self._try_import_crawl4ai():
                    return await self._crawl_with_crawl4ai(url)
                return await self._crawl_with_httpx(url)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Crawl failed for %s", url)
                return CrawlResult(
                    url=url, success=False, error=str(exc), content=f"[crawl error: {exc}]"
                )

    async def crawl_many(self, urls: list[str]) -> list[CrawlResult]:
        tasks = [self.crawl_one(u) for u in urls]
        return await asyncio.gather(*tasks)

    async def _crawl_with_crawl4ai(self, url: str) -> CrawlResult:
        from crawl4ai import AsyncWebCrawler  # type: ignore

        async with AsyncWebCrawler() as crawler:
            res = await asyncio.wait_for(
                crawler.arun(url=url),
                timeout=self.limits.crawl_timeout_sec,
            )
            markdown = getattr(res, "markdown", None) or ""
            title = ""
            meta = getattr(res, "metadata", None)
            if meta:
                title = meta.get("title", "") or meta.get("og:title", "") or ""
            content = markdown if isinstance(markdown, str) else str(markdown)
            return CrawlResult(url=url, title=title, content=content.strip())

    async def _crawl_with_httpx(self, url: str) -> CrawlResult:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.limits.crawl_timeout_sec,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        title = _extract_title(html)
        content = _html_to_text(html)
        return CrawlResult(url=url, title=title, content=content)


# ─────────────────────────────────────────────────────────────────────
# Простые утилиты для fallback-парсинга
# ─────────────────────────────────────────────────────────────────────
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style.*?</style>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\s+")
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
_BLOCK_RE = re.compile(r"</?(p|br|div|li|h\d|tr)[^>]*>", re.IGNORECASE)

# Используем chr(34) и chr(38) и т.п. чтобы текстовый движок не съел кавычки
_Q = chr(34)  # "
_AMP = chr(38)  # &
_LT = chr(60)  # <
_GT = chr(62)  # >
_NBSP = _AMP + "nbsp;"
_AMP_ENTITY = _AMP + "amp;"
_LT_ENTITY = _AMP + "lt;"
_GT_ENTITY = _AMP + "gt;"
_QUOT_ENTITY = _AMP + "quot;"
_APOS_ENTITY = _AMP + "#39;"


def _extract_title(html: str) -> str:
    m = _TITLE_RE.search(html)
    if not m:
        return ""
    return _BLANK_RE.sub(" ", m.group(1)).strip()


def _html_to_text(html: str) -> str:
    """Грубая конвертация HTML в текст без внешних зависимостей."""
    html = _SCRIPT_RE.sub(" ", html)
    html = _STYLE_RE.sub(" ", html)
    article_match = _ARTICLE_RE.search(html)
    main_match = _MAIN_RE.search(html)
    if article_match:
        text = article_match.group(1)
    elif main_match:
        text = main_match.group(1)
    else:
        text = html
    text = _BLOCK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.replace(_NBSP, " ")
    text = text.replace(_AMP_ENTITY, _AMP)
    text = text.replace(_LT_ENTITY, _LT)
    text = text.replace(_GT_ENTITY, _GT)
    text = text.replace(_QUOT_ENTITY, _Q)
    text = text.replace(_APOS_ENTITY, "'")
    lines = [_BLANK_RE.sub(" ", line).strip() for line in text.splitlines()]
    out = "\n".join(line for line in lines if line)
    return out[:50_000]
