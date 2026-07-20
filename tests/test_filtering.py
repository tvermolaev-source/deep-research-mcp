"""Тесты для новой адаптивной логики фильтрации и реранкинга.

Проверяем:
  • ``intent.detect_intent`` — детектор ключевых слов (RU + EN);
  • ``filter_policy.make_policy`` — корректная сборка политики под каждый intent;
  • ``filter_policy.matches_domain`` — суффиксный и TLD-матчинг (``vk.com``,
    ``m.vk.com``, ``mit.edu`` для шаблона ``.edu``);
  • Интеграция с Researcher: ``web_search`` учитывает политику, не блокирует
    ничего по умолчанию, но поднимает нужный домен в топ при intent.

По умолчанию (нейтральный режим) не должно быть автоблоков — никакие домены
не выкидываются «из коробки».
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from deep_research.filter_policy import (
    FilterPolicy,
    host_from_url,
    make_policy,
    matches_domain,
    rank_score,
    should_drop,
)
from deep_research.intent import detect_intent
from deep_research.researcher import Researcher
from deep_research.streaming import EventBus
from deep_research.tools.searxng_client import SearXNGResult


# ─────────────────────────────────────────────────────────────────────
# Утилиты для тестов
# ─────────────────────────────────────────────────────────────────────
def _r(url: str, *, score: float = 1.0, title: str = "", content: str = ""):
    return SearXNGResult(
        query="q",
        title=title or url,
        url=url,
        content=content,
        engine="google",
        score=score,
    )


@pytest.fixture(autouse=True)
def _clean_singletons(monkeypatch):
    """Сбрасываем кеш конфига и кеш ключевых слов перед/после каждого теста."""
    from deep_research import config as cfg_mod
    from deep_research import intent

    monkeypatch.setattr(cfg_mod, "_cached", None)
    monkeypatch.setattr(intent, "_KEYWORDS_CACHE", None)
    # Подменяем ключевые переменные на безопасный дефолт.
    monkeypatch.setenv("INTENT_DETECTION", "true")
    yield
    cfg_mod._cached = None
    if hasattr(intent, "_KEYWORDS_CACHE"):
        intent._KEYWORDS_CACHE = None


def _make_researcher(*, query: str | None = None) -> Researcher:
    return Researcher(event_bus=EventBus(), query=query)


def _patch_env(monkeypatch, **kwargs):
    for k, v in kwargs.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))


def _make_tc(name: str, arguments: dict):
    return type("TC", (), {"name": name, "arguments": arguments})()


# ─────────────────────────────────────────────────────────────────────
# Детектор намерений
# ─────────────────────────────────────────────────────────────────────
class TestIntentDetector:
    @pytest.mark.parametrize(
        "text",
        [
            "Поищи в социальных сетях реакцию на новость",
            "что пишут в твиттере про X",
            "in twitter reactions to Y",
            "reddit thread please",
            "в вк обсуждение",
        ],
    )
    def test_social_detected(self, text):
        m = detect_intent(text)
        assert m is not None
        assert m.intent == "social"

    @pytest.mark.parametrize(
        "text",
        [
            "нужно научное подтверждение этой гипотезы",
            "fact-check this claim",
            "есть ли peer-reviewed источник?",
            "arxiv paper about quantum",
            ".edu research on LLM",
            "научная статья про вакцины",
        ],
    )
    def test_academic_detected(self, text):
        m = detect_intent(text)
        assert m is not None
        assert m.intent == "academic"

    @pytest.mark.parametrize(
        "text",
        [
            "включи все источники",
            "ищи всё подряд",
            "search everywhere, no filter",
            "comprehensive search please",
        ],
    )
    def test_all_detected(self, text):
        m = detect_intent(text)
        assert m is not None
        assert m.intent == "all"

    @pytest.mark.parametrize(
        "text",
        [
            "что такое квантовый компьютер",
            "история Рима",
            "как приготовить борщ",
            "hello world",
        ],
    )
    def test_neutral_when_no_keywords(self, text):
        # Нейтральный ⇒ детектор возвращает None, никаких авто-политик.
        m = detect_intent(text)
        assert m is None

    def test_all_has_priority_over_social(self):
        """'включи все в соцсетях' → intent=all (сильнейшая инструкция)."""
        m = detect_intent("включи все источники, в том числе из социальных сетей")
        assert m is not None
        assert m.intent == "all"

    def test_short_keyword_uses_word_boundary(self):
        """'vk' как короткий термин не должен матчиться с 'avk'."""
        assert detect_intent("встретил avk") is None
        m = detect_intent("зайди в vk")
        assert m is not None
        assert m.intent == "social"

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("INTENT_DETECTION", "false")
        # Для Researcher._detect берётся из конфига.
        m = detect_intent("social media please")
        assert m is not None  # сам detect_intent не реагирует на ENV;
        # Researcher._detect — да. Это проверяется ниже в интеграционном тесте.


# ─────────────────────────────────────────────────────────────────────
# Подбор политики
# ─────────────────────────────────────────────────────────────────────
class TestMakePolicy:
    def test_neutral_policy_is_empty_by_default(self):
        p = make_policy(
            "neutral",
            social=(),
            academic=(),
            news=(),
            neutral_priority=(),
            blocked=(),
            min_score=0.0,
            domain_boost_threshold=2,
            top_k=10,
        )
        assert p.intent == "neutral"
        assert p.priority == []
        assert p.soft_priority == []
        assert p.blocked == []
        assert p.min_score == 0.0

    def test_social_policy_puts_social_in_priority(self):
        p = make_policy(
            "social",
            social=("vk.com", "twitter.com"),
            academic=(),
            news=(),
            neutral_priority=(),
            blocked=(),
            min_score=0.0,
            domain_boost_threshold=2,
            top_k=10,
        )
        assert "vk.com" in p.priority
        # blocked должен быть пустым — мы не блокируем «остальные» источники.
        assert p.blocked == []

    def test_academic_policy_boosts_academic_but_no_auto_block(self):
        p = make_policy(
            "academic",
            social=("vk.com", "twitter.com"),
            academic=("arxiv.org", "nature.com"),
            news=(),
            neutral_priority=(),
            blocked=(),
            min_score=0.0,
            domain_boost_threshold=2,
            top_k=10,
        )
        assert "arxiv.org" in p.priority
        # Без явного BLOCKED_DOMAINS ничего не блокируем.
        assert p.blocked == []
        assert "vk.com" not in p.priority

    def test_explicit_blocked_only_applied_when_set(self):
        p = make_policy(
            "all",
            social=(),
            academic=(),
            news=(),
            neutral_priority=(),
            blocked=("spam.example",),
            min_score=0.0,
            domain_boost_threshold=2,
            top_k=10,
        )
        # В режиме ``all`` мы не блокируем ничего, даже явно заданное
        # — пользователь сказал «ищи всё».
        assert p.blocked == []
        assert p.min_score == 0.0
        assert p.domain_boost_threshold == 999

    def test_respects_user_blocked_in_neutral(self):
        p = make_policy(
            "neutral",
            social=(),
            academic=(),
            news=(),
            neutral_priority=(),
            blocked=("tracker.example",),
            min_score=0.0,
            domain_boost_threshold=2,
            top_k=10,
        )
        assert "tracker.example" in p.blocked


# ─────────────────────────────────────────────────────────────────────
# Применение политики
# ─────────────────────────────────────────────────────────────────────
class TestMatchesDomain:
    @pytest.mark.parametrize(
        "host,patterns,expected",
        [
            ("vk.com", ["vk.com"], True),
            ("m.vk.com", ["vk.com"], True),
            ("vk.com", ["twitter.com"], False),
            ("mit.edu", [".edu"], True),
            ("cs.mit.edu", [".edu"], True),
            ("foo.edu.ru", [".edu"], False),  # ".edu" матчит только суффикс TLD
            ("hse.ru", ["hse.ru"], True),
            ("", ["vk.com"], False),
        ],
    )
    def test_various(self, host, patterns, expected):
        assert matches_domain(host, patterns) is expected


class TestShouldDrop:
    def test_neutral_drops_nothing_by_default(self):
        p = FilterPolicy(intent="neutral")
        for url in (
            "https://vk.com/wall1",
            "https://facebook.com/post",
            "https://twitter.com/x",
            "https://reddit.com/r/all",
            "https://t.me/channel/123",
            "https://example.com/article",
        ):
            drop, _ = should_drop(url, 1.0, p, set())
            assert drop is False, url

    def test_drops_when_explicit_blocked(self):
        p = FilterPolicy(intent="neutral", blocked=["spam.example"])
        drop, _ = should_drop("https://spam.example/x", 1.0, p, set())
        assert drop is True

    def test_drops_seen_urls(self):
        p = FilterPolicy(intent="neutral")
        drop, _ = should_drop("https://vk.com/x", 1.0, p, {"https://vk.com/x"})
        assert drop is True

    def test_drops_low_score_when_threshold_set(self):
        p = FilterPolicy(intent="neutral", min_score=2.0)
        drop, _ = should_drop("https://vk.com/x", 1.0, p, set())
        assert drop is True

    def test_min_score_zero_disables_filtering(self):
        p = FilterPolicy(intent="neutral", min_score=0.0)
        for s in (0.0, 0.1, 5.0, 100.0):
            drop, _ = should_drop("https://vk.com/x", s, p, set())
            assert drop is False


class TestRankScore:
    def test_priority_domain_gets_huge_boost(self):
        p = FilterPolicy(intent="social", priority=["vk.com"])
        domain_hits: dict[str, int] = {}
        s = rank_score("https://vk.com/x", 1.0, p, domain_hits)
        other = rank_score("https://other.com/x", 1.0, p, domain_hits)
        assert s > other + 50  # 100-балльный приоритет

    def test_no_priority_means_no_boost(self):
        p = FilterPolicy(intent="neutral")
        domain_hits: dict[str, int] = {}
        a = rank_score("https://vk.com/x", 1.0, p, domain_hits)
        b = rank_score("https://other.com/x", 1.0, p, domain_hits)
        assert a == b  # обе равны 1.0 — никаких блоков и бустов

    def test_soft_priority_adds_moderate_boost(self):
        p = FilterPolicy(intent="all", soft_priority=["example.com"])
        domain_hits: dict[str, int] = {}
        soft = rank_score("https://example.com/x", 5.0, p, domain_hits)
        none = rank_score("https://other.com/x", 5.0, p, domain_hits)
        # 20 баллов буста
        assert soft - none == 20.0


# ─────────────────────────────────────────────────────────────────────
# Интеграция с Researcher
# ─────────────────────────────────────────────────────────────────────
class TestResearcherIntegration:
    @pytest.mark.asyncio
    async def test_neutral_does_not_drop_social_by_default(self):
        """Без явных настроек соцсети НЕ выкидываются."""
        r = _make_researcher(query="просто поищи в интернете про ML")
        assert r.intent is None  # нейтральный запрос

        raw = {
            "q1": [
                _r("https://facebook.com/post/1", score=2.0),
                _r("https://vk.com/wall1", score=2.0),
                _r("https://twitter.com/x/1", score=2.0),
                _r("https://reddit.com/r/all/1", score=2.0),
                _r("https://example.com/article", score=1.0),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            _make_tc("web_search", {"queries": ["q1"]}),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        # Ничего из соцсетей не должно быть отрезано, если этого не просил юзер.
        assert "https://facebook.com/post/1" in urls
        assert "https://vk.com/wall1" in urls
        assert "https://twitter.com/x/1" in urls
        assert "https://reddit.com/r/all/1" in urls
        assert out["policy"]["intent"] == "neutral"
        assert out["policy"]["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_social_intent_boosts_social_first(self, monkeypatch):
        """Если запрос про соцсети и SOCIAL_DOMAINS задан — приоритет в топе."""
        _patch_env(
            monkeypatch,
            SOCIAL_DOMAINS="vk.com,twitter.com",
            RESULTS_TOP_K_PER_QUERY=3,
        )
        r = _make_researcher(query="Поищи в социальных сетях что пишут про X")
        assert r.intent is not None and r.intent.intent == "social"

        raw = {
            "q1": [
                _r("https://other1.com/a", score=5.0),
                _r("https://other2.com/b", score=5.0),
                _r("https://vk.com/wall1", score=0.5),
                _r("https://twitter.com/post1", score=0.5),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)

        out = await r._execute_one(
            _make_tc("web_search", {"queries": ["q1"]}),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        # Top-3 должны включать оба соцсетевых домена — несмотря на низкий score.
        assert "https://vk.com/wall1" in urls
        assert "https://twitter.com/post1" in urls
        # И они должны быть выше в списке, чем «обычные» с большим score.
        assert urls.index("https://vk.com/wall1") < urls.index("https://other1.com/a")
        assert out["policy"]["intent"] == "social"
        assert out["policy"]["priority_count"] == 2

    @pytest.mark.asyncio
    async def test_academic_intent_does_not_drop_social_if_no_blocklist(
        self, monkeypatch
    ):
        """В академическом режиме без BLOCKED_DOMAINS соцсети остаются."""
        _patch_env(
            monkeypatch,
            ACADEMIC_DOMAINS="arxiv.org,nature.com",
            NEWS_DOMAINS="ria.ru,tass.ru",
            BLOCKED_DOMAINS="",  # явно пусто
        )
        r = _make_researcher(
            query="нужно научное подтверждение этой гипотезы — peer-reviewed источник"
        )
        assert r.intent is not None and r.intent.intent == "academic"

        raw = {
            "q1": [
                _r("https://arxiv.org/abs/2401.01234", score=1.0),
                _r("https://nature.com/articles/x", score=1.0),
                _r("https://vk.com/wall1", score=1.0),
                _r("https://twitter.com/x", score=1.0),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)
        out = await r._execute_one(
            _make_tc("web_search", {"queries": ["q1"]}),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        # arxiv/nature — priority, должны быть выше.
        assert urls.index("https://arxiv.org/abs/2401.01234") < urls.index(
            "https://vk.com/wall1"
        )
        # Никто не заблокирован — пользователь не задал BLOCKED_DOMAINS.
        assert "https://vk.com/wall1" in urls
        assert "https://twitter.com/x" in urls
        assert out["policy"]["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_intent_all_clears_all_filters(self, monkeypatch):
        _patch_env(
            monkeypatch,
            SOCIAL_DOMAINS="vk.com",
            ACADEMIC_DOMAINS="arxiv.org",
            BLOCKED_DOMAINS="some-tracker.example",
            DOMAIN_BOOST_THRESHOLD=2,
        )
        r = _make_researcher(
            query="включи все источники, без фильтров, ищи везде"
        )
        assert r.intent is not None and r.intent.intent == "all"
        raw = {
            "q1": [
                _r("https://vk.com/wall1", score=0.1),
                _r("https://arxiv.org/abs/1", score=0.1),
                _r("https://some-tracker.example/x", score=0.1),
                _r("https://other.com/y", score=0.1),
            ]
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)
        out = await r._execute_one(
            _make_tc("web_search", {"queries": ["q1"]}),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        # В режиме «всё» ничего не блокируется и ничего не бустится.
        assert "https://some-tracker.example/x" in urls
        assert "https://vk.com/wall1" in urls
        assert "https://arxiv.org/abs/1" in urls
        assert out["policy"]["intent"] == "all"
        assert out["policy"]["blocked_count"] == 0
        assert out["policy"]["priority_count"] == 0

    @pytest.mark.asyncio
    async def test_dedup_across_queries(self):
        r = _make_researcher(query="обычный запрос")
        dup = "https://dup.com/article"
        raw = {
            "q1": [_r(dup, score=1.0), _r("https://uniq1.com/a", score=1.0)],
            "q2": [_r(dup, score=1.0), _r("https://uniq2.com/a", score=1.0)],
        }
        searxng = AsyncMock()
        searxng.search_many = AsyncMock(return_value=raw)
        out = await r._execute_one(
            _make_tc("web_search", {"queries": ["q1", "q2"]}),
            searxng,
            crawler=AsyncMock(),
            llm=AsyncMock(),
        )
        urls = [x["url"] for x in out["results"]]
        assert urls.count(dup) == 1

    @pytest.mark.asyncio
    async def test_disable_intent_detection_keeps_neutral(self, monkeypatch):
        monkeypatch.setenv("INTENT_DETECTION", "false")
        r = _make_researcher(query="поищи в социальных сетях про X")
        # Детектор выключен — даже при явном ключе-словe intent=None.
        assert r.intent is None


# ─────────────────────────────────────────────────────────────────────
# host_from_url
# ─────────────────────────────────────────────────────────────────────
class TestHostFromUrl:
    def test_strips_www(self):
        assert host_from_url("https://www.example.com/path") == "example.com"

    def test_lowercases(self):
        assert host_from_url("https://VK.COM/x") == "vk.com"

    def test_handles_no_host(self):
        assert host_from_url("not a url") == ""
