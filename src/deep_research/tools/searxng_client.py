"""Асинхронный клиент SearXNG.

SearXNG — метапоисковый движок, отдаёт JSON по адресу
<base>/search?q=<...>&format=json.

Совместим с конфигом Vane (`searxng` папка в репо):
  https://github.com/ItzCrazyKns/Vane/tree/main/searxng
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import SearXNGConfig

logger = logging.getLogger(__name__)


@dataclass
class SearXNGResult:
    query: str
    title: str
    url: str
    content: str = ""
    engine: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "content": self.content}


class SearXNGClient:
    """HTTP-клиент SearXNG с поддержкой retries и категорий/языков."""

    def __init__(self, config: SearXNGConfig | None = None) -> None:
        self.config = config or SearXNGConfig()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SearXNGClient":
        self._client = httpx.AsyncClient(
            base_url=self.config.url,
            timeout=self.config.timeout,
            headers={"Accept": "application/json", "User-Agent": "deep-research-mcp/0.1"},
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        categories: list[str] | None = None,
    ) -> list[SearXNGResult]:
        """Один поисковый запрос.

        Возвращает список SearXNGResult, отсортированный по позиции в выдаче.

        Если передан ``categories`` — используется он (например,
        ``["science"]`` для научных статей, ``["social"]`` для соцсетей,
        ``["videos"]`` для YouTube). Иначе — ``self.config.categories``
        (дефолт из конфига).
        """
        assert self._client is not None, "SearXNGClient must be used as async context manager"

        cats = categories if categories is not None else self.config.categories
        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": self.config.language,
            "safesearch": self.config.safesearch,
            "categories": ",".join(cats) if cats else "",
        }
        if self.config.engines:
            params["engines"] = ",".join(self.config.engines)

        # SearXNG иногда отдаёт 429 при частых запросах — простой retry
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.get("/search", params=params)
                if resp.status_code == 429:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("SearXNG attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(0.5 * (attempt + 1))
        else:
            raise RuntimeError(f"SearXNG failed after retries: {last_exc}")

        results_raw: list[dict[str, Any]] = data.get("results", [])
        limit = max_results or 10
        out: list[SearXNGResult] = []
        for r in results_raw[:limit]:
            out.append(
                SearXNGResult(
                    query=query,
                    title=r.get("title", "").strip(),
                    url=r.get("url", "").strip(),
                    content=r.get("content", "").strip(),
                    engine=r.get("engine", ""),
                    score=float(r.get("score", 0.0)),
                )
            )
        return out

    async def search_many(
        self,
        queries: list[str],
        *,
        max_results_per_query: int | None = None,
        categories: list[str] | None = None,
    ) -> dict[str, list[SearXNGResult]]:
        """Запускает несколько запросов параллельно.

        Если передан ``categories`` — все запросы идут в этих категориях
        (например, ``["science"]`` для факт-чека, ``["videos"]`` для
        YouTube). Иначе — дефолтные из конфига.
        """
        tasks = [
            self.search(q, max_results=max_results_per_query, categories=categories)
            for q in queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, list[SearXNGResult]] = {}
        for q, r in zip(queries, results):
            if isinstance(r, Exception):
                logger.error("SearXNG query '%s' failed: %s", q, r)
                out[q] = []
            else:
                out[q] = r
        return out
