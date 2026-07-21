"""Типы данных — портированы из Vane (lib/agents/search/types.ts, lib/types.ts)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


# ─────────────────────────────────────────────────────────────────────
# Источники поиска и режимы
# ─────────────────────────────────────────────────────────────────────
SearchSources = Literal["web", "news", "academic", "social"]
SearchMode = Literal["speed", "balanced", "quality"]


# ─────────────────────────────────────────────────────────────────────
# SubSteps — то, что мы стримим в UI как "task steps"
# (соответствует Vane: ReasoningResearchBlock | SearchingResearchBlock |
#  SearchResultsResearchBlock | ReadingResearchBlock)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ReasoningStep:
    id: str
    type: Literal["reasoning"] = "reasoning"
    reasoning: str = ""


@dataclass
class SearchingStep:
    id: str
    type: Literal["searching"] = "searching"
    queries: list[str] = field(default_factory=list)


@dataclass
class SearchResultItem:
    """Один результат поиска (snippet), как в Vane Chunk.metadata."""
    title: str
    url: str
    content: str = ""


@dataclass
class SearchResultsStep:
    id: str
    type: Literal["search_results"] = "search_results"
    results: list[SearchResultItem] = field(default_factory=list)


@dataclass
class ReadingStep:
    id: str
    type: Literal["reading"] = "reading"
    sources: list[SearchResultItem] = field(default_factory=list)


@dataclass
class SynthesisStep:
    """Финальный шаг — синтез ответа (стримится чанками)."""
    id: str
    type: Literal["synthesis"] = "synthesis"
    answer: str = ""


SubStep = Union[ReasoningStep, SearchingStep, SearchResultsStep, ReadingStep, SynthesisStep]


# ─────────────────────────────────────────────────────────────────────
# Конфиг запуска одной сессии исследования
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ResearchInput:
    query: str
    mode: SearchMode = "balanced"
    sources: list[SearchSources] = field(default_factory=lambda: ["web"])
    chat_history: list[dict[str, str]] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# SourcePlan — результат LLM-планирования источников (см. _plan_sources)
# ─────────────────────────────────────────────────────────────────────
@dataclass
class SourcePlan:
    """План источников, построенный planner-LLM до старта цикла.

    • ``intent`` — основной тип источника (``social/academic/news/videos/general/all``)
    • ``searxng_categories`` — категории, передаваемые в SearXNGClient
    • ``rationale`` — короткое пояснение (стримится в UI)
    • ``needs_*`` — флаги, можно использовать в логике (например,
      увеличить лимит итераций для quality если нужны видео)
    """
    intent: str = "general"
    searxng_categories: list[str] = field(default_factory=lambda: ["general"])
    rationale: str = ""
    needs_social: bool = False
    needs_academic: bool = False
    needs_news: bool = False
    needs_videos: bool = False

    @classmethod
    def default(cls) -> "SourcePlan":
        return cls(
            intent="general",
            searxng_categories=["general"],
            rationale="default plan",
        )


# ─────────────────────────────────────────────────────────────────────
# Tool-схемы для LLM (OpenAI-compatible function calling)
# ─────────────────────────────────────────────────────────────────────
TOOL_PLAN = {
    "type": "function",
    "function": {
        "name": "__reasoning_preamble",
        "description": (
            "Use this FIRST on every turn to state your plan in natural language "
            "before any other action. Keep it short, action-focused."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": (
                        "A concise natural-language plan in one short paragraph. "
                        "Open with a short intent phrase (e.g., 'Okay, the user wants to...')."
                    ),
                }
            },
            "required": ["plan"],
        },
    },
}

TOOL_WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Perform web searches via SearXNG. Up to 3 queries per call. "
            "Use SEO-friendly keywords, not full sentences."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 3 search queries (keywords, not sentences).",
                }
            },
            "required": ["queries"],
        },
    },
}

TOOL_SCRAPE_URL = {
    "type": "function",
    "function": {
        "name": "scrape_url",
        "description": (
            "Scrape and extract content from up to 3 URLs via Crawl4AI. "
            "Only call when the user explicitly asks to read specific pages."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A list of URLs to scrape.",
                }
            },
            "required": ["urls"],
        },
    },
}

TOOL_DONE = {
    "type": "function",
    "function": {
        "name": "done",
        "description": (
            "Call ONLY when research is complete and you are ready to provide the final answer."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def tools_for_mode(mode: SearchMode, sources: list[SearchSources]) -> list[dict[str, Any]]:
    """Возвращает список инструментов для LLM в зависимости от режима и источников.

    Соответствует Vane ActionRegistry.getAvailableActionTools.
    """
    tools: list[dict[str, Any]] = []
    if mode != "speed":
        tools.append(TOOL_PLAN)
    if "web" in sources:
        tools.append(TOOL_WEB_SEARCH)
    tools.append(TOOL_SCRAPE_URL)
    tools.append(TOOL_DONE)
    return tools
