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
