"""Тесты для фильтрации и реранкинга результатов SearXNG в Researcher.

Проверяем:
  • блокировку по домену (BLOCKED_DOMAINS),
  • отсечение по MIN_RESULT_SCORE,
  • ограничение top-K на запрос (RESULTS_TOP_K_PER_QUERY),
  • доменный буст (DOMAIN_BOOST_THRESHOLD) и приоритеты (PRIORITY_DOMAINS),
  • дедупликацию по URL между запросами.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from deep_research.researcher import Researcher
from deep_research.streaming import EventBus
from deep_research.tools.searxng_client import SearXNGResult


def _r(url: str, *, score: float = 1.0, title: str = "", content: str = ""):
    return SearXNGResult(
        query="q",
        title=title or url,
        url=url,
        content=content,
        engine="google",
        score=score,
    )


@pytest.fixture
def base_env():
    """Ставит нейтральный ENV, чтобы фикстура не зависела от .env-файла разработчика."""
    saved = {}
    base = {
        "SEARXNG_URL": "http://localhost:1",
        "LLM_BASE_URL": "http://localhost:1",
        "LLM_API_KEY": "x",
        "LLM_MODEL": "test-model",
        "MAX_PARALLEL_CRAWLS": "1",
    }
    for k, v in base.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Сброс singleton-конфига после каждого теста
    from deep_research import config as cfg_mod

    cfg_mod._cached = None


def _make_researcher() -> Researcher:
    """Сбрасывает singleton-конфиг и возвращает свежий Researcher.

    Researcher.__init__ читает конфиг один раз, поэтому важно создавать
    его ТОЛЬКО после того, как test выставил все ENV-переменные.
    """
    from deep_research import config as cfg_mod

    cfg_mod._cached = None
    return Researcher(event_bus=EventBus())


def _patch_env(**kwargs) -> dict[str, str | None]:
    """Подменяет ENV и сбрасывает кеш конфига. Возвращает saved для restore."""
    saved = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    from deep_research import config as cfg_mod

    cfg_mod._cached = None
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    from deep_research import config as cfg_mod

    cfg_mod._cached = None


@pytest.mark.asyncio
async def test_blocked_domains_are_filtered_out(base_env):
    """facebook.com / vk.com должны быть отброшены."""
    saved = _patch_env(
        MIN_RESULT_SCORE=0,
        RESULTS_TOP_K_PER_QUERY=10,
        DOMAIN_BOOST_THRESHOLD=99,
        BLOCKED_DOMAINS="facebook.com,vk.com",
        PRIORITY_DOMAINS="",
    )
    try:
        r = _make_researcher()
        raw = {
            "q1": [
                _r("https://facebook.com/post/1", score=10.0),
                _r("https://vk.com/wall1", score=10.0),
                _r("https://hightech.fm/article/quantum", score=10.0),
                _r("https://naukaip.ru/paper/1", score=10.0),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            type("TC", (), {"name": "web_search", "arguments": {"queries": ["q1"]}})(),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        assert "https://facebook.com/post/1" not in urls
        assert "https://vk.com/wall1" not in urls
        assert "https://hightech.fm/article/quantum" in urls
        assert "https://naukaip.ru/paper/1" in urls
    finally:
        _restore_env(saved)


@pytest.mark.asyncio
async def test_min_score_threshold_drops_weak_results(base_env):
    saved = _patch_env(
        MIN_RESULT_SCORE=2.0,
        RESULTS_TOP_K_PER_QUERY=10,
        DOMAIN_BOOST_THRESHOLD=99,
        BLOCKED_DOMAINS="",
        PRIORITY_DOMAINS="",
    )
    try:
        r = _make_researcher()
        raw = {
            "q1": [
                _r("https://strong.com/a", score=5.0),
                _r("https://weak.com/a", score=0.5),
                _r("https://weak2.com/a", score=1.9),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            type("TC", (), {"name": "web_search", "arguments": {"queries": ["q1"]}})(),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        assert urls == ["https://strong.com/a"]
    finally:
        _restore_env(saved)


@pytest.mark.asyncio
async def test_top_k_per_query_is_enforced(base_env):
    saved = _patch_env(
        MIN_RESULT_SCORE=0,
        RESULTS_TOP_K_PER_QUERY=3,
        DOMAIN_BOOST_THRESHOLD=99,
        BLOCKED_DOMAINS="",
        PRIORITY_DOMAINS="",
    )
    try:
        r = _make_researcher()
        raw = {
            "q1": [
                _r(f"https://site{i}.com/a", score=float(i)) for i in range(10)
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            type("TC", (), {"name": "web_search", "arguments": {"queries": ["q1"]}})(),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        assert len(out["results"]) == 3
        urls = [x["url"] for x in out["results"]]
        assert "https://site9.com/a" in urls
        assert "https://site8.com/a" in urls
        assert "https://site7.com/a" in urls
    finally:
        _restore_env(saved)


@pytest.mark.asyncio
async def test_priority_domain_boosts_result(base_env):
    saved = _patch_env(
        MIN_RESULT_SCORE=0,
        RESULTS_TOP_K_PER_QUERY=5,
        DOMAIN_BOOST_THRESHOLD=99,
        BLOCKED_DOMAINS="",
        PRIORITY_DOMAINS="priority.com",
    )
    try:
        r = _make_researcher()
        raw = {
            "q1": [
                _r("https://priority.com/a", score=0.1),
                _r("https://other1.com/a", score=10.0),
                _r("https://other2.com/b", score=10.0),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            type("TC", (), {"name": "web_search", "arguments": {"queries": ["q1"]}})(),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        assert urls[0] == "https://priority.com/a"
    finally:
        _restore_env(saved)


@pytest.mark.asyncio
async def test_dedup_across_queries(base_env):
    """Один URL в двух запросах не должен войти дважды."""
    saved = _patch_env(
        MIN_RESULT_SCORE=0,
        RESULTS_TOP_K_PER_QUERY=10,
        DOMAIN_BOOST_THRESHOLD=99,
        BLOCKED_DOMAINS="",
        PRIORITY_DOMAINS="",
    )
    try:
        r = _make_researcher()
        dup = "https://dup.com/article"
        raw = {
            "q1": [_r(dup, score=1.0), _r("https://uniq1.com/a", score=1.0)],
            "q2": [_r(dup, score=1.0), _r("https://uniq2.com/a", score=1.0)],
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            type(
                "TC",
                (),
                {"name": "web_search", "arguments": {"queries": ["q1", "q2"]}},
            )(),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        assert urls.count(dup) == 1
    finally:
        _restore_env(saved)
