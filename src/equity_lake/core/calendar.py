"""Exchange-specific trading calendar utilities.

Wraps ``exchange_calendars`` to provide market-aware trading-day checks.
Each supported market maps to an exchange MIC code with its holiday calendar
and timezone.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import structlog

from equity_lake.core.paths import PRICE_MARKETS, SHORT_TO_LONG

logger = structlog.get_logger()

# Each market maps to one or more exchange MIC codes with their holiday
# calendars and timezones. Both dicts are DERIVED from the price-market
# registry in ``core/paths.py`` (ADR-0010) and keyed by the canonical long
# form only — the short-key rows added as a transitional stopgap were
# deleted; alias acceptance goes through ``SHORT_TO_LONG`` at lookup time.
_MARKET_TO_EXCHANGE: dict[str, tuple[str, ...]] = {market: entry.exchanges for market, entry in PRICE_MARKETS.items()}

_MARKET_TZ: dict[str, str] = {market: entry.timezone for market, entry in PRICE_MARKETS.items()}

_calendar_cache: dict[str, xc.ExchangeCalendar] = {}


def _get_calendar(exchange: str) -> xc.ExchangeCalendar:
    if exchange not in _calendar_cache:
        _calendar_cache[exchange] = xc.get_calendar(exchange)
    return _calendar_cache[exchange]


def _canonical_key(market: str) -> str:
    """Canonical registry key for a market identifier (long or short form)."""
    return SHORT_TO_LONG.get(market, market)


def is_trading_day(market: str, d: date) -> bool:
    """True when ANY exchange mapped to the market holds a session on ``d``.

    Union semantics: answers "is this market active somewhere today?", which
    is what orchestration and freshness checks need. Gap detection instead
    uses the stricter intersection semantics of :func:`trading_days_between`.
    Non-price keys (enrichment tables, typos) keep the historical "no
    sessions" answer; :func:`equity_lake.core.dates.resolve_trading_date`
    raises on them before it starts looping.
    """
    return any(_get_calendar(exchange).is_session(d) for exchange in _MARKET_TO_EXCHANGE.get(_canonical_key(market), []))


def trading_days_between(market: str, start: date, end: date) -> list[date]:
    """Sessions between ``start`` and ``end`` common to ALL of the market's exchanges.

    Intersection semantics: a date is included only when every exchange mapped
    to the market holds a session. Gap detection uses this to build the set of
    dates expected for *every* ticker in a table; for the mixed-exchange
    ``hk_sg_equity`` market (XHKG + XSES), first-exchange-only or union
    semantics would flag false gaps for ``.SI`` tickers on Hong Kong-only
    sessions (e.g. Singapore National Day, when XHKG trades but XSES is
    closed) — and vice versa for ``.HK`` tickers. The trade-off: dates where
    only one exchange trades are never expected for any ticker, so a genuine
    single-exchange gap is not reported; gap-filling here prioritizes zero
    false positives. This intentionally differs from :func:`is_trading_day`.
    """
    exchanges: tuple[str, ...] = _MARKET_TO_EXCHANGE.get(_canonical_key(market), ())
    if not exchanges:
        return []
    session_sets = [set(_get_calendar(exchange).sessions_in_range(start, end)) for exchange in exchanges]
    common = set.intersection(*session_sets)
    return sorted(session.date() for session in common)


def count_trading_days(market: str, start: date, end: date) -> int:
    return len(trading_days_between(market, start, end))


def market_timezone(market: str) -> ZoneInfo:
    tz_name = _MARKET_TZ.get(_canonical_key(market), "UTC")
    return ZoneInfo(tz_name)


def market_now(market: str) -> date:
    from datetime import datetime

    tz = market_timezone(market)
    return datetime.now(tz).date()


__all__ = [
    "count_trading_days",
    "is_trading_day",
    "market_now",
    "market_timezone",
    "trading_days_between",
]
