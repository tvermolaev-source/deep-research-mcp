"""Шина событий для стриминга прогресса исследования в UI.

В Vane это session.emitBlock/updateBlock (JSON-patch). Здесь —
простая in-memory pub/sub-шина с двумя вариантами подписки:
  • asyncio.Queue — для MCP-стрима (SSE)
  • callback     — для тестов и не-MCP вызовов

События отправляются как dataclass-совместимые dict-структуры,
чтобы Open WebUI и любой MCP-клиент мог отображать steps в UI.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Callable


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ResearchEvent:
    """Базовое событие стрима."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "data": self.data}, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────
# Конкретные события — соответствуют SubStep'ам из Vane
# ─────────────────────────────────────────────────────────────────────
@dataclass
class PlanEvent:
    """Reasoning preamble — короткий план в свободной форме."""
    text: str
    iteration: int = 0

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="plan",
            data={"id": _new_id(), "text": self.text, "iteration": self.iteration},
        )


@dataclass
class SearchStartEvent:
    queries: list[str]

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="search_start",
            data={"id": _new_id(), "queries": self.queries},
        )


@dataclass
class SearchResultEvent:
    results: list[dict[str, str]]

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="search_result",
            data={"id": _new_id(), "results": self.results},
        )


@dataclass
class ReadStartEvent:
    urls: list[str]

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="read_start",
            data={"id": _new_id(), "urls": self.urls},
        )


@dataclass
class ReadDoneEvent:
    """Один URL прочитан и из него вытащены факты."""
    url: str
    title: str
    facts: str

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="read_done",
            data={"id": _new_id(), "url": self.url, "title": self.title, "facts": self.facts},
        )


@dataclass
class SynthesisChunkEvent:
    """Чанк финального ответа (стримится по мере генерации)."""
    text: str

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(type="synthesis_chunk", data={"text": self.text})


@dataclass
class DoneEvent:
    """Исследование завершено. Содержит полный ответ и список источников."""
    answer: str
    sources: list[dict[str, str]]

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(
            type="done",
            data={"answer": self.answer, "sources": self.sources},
        )


@dataclass
class ErrorEvent:
    message: str

    def to_event(self) -> ResearchEvent:
        return ResearchEvent(type="error", data={"message": self.message})


# ─────────────────────────────────────────────────────────────────────
# EventBus
# ─────────────────────────────────────────────────────────────────────
class EventBus:
    """In-memory pub/sub шина событий исследования.

    Каждая сессия получает свой EventBus; подписчики (MCP-клиент через
    SSE, тестовый код, логгер) читают события как AsyncIterator.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[ResearchEvent | None]] = []
        self._closed = False

    def subscribe(self) -> asyncio.Queue[ResearchEvent | None]:
        q: asyncio.Queue[ResearchEvent | None] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    async def publish(self, event: ResearchEvent) -> None:
        if self._closed:
            return
        # Snapshot, чтобы подписчики могли отписываться во время итерации
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Если клиент не успевает — дропаем, чтобы не блокировать
                pass

    async def stream(self) -> AsyncIterator[ResearchEvent]:
        q = self.subscribe()
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    async def close(self) -> None:
        self._closed = True
        for q in list(self._subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

    # Удобные обёртки для типичных событий
    async def emit_plan(self, text: str, iteration: int = 0) -> None:
        await self.publish(PlanEvent(text=text, iteration=iteration).to_event())

    async def emit_search_start(self, queries: list[str]) -> None:
        await self.publish(SearchStartEvent(queries=queries).to_event())

    async def emit_search_results(self, results: list[dict[str, str]]) -> None:
        await self.publish(SearchResultEvent(results=results).to_event())

    async def emit_read_start(self, urls: list[str]) -> None:
        await self.publish(ReadStartEvent(urls=urls).to_event())

    async def emit_read_done(self, url: str, title: str, facts: str) -> None:
        await self.publish(ReadDoneEvent(url=url, title=title, facts=facts).to_event())

    async def emit_synthesis_chunk(self, text: str) -> None:
        await self.publish(SynthesisChunkEvent(text=text).to_event())

    async def emit_done(self, answer: str, sources: list[dict[str, str]]) -> None:
        await self.publish(DoneEvent(answer=answer, sources=sources).to_event())

    async def emit_error(self, message: str) -> None:
        await self.publish(ErrorEvent(message=message).to_event())
