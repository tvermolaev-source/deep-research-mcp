"""OpenAI-совместимый LLM-клиент с поддержкой streaming + tool calls.

Совместим с любым endpoint, поддерживающим OpenAI Chat Completions API:
  • Ollama (через /v1)
  • Open WebUI proxy
  • OpenAI/Azure/local LLama.cpp etc.

Используется асинхронный httpx, без openai-пакета (минимум зависимостей).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Один tool call от LLM (аналог Vane ToolCall)."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Не-стриминговый ответ LLM: content + tool calls."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "LLMClient":
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=60.0),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ─────────────────────────────────────────────────────────────────
    # Chat API
    # ─────────────────────────────────────────────────────────────────
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> LLMResponse | AsyncIterator[str | ToolCall]:
        """Стриминговый или обычный запрос к Chat Completions API.

        Если stream=True — возвращает AsyncIterator, элементы которого
        могут быть строками (text chunk) или ToolCall (по мере сборки).
        Иначе возвращает LLMResponse с финальным content + tool_calls.
        """
        assert self._client is not None, "Use LLMClient as async context manager"

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        if max_tokens:
            payload["max_tokens"] = max_tokens

        if stream:
            return self._stream_chat(payload)
        return await self._blocking_chat(payload)

    async def _blocking_chat(self, payload: dict[str, Any]) -> LLMResponse:
        resp = await self._client.post("/chat/completions", json=payload)  # type: ignore[union-attr]
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        raw_calls = message.get("tool_calls") or []
        calls: list[ToolCall] = []
        for c in raw_calls:
            try:
                args = json.loads(c.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(
                    id=c.get("id", ""),
                    name=(c.get("function") or {}).get("name", ""),
                    arguments=args,
                )
            )
        return LLMResponse(
            content=content, tool_calls=calls, finish_reason=choice.get("finish_reason", "")
        )

    async def _stream_chat(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[str | ToolCall]:
        """Стрим SSE-style: отдаёт чанки текста и ToolCall'ы.

        ToolCall отдаётся постепенно (аргументы достраиваются), но мы
        отдаём его только когда стрим по этому call'у завершён
        (finish_reason == 'tool_calls'). Для нашего Researcher'а этого
        достаточно: он ждёт полного списка tool_calls за итерацию.
        """
        # Сборщик аргументов по index/id
        buffers: dict[int, dict[str, Any]] = {}
        final_calls: list[ToolCall] = []

        async with self._client.stream(  # type: ignore[union-attr]
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}

                # Текстовый chunk
                chunk_text = delta.get("content") or ""
                if chunk_text:
                    yield chunk_text

                # Tool-call chunks
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    buf = buffers.setdefault(
                        idx,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        buf["name"] = fn["name"]
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]
                    # Когда стрим "закрывает" вызов — отдаём ToolCall
                    if choice.get("finish_reason"):
                        try:
                            args = json.loads(buf["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        final_calls.append(
                            ToolCall(id=buf["id"], name=buf["name"], arguments=args)
                        )

        # Отдаём собранные tool calls в самом конце стрима
        for tc in final_calls:
            yield tc

    # ─────────────────────────────────────────────────────────────────
    # Удобные обёртки
    # ─────────────────────────────────────────────────────────────────
    async def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self.chat(messages)
        return resp.content if isinstance(resp, LLMResponse) else ""
