"""Детектор намерений пользователя по доменам/источникам поиска.

Идея: пользователь пишет в свободной форме, например:
  • «поищи в социальных сетях реакцию на X»
  • «нужно научное подтверждение / факт-чек»
  • «ищи по статьям в СМИ»
  • «ищи вообще всё / включи все источники»
  • «просто поищи в интернете»  (нейтральный)

Мы пытаемся распознать одно из 4 намерений через ключевые слова и
передать его в фильтр Researcher'а. Если ничего не подошло — возвращаем
``None`` и работает дефолтная нейтральная схема.

Намеренно простая эвристика (без LLM) — чтобы решение было мгновенным
и детерминированным. Расширяется через .env (`INTENT_KEYWORDS_SOCIAL=…`).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Типы намерений
# ─────────────────────────────────────────────────────────────────────
Intent = Literal["social", "academic", "news", "all", "neutral"]


# ─────────────────────────────────────────────────────────────────────
# Дефолтные ключевые слова (RU + EN)
# Можно переопределить через ENV (через запятую)
# ─────────────────────────────────────────────────────────────────────
_DEFAULT_KEYWORDS: dict[Intent, list[str]] = {
    "social": [
        # RU
        "соцсет", "соц сет", "соц-се", "в соц", "в социальн",
        "социальных сетях", "в твиттер", "в тви", "в вк", "вконтакте",
        "vk", "вк", "телеграм", "telegram", "reddit", "реддит",
        # EN
        "social media", "social network", "twitter", "tweet",
        "facebook", "instagram", "tiktok", "vkontakte", "vk.com",
        "x.com", "reddit post", "subreddit", "linkedin",
    ],
    "academic": [
        # RU
        "научн", "наук", "факт-чек", "фактчек", "проверь факт",
        "рецензируем", "рецензированный источник", "статья из журнала",
        "опубликован", "pubmed", "pub med", "исследовани",
        # EN
        "scientific", "peer-reviewed", "peer reviewed", "scholarly",
        "fact check", "fact-check", "verify", "arxiv", "doi",
        "research paper", "study", "journal", "academia",
        ".edu", "scholar.google",
    ],
    "news": [
        # RU
        "новост", "сми", "пресс", "репортаж", "статьи в сми",
        # EN
        "news article", "press", "news outlet", "newspaper",
        "headline", "breaking",
    ],
    "all": [
        # RU
        "включи все", "подключ все", "ищи все", "ищи всё", "везде",
        "без ограничений", "без фильтров", "по всем источникам",
        "по всему интернету", "по всем сайтам", "по всем ресурсам",
        # EN
        "include all", "search everywhere", "no filter", "no filters",
        "everything", "all sources", "comprehensive search",
    ],
}


def _split_env(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


# Кешируем ключевые слова с учётом ENV-override.
_KEYWORDS_CACHE: dict[Intent, list[str]] | None = None


def _keywords() -> dict[Intent, list[str]]:
    global _KEYWORDS_CACHE
    if _KEYWORDS_CACHE is not None:
        return _KEYWORDS_CACHE
    out: dict[Intent, list[str]] = {}
    for intent, defaults in _DEFAULT_KEYWORDS.items():
        env = _split_env(f"INTENT_KEYWORDS_{intent.upper()}")
        out[intent] = [w.lower() for w in (env or defaults)]
    _KEYWORDS_CACHE = out
    return out


# ─────────────────────────────────────────────────────────────────────
# Публичная утилита
# ─────────────────────────────────────────────────────────────────────
@dataclass
class IntentMatch:
    intent: Intent
    matched_keyword: str


def detect_intent(text: str) -> IntentMatch | None:
    """Распознаёт намерение пользователя по тексту запроса.

    Возвращает ``IntentMatch`` или ``None``, если ни одно ключевое слово
    не сработало (нейтральный режим).
    """
    if not text:
        return None
    haystack = f" {text.lower()} "

    # Приоритет проверки: all → social → academic → news → neutral.
    # ``all`` — самый сильный сигнал, важно отдать ему приоритет,
    # иначе «включи все + соцсети» ошибочно зажмёт только соцсети.
    order: tuple[Intent, ...] = ("all", "social", "academic", "news")
    kw = _keywords()
    for intent in order:
        for word in kw[intent]:
            # Слово ищем как подстроку, но границы слов важны для коротких
            # терминов вроде "vk" — используем простую проверку через
            # границы непробельных символов.
            if _word_in(haystack, word):
                return IntentMatch(intent=intent, matched_keyword=word)
    return None


def _word_in(haystack: str, needle: str) -> bool:
    """Подстрочный поиск с защитой от ложных срабатываний на коротких терминах.

    • Для коротких терминов (<=3 символов) — требуем границу слова,
      иначе «vk» сматчится с «avk», «invk» и т.п.
    • Для длинных — простой ``in``.
    """
    if len(needle) <= 3:
        pattern = r"(?<![a-zа-яё0-9])" + re.escape(needle) + r"(?![a-zа-яё0-9])"
        return re.search(pattern, haystack, flags=re.IGNORECASE | re.UNICODE) is not None
    return needle in haystack
