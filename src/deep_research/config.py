"""Конфигурация MCP-сервера.

Все параметры читаются из переменных окружения (.env поддерживается).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class SearXNGConfig:
    url: str = field(default_factory=lambda: os.getenv("SEARXNG_URL", "http://searxng:8080"))
    categories: list[str] = field(default_factory=lambda: _get_list("SEARXNG_CATEGORIES", ["general"]))
    language: str = field(default_factory=lambda: os.getenv("SEARXNG_LANGUAGE", "ru"))
    engines: list[str] = field(default_factory=lambda: _get_list("SEARXNG_ENGINES", []))
    safesearch: int = field(default_factory=lambda: _get_int("SEARXNG_SAFESEARCH", 0))
    timeout: float = 30.0


@dataclass
class LLMConfig:
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "ollama"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "qwen2.5:7b"))


@dataclass
class EmbeddingConfig:
    base_url: str = field(default_factory=lambda: os.getenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1"))
    api_key: str = field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY", os.getenv("LLM_API_KEY", "ollama")))
    model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "nomic-embed-text"))


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: os.getenv("MCP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _get_int("MCP_PORT", 8765))


@dataclass
class LimitsConfig:
    """Лимиты на итерации и параллельность (как в Vane)."""
    max_iterations_speed: int = field(default_factory=lambda: _get_int("MAX_ITERATIONS_SPEED", 2))
    max_iterations_balanced: int = field(default_factory=lambda: _get_int("MAX_ITERATIONS_BALANCED", 6))
    max_iterations_quality: int = field(default_factory=lambda: _get_int("MAX_ITERATIONS_QUALITY", 25))
    max_parallel_crawls: int = field(default_factory=lambda: _get_int("MAX_PARALLEL_CRAWLS", 5))
    max_results_per_query: int = field(default_factory=lambda: _get_int("MAX_RESULTS_PER_QUERY", 10))
    crawl_timeout_sec: int = field(default_factory=lambda: _get_int("CRAWL_TIMEOUT_SEC", 60))

    # ── Реранкинг выдачи SearXNG ────────────────────────────────────
    # Базовая фильтрация/реранкинг на основе score и доменной частоты.
    # Никаких жёстких блокировок по доменам по умолчанию — пользователь
    # сам решает, какие источники важны, через ENV или через явное
    # указание в запросе («ищи в социальных сетях», «научное подтверждение»).
    #
    # • MIN_RESULT_SCORE=0.0       — отключено по умолчанию; выдача
    #   не режется по score, если явно не задать порог.
    # • RESULTS_TOP_K_PER_QUERY=10 — после реранкинга оставляем 10 URL
    #   на запрос (вместе с MAX_RESULTS_PER_QUERY определяет выборку).
    # • DOMAIN_BOOST_THRESHOLD=2   — если один домен встретился по ≥2
    #   разным запросам в одной итерации — он узнаваемый «эксперт».
    #
    # Домены ниже — опциональные ENV-настройки. По умолчанию всё пусто:
    # мы не хотим навязывать пользователю «правильный» список источников.
    # Если нужно поднять в топ любимый сайт — задайте PRIORITY_DOMAINS;
    # если нужно что-то отсечь — задайте BLOCKED_DOMAINS. Всё сугубо opt-in.
    min_result_score: float = field(
        default_factory=lambda: float(os.getenv("MIN_RESULT_SCORE", "0.0"))
    )
    results_top_k_per_query: int = field(
        default_factory=lambda: _get_int("RESULTS_TOP_K_PER_QUERY", 10)
    )
    domain_boost_threshold: int = field(
        default_factory=lambda: _get_int("DOMAIN_BOOST_THRESHOLD", 2)
    )
    # Категория «всегда блокировать» — пусто по умолчанию. Чтобы включить,
    # задайте BLOCKED_DOMAINS=facebook.com,vk.com в .env.
    blocked_domains: list[str] = field(
        default_factory=lambda: _get_list("BLOCKED_DOMAINS", [])
    )
    # «Поднять в топ» для нейтрального режима — тоже пусто по умолчанию.
    priority_domains: list[str] = field(
        default_factory=lambda: _get_list("PRIORITY_DOMAINS", [])
    )

    # ── Мягкие приоритеты для адаптивных режимов ───────────────────
    # Эти словари используются только когда пользователь явно попросил
    # конкретный тип источника в запросе («ищи в соцсетях», «научный
    # факт-чек»). Они НЕ применяются к дефолтному нейтральному поиску.
    # Никаких блокировок: только поднять нужный домен в топ через реранкинг.
    social_domains: list[str] = field(
        default_factory=lambda: _get_list("SOCIAL_DOMAINS", [])
    )
    academic_domains: list[str] = field(
        default_factory=lambda: _get_list("ACADEMIC_DOMAINS", [])
    )
    news_domains: list[str] = field(
        default_factory=lambda: _get_list("NEWS_DOMAINS", [])
    )

    # ── Детектор намерений ──────────────────────────────────────────
    # Каждый пользовательский ключ-слово можно переопределить через ENV
    # (см. intent.py). Если хотите отключить намерения — задайте
    # INTENT_DETECTION=false.
    intent_detection_enabled: bool = field(
        default_factory=lambda: os.getenv("INTENT_DETECTION", "true").lower()
        in ("1", "true", "yes", "on")
    )


@dataclass
class Config:
    searxng: SearXNGConfig = field(default_factory=SearXNGConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)


# Глобальный инстанс — простой singleton через функцию
_cached: Config | None = None


def get_config() -> Config:
    global _cached
    if _cached is None:
        _cached = Config()
    return _cached


def max_iterations_for(mode: str) -> int:
    limits = get_config().limits
    if mode == "speed":
        return limits.max_iterations_speed
    if mode == "balanced":
        return limits.max_iterations_balanced
    return limits.max_iterations_quality  # quality (по умолчанию)
