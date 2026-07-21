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
from .filter_policy import (
    FilterPolicy,
    host_from_url,
    make_policy,
    matches_domain,
    rank_score,
    should_drop,
)
from .intent import Intent, IntentMatch, detect_intent
from .llm_client import LLMClient, LLMFactory, LLMResponse, ToolCall
from .prompts import (
    EXTRACTOR_PROMPT,
    SOURCE_PLANNER_PROMPT,
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
    SourcePlan,
    tools_for_mode,
)
import json as _json
import re as _re

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
        query: str | None = None,
    ) -> None:
        self.config = config or get_config()
        self.bus = event_bus or EventBus()
        self._seen_urls: set[str] = set()
        self._all_sources: list[dict[str, str]] = []
        # Распознаём намерение ОДИН раз при инициализации — по тексту
        # запроса пользователя. Если детектор отключён или ничего не
        # нашёл — это None (нейтральный режим).
        self._intent_match: IntentMatch | None = self._detect(query)
        self._intent_emitted = False
        # План источников от LLM — заполняется в research() через
        # _plan_sources(). По умолчанию — general.
        self._source_plan: SourcePlan = SourcePlan.default()
        # Флаг: планирование уже выполнено
        self._plan_emitted = False

    def _detect(self, query: str | None) -> IntentMatch | None:
        if not query:
            return None
        limits = self.config.limits
        if not getattr(limits, "intent_detection_enabled", True):
            return None
        return detect_intent(query)

    @property
    def intent(self) -> IntentMatch | None:
        return self._intent_match

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
            LLMFactory(self.config.llm) as factory,
            SearXNGClient(self.config.searxng) as searxng,
            CrawlClient(self.config.limits) as crawler,
        ):
            planner = factory.planner()   # сильная модель — планирование и синтез
            worker = factory.worker()     # слабая модель — извлечение фактов

            # ── 0. Планирование источников (через planner-LLM) ──────
            # Один вызов LLM ДО старта цикла: выбираем SearXNG-категории
            # и стратегию реранкинга. При ошибке — fallback на дефолт.
            await self._plan_sources(input_data.query, planner)

            iterations_used = 0
            for i in range(max_iter):
                iterations_used = i + 1
                system_prompt = get_researcher_system_prompt(mode, i, max_iter)
                messages = [{"role": "system", "content": system_prompt}, *history]

                # 1. Запрос к planner-LLM с tools
                response = await planner.chat(messages, tools=tools, tool_choice="auto")
                assert isinstance(response, LLMResponse)
                tool_calls = response.tool_calls

                # 2. Обработка ответа
                history.append(self._assistant_message(response))

                # Если LLM не вызвал ни одного tool — завершаем
                if not tool_calls:
                    logger.info("LLM produced no tool calls on iteration %d — finishing", i)
                    break

                # 3. Выполнение tool calls (параллельно)
                tool_messages = await self._execute_tools(
                    tool_calls, searxng, crawler, worker
                )
                history.extend(tool_messages)

                # 4. Проверяем, вызвал ли LLM `done`
                if any(tc.name == "done" for tc in tool_calls):
                    break

            # 5. Синтез финального ответа — тоже через planner (сильная модель)
            answer = await self._synthesize(planner, input_data.query, history)
            await self.bus.emit_done(answer, self._all_sources)

        return ResearchOutput(
            answer=answer, sources=self._all_sources, iterations=iterations_used
        )

    # ─────────────────────────────────────────────────────────────────
    # Планирование источников (вызывается один раз в начале research)
    # ─────────────────────────────────────────────────────────────────
    async def _plan_sources(self, query: str, planner: LLMClient) -> None:
        """Один LLM-вызов: классифицирует запрос и подбирает SearXNG-категории.

        Результат сохраняется в ``self._source_plan`` и используется:
          • в ``_execute_one('web_search')`` — ``search_many(categories=...)``
          • в логике адаптивной политики реранкинга — мэппим
            ``plan.intent`` в один из наших intent-классов для ``make_policy``.

        При любой ошибке (LLM недоступен, битый JSON) — fallback на дефолт
        ``SourcePlan.default()``, чтобы пайплайн не падал.
        """
        if not query:
            return
        try:
            resp = await planner.chat(
                [
                    {"role": "system", "content": SOURCE_PLANNER_PROMPT},
                    {"role": "user", "content": query},
                ]
            )
            if not isinstance(resp, LLMResponse):
                return
            plan = self._parse_source_plan(resp.content)
            if plan is not None:
                self._source_plan = plan
                logger.info(
                    "source plan: intent=%s categories=%s rationale=%s",
                    plan.intent, plan.searxng_categories, plan.rationale,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_plan_sources failed, using default: %s", exc)
        finally:
            # Стримим в UI как plan-событие (один раз)
            if not self._plan_emitted:
                await self.bus.emit_plan(
                    f"source plan: {self._source_plan.intent} "
                    f"({', '.join(self._source_plan.searxng_categories)})"
                    + (f" — {self._source_plan.rationale}" if self._source_plan.rationale else "")
                )
                self._plan_emitted = True

    @staticmethod
    def _parse_source_plan(text: str) -> SourcePlan | None:
        """Грубый парсинг JSON из ответа LLM (даже если в ```json```)."""
        if not text:
            return None
        # Снять markdown-обёртку если есть
        fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
        candidate = fenced.group(1) if fenced else text.strip()
        try:
            obj = _json.loads(candidate)
        except Exception:  # noqa: BLE001
            # Попробуем вытащить первый JSON-объект из текста
            m = _re.search(r"\{.*\}", text, _re.DOTALL)
            if not m:
                return None
            try:
                obj = _json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
        # Валидация и нормализация
        intent = str(obj.get("intent", "general")).lower().strip()
        if intent not in {"social", "academic", "news", "videos", "general", "all"}:
            intent = "general"
        cats_raw = obj.get("searxng_categories") or []
        if not isinstance(cats_raw, list):
            cats_raw = []
        allowed_cats = {
            "general", "news", "science", "social", "videos",
            "images", "files", "music", "it", "map",
        }
        cats = [c for c in (str(x).strip().lower() for x in cats_raw) if c in allowed_cats]
        if not cats:
            cats = ["general"]
        return SourcePlan(
            intent=intent,
            searxng_categories=cats,
            rationale=str(obj.get("rationale", "")).strip()[:300],
            needs_social=bool(obj.get("needs_social", False)),
            needs_academic=bool(obj.get("needs_academic", False)),
            needs_news=bool(obj.get("needs_news", False)),
            needs_videos=bool(obj.get("needs_videos", False)),
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

            limits = self.config.limits
            per_query = limits.max_results_per_query
            # Прокидываем SearXNG-категории из LLM-плана (general/science/
            # social/news/videos/...). Дефолт из config если план не сделали.
            searxng_cats = (
                self._source_plan.searxng_categories
                or self.config.searxng.categories
            )
            results_by_q = await searxng.search_many(
                queries,
                max_results_per_query=per_query,
                categories=searxng_cats,
            )

            # ── Адаптивная политика реранкинга ────────────────────────
            # Приоритет источника намерения:
            #   1) LLM-план (_source_plan.intent) — если задал социальные/
            #      научные/новостные домены;
            #   2) keyword-детектор (_intent_match) — fallback;
            #   3) нейтральный режим — только ENV-приоритеты.
            plan_intent = self._source_plan.intent
            if plan_intent in ("social", "academic", "news", "all"):
                intent_name: Intent = plan_intent  # type: ignore[assignment]
            elif self._intent_match is not None:
                intent_name = self._intent_match.intent
            else:
                intent_name = "neutral"
            policy = make_policy(
                intent_name,
                social=limits.social_domains,
                academic=limits.academic_domains,
                news=limits.news_domains,
                neutral_priority=limits.priority_domains,
                blocked=limits.blocked_domains,
                min_score=limits.min_result_score,
                domain_boost_threshold=limits.domain_boost_threshold,
                top_k=limits.results_top_k_per_query,
            )

            if not self._intent_emitted:
                # Сообщим UI, какой режим реранкинга активен.
                payload: dict[str, Any] = {
                    "intent": intent_name,
                    "matched": (
                        self._intent_match.matched_keyword
                        if self._intent_match
                        else None
                    ),
                    "title": policy.title,
                    "priority": policy.priority[:5],
                }
                if self._intent_match:
                    payload["reason"] = (
                        f"распознано ключевое слово «{self._intent_match.matched_keyword}»"
                    )
                else:
                    payload["reason"] = "нейтральный режим (без активной подсказки)"
                self._intent_emitted = True
                logger.info("search policy: %s", policy.title)

            # Считаем частоту доменов по всем запросам, чтобы дать буст
            domain_hits: dict[str, int] = {}
            for q in queries:
                seen_q: set[str] = set()
                for r in results_by_q.get(q, []):
                    h = host_from_url(r.url)
                    if not h or h in seen_q:
                        continue
                    seen_q.add(h)
                    domain_hits[h] = domain_hits.get(h, 0) + 1

            merged: list[SearXNGResult] = []
            kept_per_query: dict[str, list[SearXNGResult]] = {}
            for q in queries:
                raw = results_by_q.get(q, [])
                # Применяем политику: фильтрация + реранкинг.
                kept: list[SearXNGResult] = []
                for r in raw:
                    drop, _reason = should_drop(
                        r.url,
                        float(r.score or 0.0),
                        policy,
                        self._seen_urls,
                    )
                    if drop:
                        continue
                    kept.append(r)

                # Сортируем по rank с учётом бустов политики.
                kept.sort(
                    key=lambda r: rank_score(r.url, r.score or 0.0, policy, domain_hits),
                    reverse=True,
                )
                top = kept[: policy.top_k]
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
            # (сколько отбросили + активная политика) — чтобы модель знала,
            # что выборка сокращена и могла переформулировать запрос.
            return {
                "type": "search_results",
                "filtered_out": dropped_by_query,
                "policy": {
                    "intent": intent_name,
                    "title": policy.title,
                    "priority_count": len(policy.priority),
                    "blocked_count": len(policy.blocked),
                },
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
