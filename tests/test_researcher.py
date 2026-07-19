"""Тесты Researcher — с моками SearXNG/Crawl/LLM."""
import asyncio
import json
from typing import Any

import pytest

from deep_research.llm_client import LLMResponse, ToolCall
from deep_research.researcher import Researcher
from deep_research.streaming import EventBus
from deep_research.tools.crawl_client import CrawlResult
from deep_research.tools.searxng_client import SearXNGResult
from deep_research.types import ResearchInput


class _FakeSearXNG:
    def __init__(self, results_per_query):
        self._r = results_per_query

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def search_many(self, queries, *, max_results_per_query=None):
        out = {}
        for q in queries:
            out[q] = list(self._r.get(q, []))
        return out


class _FakeCrawler:
    def __init__(self, results):
        self._r = results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def crawl_many(self, urls):
        return [self._r.get(u) or CrawlResult(url=u, success=False, error="not found") for u in urls]


class _FakeLLM:
    """Эмулирует поведение LLM: выдаёт заранее заготовленные tool calls."""

    def __init__(self, scripts: list[list[ToolCall]]):
        self._scripts = scripts
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def chat(self, messages, *, tools=None, stream=False, **kw):
        # Фаза синтеза: последний вызов со stream=True отдает стрим ответа
        if stream:
            async def _gen():
                # Сначала куски текста, потом tool_calls=[] как маркер конца
                for piece in ["syn", "the", "sis ", "answer"]:
                    yield piece
            return _gen()

        if self._idx >= len(self._scripts):
            return LLMResponse(content="done", tool_calls=[])
        calls = self._scripts[self._idx]
        self._idx += 1
        return LLMResponse(content="", tool_calls=calls)

    async def generate_text(self, prompt, *, system=None):
        return "synth answer"


def _plan_tc(plan="Plan"): return ToolCall(id="p1", name="__reasoning_preamble", arguments={"plan": plan})
def _search_tc(queries): return ToolCall(id="s1", name="web_search", arguments={"queries": queries})
def _scrape_tc(urls): return ToolCall(id="r1", name="scrape_url", arguments={"urls": urls})
def _done_tc(): return ToolCall(id="d1", name="done", arguments={})


@pytest.mark.asyncio
async def test_researcher_runs_full_cycle(monkeypatch):
    """Проверяем, что Researcher проходит цикл: plan → search → scrape → done → synthesis."""

    searxng_results = {
        "renewable energy 2025": [
            SearXNGResult(
                query="renewable energy 2025",
                title="Big report",
                url="https://search-result.example/page",  # другой URL, чтобы не dedup-ился со scrape
                content="snippet text",
            )
        ]
    }
    crawler_results = {
        "https://example.com/report": CrawlResult(
            url="https://example.com/report",
            title="Big report",
            content="Some long content " * 100,
        )
    }

    # Подменяем clients на fakes
    from deep_research import researcher as rmod

    monkeypatch.setattr(rmod, "SearXNGClient", lambda *_a, **_kw: _FakeSearXNG(searxng_results))
    # URL в search results не должен матчиться с URL в scrape (разные домены) —
    # иначе тест ловит дедуп по _seen_urls.
    monkeypatch.setattr(rmod, "CrawlClient", lambda *_a, **_kw: _FakeCrawler(crawler_results))
    monkeypatch.setattr(rmod, "LLMClient", lambda *_a, **_kw: _FakeLLM([
        [_plan_tc("Ищу отчёт"), _search_tc(["renewable energy 2025"])],
        [_plan_tc("Читаю источник"), _scrape_tc(["https://example.com/report"])],
        [_plan_tc("Готово"), _done_tc()],
    ]))

    bus = EventBus()
    received = []

    async def consumer():
        async for ev in bus.stream():
            received.append(ev)
            if ev.type in ("done", "error"):
                break

    consumer_task = asyncio.create_task(consumer())

    r = Researcher(event_bus=bus)
    out = await r.research(ResearchInput(query="What's new in renewable energy in 2025?", mode="balanced"))

    await asyncio.sleep(0.05)
    await bus.close()
    await asyncio.wait_for(consumer_task, timeout=1.0)

    assert out.answer == "synthesis answer"
    assert len(out.sources) == 1
    assert out.sources[0]["url"] == "https://example.com/report"
    types = [e.type for e in received]
    # Должны быть: plan, search_start, search_result, plan, read_start, read_done, synthesis_chunk*, done
    assert "plan" in types
    assert "search_start" in types
    assert "read_start" in types
    assert "done" in types
