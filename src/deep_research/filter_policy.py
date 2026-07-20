"""Политики фильтрации/реранкинга результатов SearXNG под разные намерения.

Идея: пользователь сам решает, какие источники ему важны. По умолчанию
мы **ничего не блокируем и никого не приоритизируем** — берём всё, что
вернул SearXNG, и аккуратно реранкируем по score + частотности домена.

Если пользователь явно попросил в запросе конкретный тип источника
(«ищи в соцсетях», «научный факт-чек», «только статьи СМИ»), или
задал «ищи всё/включи все» — мы переключаемся на адаптивную политику,
которая мягко поднимает нужный тип доменов в топ. Блокировок по
префиксам типов источников нет.

Списки доменов — задаются через .env (см. config.py):
  • SOCIAL_DOMAINS   — соцсети;
  • ACADEMIC_DOMAINS — научные/академические;
  • NEWS_DOMAINS     — СМИ;
  • PRIORITY_DOMAINS — что поднимать в нейтральном режиме;
  • BLOCKED_DOMAINS  — что отсекать (по умолчанию пусто).

По умолчанию все эти переменные пустые, и Researcher ведёт себя
нейтрально. Если хочется готового набора — укажите свой; если наборы
подсказывать не нужно — оставляйте пусто.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .intent import Intent


# ─────────────────────────────────────────────────────────────────────
# Политика
# ─────────────────────────────────────────────────────────────────────
@dataclass
class FilterPolicy:
    """Политика фильтрации для одного режима запроса.

    • ``intent``           — какой режим активен (для логов);
    • ``priority``         — домены, получающие мощный буст при реранкинге;
    • ``soft_priority``    — домены, получающие умеренный буст;
    • ``blocked``          — домены, которые выбрасываются из выдачи;
                             обычно пусто (мы не блокируем по умолчанию);
    • ``min_score``        — порог отсечения по SearXNG-score (None = не фильтровать);
    • ``domain_boost_threshold`` — после скольки разных запросов давать буст;
    • ``top_k``            — сколько URL оставить на запрос.
    """

    intent: Intent
    priority: list[str] = field(default_factory=list)
    soft_priority: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    min_score: float | None = 0.0
    domain_boost_threshold: int = 2
    top_k: int = 10

    @property
    def title(self) -> str:
        return {
            "social": "приоритет на социальные сети",
            "academic": "приоритет на научные/академические источники",
            "news": "приоритет на СМИ",
            "all": "без фильтров (все источники)",
            "neutral": "нейтральный поиск",
        }.get(self.intent, self.intent)


# ─────────────────────────────────────────────────────────────────────
# Билдер политик из конфигурации
# ─────────────────────────────────────────────────────────────────────
def make_policy(
    intent: Intent,
    *,
    social: Iterable[str],
    academic: Iterable[str],
    news: Iterable[str],
    neutral_priority: Iterable[str],
    blocked: Iterable[str],
    min_score: float,
    domain_boost_threshold: int,
    top_k: int,
) -> FilterPolicy:
    """Собирает политику для конкретного намерения из доменов конфига.

    ВАЖНО: ``blocked`` применяется **только если явно задан** в конфиге.
    Мы не навязываем стоп-лист по типам источников — никакой auto-блок
    соцсетей, никакого auto-отсечения других категорий.
    """
    social_set = [d.lower() for d in social if d]
    academic_set = [d.lower() for d in academic if d]
    news_set = [d.lower() for d in news if d]
    neutral_set = [d.lower() for d in neutral_priority if d]
    blocked_set = [d.lower() for d in blocked if d]

    if intent == "social":
        # Подняли соцсети в топ — остальное как фон.
        return FilterPolicy(
            intent="social",
            priority=social_set,
            soft_priority=news_set + academic_set,
            blocked=blocked_set,  # ТОЛЬКО то, что задал пользователь явно
            min_score=min_score,
            domain_boost_threshold=domain_boost_threshold,
            top_k=top_k,
        )

    if intent == "academic":
        return FilterPolicy(
            intent="academic",
            priority=academic_set,
            soft_priority=news_set,
            blocked=blocked_set,
            min_score=min_score,
            domain_boost_threshold=domain_boost_threshold,
            top_k=top_k,
        )

    if intent == "news":
        return FilterPolicy(
            intent="news",
            priority=news_set,
            soft_priority=academic_set,
            blocked=blocked_set,
            min_score=min_score,
            domain_boost_threshold=domain_boost_threshold,
            top_k=top_k,
        )

    if intent == "all":
        # Пользователь сказал «ищи всё» — никаких приоритетов и блокировок.
        return FilterPolicy(
            intent="all",
            priority=[],
            soft_priority=[],
            blocked=[],
            min_score=0.0,
            domain_boost_threshold=999,  # отключаем доменный буст
            top_k=top_k,
        )

    # neutral — пользователь не указал явный источник.
    return FilterPolicy(
        intent="neutral",
        priority=neutral_set,
        soft_priority=[],
        blocked=blocked_set,
        min_score=min_score,
        domain_boost_threshold=domain_boost_threshold,
        top_k=top_k,
    )


# ─────────────────────────────────────────────────────────────────────
# Применение политики
# ─────────────────────────────────────────────────────────────────────
def matches_domain(host: str, patterns: Iterable[str]) -> bool:
    """Проверяет, матчится ли хост с любым из шаблонов.

    Поддерживает:
      • точное совпадение ``vk.com``;
      • суффиксное: ``foo.com`` матчит ``m.foo.com`` и ``foo.com``;
      • префиксы с ведущей точки ``.edu`` (по TLD-логике, матчит
        ``mit.edu``, ``harvard.edu`` и любой другой ``*.edu``).
    """
    if not host:
        return False
    host = host.lower()
    for p in patterns:
        if not p:
            continue
        p = p.lower()
        if p.startswith("."):
            # Режим TLD: ".edu" матчит "mit.edu"
            if host.endswith(p):
                return True
        else:
            # Суффиксный матч: "vk.com" матчит "vk.com" и "m.vk.com"
            if host == p or host.endswith("." + p):
                return True
    return False


def host_from_url(url: str) -> str:
    """Возвращает хост URL без ``www.``, в нижнем регистре."""
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.lower()


def should_drop(
    url: str,
    score: float,
    policy: FilterPolicy,
    seen_urls: set[str],
) -> tuple[bool, str]:
    """Решает, выбросить ли результат из выдачи. Возвращает (drop, reason)."""
    if not url or url in seen_urls:
        return True, "duplicate_or_empty"

    host = host_from_url(url)
    if policy.blocked and matches_domain(host, policy.blocked):
        return True, f"blocked_domain:{host}"

    if policy.min_score is not None and float(score or 0.0) < policy.min_score:
        return True, f"low_score:{score}"

    return False, ""


def rank_score(
    url: str,
    score: float,
    policy: FilterPolicy,
    domain_hits: dict[str, int],
) -> float:
    """Считает итоговый score после применения бустов политики.

    Приоритеты поднимают score, но **никогда не блокируют**. Если
    приоритеты в политике пусты (а так по умолчанию) — возвращается
    исходный score с лёгкой добавкой за частотность домена.
    """
    base = float(score or 0.0)
    host = host_from_url(url)

    # Главный приоритет
    if policy.priority and matches_domain(host, policy.priority):
        base += 100.0

    # Мягкий приоритет
    if policy.soft_priority and matches_domain(host, policy.soft_priority):
        base += 20.0

    # Доменный буст за повторяемость по разным запросам
    hits = domain_hits.get(host, 0)
    if (
        policy.domain_boost_threshold
        and policy.domain_boost_threshold < 999
        and hits >= policy.domain_boost_threshold
    ):
        base += 10.0 * hits

    return base
