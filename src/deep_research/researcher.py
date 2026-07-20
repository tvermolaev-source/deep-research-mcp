"""Researcher — главный цикл глубокого поиска.

Логика портирована с Vane (researcher/index.ts + actions/*):
  • итеративный цикл tool-call'ов через LLM
  • tool `web_search` → SearXNGClient
  • tool `scrape_url` → CrawlClient
  • tool `done` → синтез финального ответа
  • стрим событий через EventBus

Отличия от Vane:
  • Язык: Python + asyncio (вместо TypeScript + streams)
  • Транспорт для UI: SSE / MCP (вместо JSON-patch через session.emitBlock)
  • LLM endpoint: OpenAI-compatible (Ollama / Open WebUI / etc.)
  • Crawler: Crawl4AI (crawl4ai.AsyncWebCrawler) с fallback на httpx
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import Config, get_config, max_iterations_for
from .llm_client import LLMClient, LLMResponse, ToolCall
from .prompts import (
    EXTRACTOR_PROMPT,
    SYNTHESIS_PROMPT,
    USER_PROMPT_TEMPLATE,
    get_researcher_system_prompt,
)
from .streaming import EventBus
from .tools import CrawlClient, CrawlResult, SearXNGClient, SearXNGResult
from .types import (
    ResearchInput,
    SearchMode,
    SearchResultItem,
    tools_for_mode,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Результат исследования
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ResearchOutput:
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    iterations: int = 0


# ─────────────────────────────────────────────────────────────────────
# Researcher
# ─────────────────────────────────────────────────────────────────────
class Researcher:
    """Итеративный глубокий поиск, порт Vane Researcher на Python."""

    def __init__(
        self,
        config: Config | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.config = config or get_config()
        self.bus = event_bus or EventBus()
        self._seen_urls: set[str] = set()
        self._all_sources: list[dict[str, str]] = []

    # ─────────────────────────────────────────────────────────────────
    # Главный метод
    # ─────────────────────────────────────────────────────────────────
    async def research(self, input_data: ResearchInput) -> ResearchOutput:
        mode = input_data.mode
        max_iter = max_iterations_for(mode)
        tools = tools_for_mode(mode, input_data.sources)

        # Сообщения для истории LLM-агента
        history: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": self._build_user_message(input_data),
            }
        ]

        async with (
            LLMClient(self.config.llm) as llm,
            SearXNGClient(self.config.searxng) as searxng,
            CrawlClient(self.config.limits) as crawler,
        ):
            iterations_used = 0
            for i in range(max_iter):
                iterations_used = i + 1
                system_prompt = get_researcher_system_prompt(mode, i, max_iter)
                messages = [{"role": "system", "content": system_prompt}, *history]

                # 1. Запрос к LLM с tools
                response = await llm.chat(messages, tools=tools, tool_choice="auto")
                assert isinstance(response, LLMResponse)
                tool_calls = response.tool_calls

                # 2. Обработка ответа
                history.append(self._assistant_message(response))

                # Если LLM не вызвал ни одного tool — завершаем
                if not tool_calls:
                    logger.info("LLM produced no tool calls on iteration %d — finishing", i)
                    break

                # 3. Выполнение tool calls (параллельно)
                tool_messages = await self._execute_tools(tool_calls, searxng, crawler, llm)
                history.extend(tool_messages)

                # 4. Проверяем, вызвал ли LLM `done`
                if any(tc.name == "done" for tc in tool_calls):
                    break

            # 5. Синтез финального ответа
            answer = await self._synthesize(llm, input_data.query, history)
            await self.bus.emit_done(answer, self._all_sources)

        return ResearchOutput(
            answer=answer, sources=self._all_sources, iterations=iterations_used
        )

    # ─────────────────────────────────────────────────────────────────
    # Строители сообщений
    # ─────────────────────────────────────────────────────────────────
    def _build_user_message(self, input_data: ResearchInput) -> str:
        chat_history_str = ""
        if input_data.chat_history:
            last_msgs = input_data.chat_history[-10:]
            lines = []
            for m in last_msgs:
                role = m.get("role", "user")
                content = m.get("content", "")
                lines.append(f"{role}: {content}")
            chat_history_str = "\n".join(lines)

        return (
            f"<conversation>\n{chat_history_str}\n"
            f"User: {input_data.query}\n</conversation>"
        )

    def _assistant_message(self, response: LLMResponse) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": _json_dumps(tc.arguments),
                    },
                }
                for tc in response.tool_calls
            ]
        return msg

    # ─────────────────────────────────────────────────────────────────
    # Исполнение tool calls
    # ─────────────────────────────────────────────────────────────────
    async def _execute_tools(
        self,
        tool_calls: list[ToolCall],
        searxng: SearXNGClient,
        crawler: CrawlClient,
        llm: LLMClient,
    ) -> list[dict[str, Any]]:
        tasks = [self._execute_one(tc, searxng, crawler, llm) for tc in tool_calls]
        results = await asyncio.gather(*tasks)
        # Превращаем (tc, result) в tool-сообщения для LLM
        out: list[dict[str, Any]] = []
        for tc, res in zip(tool_calls, results):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": _json_dumps(res),
                }
            )
        return out

    async def _execute_one(
        self,
        tc: ToolCall,
        searxng: SearXNGClient,
        crawler: CrawlClient,
        llm: LLMClient,
    ) -> dict[str, Any]:
        name = tc.name
        args = tc.arguments or {}

        if name == "__reasoning_preamble":
            plan = args.get("plan", "").strip()
            await self.bus.emit_plan(plan)
            return {"type": "reasoning", "plan": plan}

        if name == "web_search":
            queries = args.get("queries") or []
            if isinstance(queries, str):
                queries = [queries]
            # В Vane до 3 запросов за вызов
            queries = queries[:3]
            await self.bus.emit_search_start(queries)
            per_query = self.config.limits.max_results_per_query
            results_by_q = await searxng.search_many(queries, max_results_per_query=per_query)

            # ── Нормализация + фильтрация выдачи SearXNG ───────────────
            # 1) выбрасываем мусор по домену (соцсети, PDF-фолдеры и т.п.)
            # 2) отсекаем результаты ниже MIN_RESULT_SCORE
            # 3) считаем по доменам, сколько раз они всплыли по разным
            #    запросам; если >= DOMAIN_BOOST_THRESHOLD — даём доменный буст.
            # 4) сортируем и режем верх RESULTS_TOP_K_PER_QUERY на запрос.
            limits = self.config.limits
            blocked = [d.lower() for d in limits.blocked_domains]
            priority = [d.lower() for d in limits.priority_domains]

            def _domain(url: str) -> str:
                try:
                    from urllib.parse import urlparse
                    host = urlparse(url).hostname or ""
                    # выкидываем www. для группировки
                    if host.startswith("www."):
                        host = host[4:]
                    return host.lower()
                except Exception:
                    return ""

            # Считаем частоту доменов по всем запросам, чтобы дать буст
            domain_hits: dict[str, int] = {}
            for q in queries:
                seen_q: set[str] = set()
                for r in results_by_q.get(q, []):
                    d = _domain(r.url)
                    if not d or d in seen_q:
                        continue
                    seen_q.add(d)
                    domain_hits[d] = domain_hits.get(d, 0) + 1

            def _rank(r: SearXNGResult) -> float:
                score = float(r.score or 0.0)
                d = _domain(r.url)
                # Бонус если домен приоритетный
                if any(d.endswith(p) for p in priority):
                    score += 50.0
                # Бонус если домен часто появляется по разным запросам
                hits = domain_hits.get(d, 0)
                if hits >= limits.domain_boost_threshold:
                    score += 10.0 * hits
                # Небольшой бонус за совпадение контента с оригинальным запросом
                return score

            merged: list[SearXNGResult] = []
            kept_per_query: dict[str, list[SearXNGResult]] = {}
            for q in queries:
                raw = results_by_q.get(q, [])
                # 1) выбрасываем уже виденные URL — это наша глобальная защита
                # 2) выбрасываем по доменам-блэклисту
                # 3) выбрасываем по min_result_score
                filtered: list[SearXNGResult] = []
                for r in raw:
                    if not r.url or r.url in self._seen_urls:
                        continue
                    d = _domain(r.url)
                    if any(d.endswith(b) for b in blocked):
                        continue
                    if float(r.score or 0.0) < limits.min_result_score:
                        continue
                    filtered.append(r)
                # сортируем по rank и оставляем top-K
                filtered.sort(key=_rank, reverse=True)
                top = filtered[: limits.results_top_k_per_query]
                kept_per_query[q] = top
                for r in top:
                    self._seen_urls.add(r.url)
                    merged.append(r)

            dropped_by_query = {
                q: len(results_by_q.get(q, [])) - len(kept_per_query.get(q, []))
                for q in queries
            }

            await self.bus.emit_search_results(
                [{"title": r.title, "url": r.url, "content": r.content} for r in merged]
            )

            # Возвращаем LLM: что вернули после фильтрации + метаданные
            # (сколько отбросили) — чтобы модель знала, что выборка сокращена
            # и при необходимости могла переформулировать запрос.
            return {
                "type": "search_results",
                "filtered_out": dropped_by_query,
                "min_result_score": limits.min_result_score,
                "results": [
                    {"title": r.title, "url": r.url, "content": r.content}
                    for r in merged
                ],
            }

        if name == "scrape_url":
            urls = args.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            urls = urls[:3]
            # Не дублируем уже прочитанные
            urls = [u for u in urls if u not in self._seen_urls]
            await self.bus.emit_read_start(urls)
            results = await crawler.crawl_many(urls)

            facts_by_url: dict[str, str] = {}
            for res in results:
                if not res.success or not res.content:
                    facts_by_url[res.url] = f"[failed to scrape: {res.error or 'no content'}]"
                    continue
                # Извлечение фактов через LLM (chunked)
                facts = await self._extract_facts(llm, res.content, urls)
                facts_by_url[res.url] = facts
                await self.bus.emit_read_done(res.url, res.title, facts[:500])
            # Регистрируем источники (даже если scrape failed — для трассировки)
            for res in results:
                self._all_sources.append(
                    {
                        "title": res.title or res.url,
                        "url": res.url,
                        "content": facts_by_url.get(res.url, ""),
                    }
                )

            return {
                "type": "reading",
                "results": [
                    {"url": u, "facts": facts_by_url.get(u, "")} for u in urls
                ],
            }

        if name == "done":
            return {"type": "done"}

        return {"type": "error", "message": f"Unknown tool: {name}"}

    # ─────────────────────────────────────────────────────────────────
    # Извлечение фактов из скрапленного контента (как в Vane extractor)
    # ─────────────────────────────────────────────────────────────────
    async def _extract_facts(
        self, llm: LLMClient, content: str, queries: list[str]
    ) -> str:
        """Разбивает длинный контент на чанки и извлекает факты через LLM."""
        chunks = _split_text(content, max_chars=4000, overlap=200)
        if len(chunks) <= 1:
            # Без экстракции — отдаём как есть, если короткий
            return content

        async def _one(chunk: str) -> str:
            try:
                prompt = (
                    f"<queries>{', '.join(queries) or 'Summarize'}</queries>\n"
                    f"<scraped_data>{chunk}</scraped_data>"
                )
                resp = await llm.chat(
                    [
                        {"role": "system", "content": EXTRACTOR_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                if isinstance(resp, LLMResponse):
                    # Пытаемся вытащить JSON-объект
                    return _extract_json_field(resp.content, "extracted_facts") or chunk
            except Exception as exc:  # noqa: BLE001
                logger.warning("extract_facts failed: %s", exc)
            return chunk

        extracted = await asyncio.gather(*[_one(c) for c in chunks])
        return "\n".join(e for e in extracted if e).strip()

    # ─────────────────────────────────────────────────────────────────
    # Синтез финального ответа
    # ─────────────────────────────────────────────────────────────────
    async def _synthesize(
        self, llm: LLMClient, query: str, history: list[dict[str, Any]]
    ) -> str:
        """Собирает все найденные факты и просит LLM написать финальный ответ."""
        facts = self._collect_facts(history)
        sources_block = "\n".join(
            f"[{i+1}] {s['title']} — {s['url']}" for i, s in enumerate(self._all_sources)
        ) or "(no sources)"

        user_msg = USER_PROMPT_TEMPLATE.format(
            query=query, facts=facts, sources=sources_block
        )

        # Стримим в bus и одновременно накапливаем
        chunks: list[str] = []
        stream = await llm.chat(
            [
                {"role": "system", "content": SYNTHESIS_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            stream=True,
        )
        async for chunk in stream:
            if isinstance(chunk, str):
                chunks.append(chunk)
                await self.bus.emit_synthesis_chunk(chunk)
        return "".join(chunks).strip()

    def _collect_facts(self, history: list[dict[str, Any]]) -> str:
        """Собирает все facts из reading-результатов в одну строку."""
        out: list[str] = []
        for msg in history:
            if msg.get("role") != "tool":
                continue
            try:
                import json

                payload = json.loads(msg.get("content") or "{}")
            except Exception:  # noqa: BLE001
                continue
            if payload.get("type") == "reading":
                for r in payload.get("results", []):
                    out.append(f"--- {r.get('url', '')} ---\n{r.get('facts', '')}")
        return "\n\n".join(out) or "(no facts extracted)"


# ─────────────────────────────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────────────────────────────
def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _split_text(text: str, *, max_chars: int = 4000, overlap: int = 200) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _extract_json_field(text: str, field_name: str) -> str | None:
    """Грубо вытаскивает поле из JSON-ответа LLM (даже если обёрнут в ```json```)."""
    import json
    import re

    # Убираем ```json ... ``` если есть
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        obj = json.loads(candidate)
        val = obj.get(field_name)
        if isinstance(val, str):
            return val
    except Exception:  # noqa: BLE001
        pass
    # Fallback: regex
    m = re.search(rf'"{field_name}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if m:
        return m.group(1).encode().decode("unicode_escape")
    return None
