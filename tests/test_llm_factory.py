"""Тесты модельного роутинга (LLMFactory + SourcePlan parser)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from deep_research.config import LLMConfig
from deep_research.llm_client import LLMFactory, _resolve_client_config
from deep_research.researcher import Researcher
from deep_research.types import SourcePlan


# ─────────────────────────────────────────────────────────────────────
# LLMConfig: resolved_planner_model / resolved_worker_model / worker_endpoint
# ─────────────────────────────────────────────────────────────────────
def test_llmconfig_falls_back_to_base_model():
    """Если planner_model/worker_model не заданы — используется базовый model."""
    cfg = LLMConfig(model="base-7b", planner_model="", worker_model="")
    assert cfg.resolved_planner_model() == "base-7b"
    assert cfg.resolved_worker_model() == "base-7b"
    assert cfg.worker_endpoint() == (cfg.base_url, cfg.api_key)


def test_llmconfig_separates_planner_and_worker():
    cfg = LLMConfig(
        model="base-7b",
        planner_model="planner-14b",
        worker_model="worker-3b",
        worker_base_url="http://localhost:11435/v1",
        worker_api_key="wkey",
    )
    assert cfg.resolved_planner_model() == "planner-14b"
    assert cfg.resolved_worker_model() == "worker-3b"
    assert cfg.worker_endpoint() == ("http://localhost:11435/v1", "wkey")


def test_resolve_client_config_returns_distinct_configs():
    """_resolve_client_config подменяет model/base_url/api_key по роли."""
    cfg = LLMConfig(
        model="base-7b",
        planner_model="planner-14b",
        worker_model="worker-3b",
        worker_base_url="http://worker:9999/v1",
    )
    p = _resolve_client_config(cfg, "planner")
    w = _resolve_client_config(cfg, "worker")
    assert p.model == "planner-14b"
    assert p.base_url == cfg.base_url
    assert w.model == "worker-3b"
    assert w.base_url == "http://worker:9999/v1"


# ─────────────────────────────────────────────────────────────────────
# SourcePlan parser
# ─────────────────────────────────────────────────────────────────────
def test_parse_source_plan_strict_json():
    p = Researcher._parse_source_plan(
        '{"intent":"academic","searxng_categories":["science","general"],'
        '"rationale":"нужны научные источники","needs_social":false,'
        '"needs_academic":true,"needs_news":false,"needs_videos":false}'
    )
    assert p is not None
    assert p.intent == "academic"
    assert p.searxng_categories == ["science", "general"]
    assert p.needs_academic is True
    assert p.rationale == "нужны научные источники"


def test_parse_source_plan_strips_markdown_fence():
    text = (
        "```json\n"
        '{"intent":"videos","searxng_categories":["videos","general"],'
        '"rationale":"user wants video"}\n'
        "```"
    )
    p = Researcher._parse_source_plan(text)
    assert p is not None
    assert p.intent == "videos"
    assert p.searxng_categories == ["videos", "general"]


def test_parse_source_plan_recovers_from_extra_text():
    """Если LLM добавил поясняющий текст вокруг JSON — парсер всё равно достанет объект."""
    text = (
        "Okay, let me analyze:\n"
        '{"intent":"social","searxng_categories":["social"],'
        '"rationale":"user wants tweets"}\n'
        "Done."
    )
    p = Researcher._parse_source_plan(text)
    assert p is not None
    assert p.intent == "social"
    assert p.searxng_categories == ["social"]


def test_parse_source_plan_filters_invalid_categories():
    """Невалидные категории выкидываются; пустой список → ['general']."""
    p = Researcher._parse_source_plan(
        '{"intent":"general","searxng_categories":["bogus","videos","hack"],'
        '"rationale":""}'
    )
    assert p is not None
    assert p.searxng_categories == ["videos"]


def test_parse_source_plan_normalizes_invalid_intent():
    p = Researcher._parse_source_plan(
        '{"intent":"hacker","searxng_categories":["general"]}'
    )
    assert p is not None
    assert p.intent == "general"


def test_parse_source_plan_returns_none_on_garbage():
    assert Researcher._parse_source_plan("") is None
    assert Researcher._parse_source_plan("not json at all") is None


# ─────────────────────────────────────────────────────────────────────
# LLMFactory: planner/worker разделение при разных моделях
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_llm_factory_creates_distinct_clients_when_different():
    """Если planner и worker разные — открываются два независимых клиента."""
    cfg = LLMConfig(
        model="base-7b",
        planner_model="planner-14b",
        worker_model="worker-3b",
        # Разные endpoint'ы чтобы гарантировать distinct-клиентов
        worker_base_url="http://worker-host:11435/v1",
    )
    factory = LLMFactory(cfg)
    async with factory as f:
        p = f.planner()
        w = f.worker()
        assert p is not w
        assert p.config.model == "planner-14b"
        assert p.config.base_url == cfg.base_url
        assert w.config.model == "worker-3b"
        assert w.config.base_url == "http://worker-host:11435/v1"


@pytest.mark.asyncio
async def test_llm_factory_shares_client_when_identical():
    """Если planner и worker совпадают по (model, base_url, api_key) — один клиент."""
    cfg = LLMConfig(model="only-7b")
    factory = LLMFactory(cfg)
    async with factory as f:
        assert f.planner() is f.worker()


# ─────────────────────────────────────────────────────────────────────
# ENV-override для planner/worker моделей
# ─────────────────────────────────────────────────────────────────────
def test_env_overrides_planner_and_worker_models(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "base-7b")
    monkeypatch.setenv("LLM_PLANNER_MODEL", "planner-14b")
    monkeypatch.setenv("LLM_WORKER_MODEL", "worker-3b")
    monkeypatch.setenv("LLM_WORKER_BASE_URL", "http://worker:9999/v1")
    # Сбрасываем кеш конфига чтобы ENV перечитался
    import deep_research.config as cfgmod

    cfgmod._cached = None
    cfg = cfgmod.get_config().llm
    assert cfg.model == "base-7b"
    assert cfg.planner_model == "planner-14b"
    assert cfg.worker_model == "worker-3b"
    assert cfg.worker_base_url == "http://worker:9999/v1"
    # cleanup
    cfgmod._cached = None


# ─────────────────────────────────────────────────────────────────────
# SourcePlan.default()
# ─────────────────────────────────────────────────────────────────────
def test_source_plan_default():
    p = SourcePlan.default()
    assert p.intent == "general"
    assert p.searxng_categories == ["general"]
    assert p.needs_social is False
    assert p.needs_videos is False